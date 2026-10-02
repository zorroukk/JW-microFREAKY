"""Route-level battery for v0.4 wiring: boots plugin_api itself (mock MIDI, audio
off, temp sendlog/paths) and drives it over HTTP like the pane does.
Run: python test_routes.py"""
import json
import os
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mfvis-routes-"))
os.environ["MF_VIS_MOCK"] = "1"
os.environ["MF_VIS_AUDIO_OFF"] = "1"
os.environ["MF_MCP_SENDLOG"] = str(TMP / "sendlog.jsonl")
os.environ["MF_VIS_MACROS"] = str(TMP / "macros.json")
os.environ["MF_VIS_CLIPS"] = str(TMP / "clips.json")
os.environ["MF_VIS_LOOPS"] = str(TMP / "loops.json")
os.environ["MF_VIS_MATRIX"] = str(TMP / "matrix.json")
os.environ["MF_VIS_COMMANDS"] = str(TMP / "command-history.json")   # v0.6.1 F1: never touch real user data
os.environ["MF_VIS_SNAPS"] = str(TMP / "command-snapshots.json")

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402
import plugin_api                                # noqa: E402

app = FastAPI()
app.include_router(plugin_api.router)
C = TestClient(app)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# --- boot + handshake -----------------------------------------------------------
r = C.get("/state").json()
ok(r.get("api") == 4, f"api handshake: {r.get('api')}")
for key in ("macros", "clips", "clips_playing", "recording"):
    ok(key in r, f"/state missing {key}")
ok(r["macros"] == [] and r["clips"] == [] and r["recording"] is False, "non-empty cold banks")

# --- v0.4.2 fast analyzer feed: /spectrum serves the band payload on its own -----
sp = C.get("/spectrum").json()
ok(isinstance(sp.get("bands"), list) and len(sp["bands"]) == 64, f"/spectrum bands wrong: {type(sp.get('bands'))}")
ok(all(isinstance(b, (int, float)) and 0.0 <= b <= 1.0 for b in sp["bands"]), "/spectrum band outside 0..1")
ok(isinstance(sp.get("model"), bool) and "src" in sp and "err" in sp, "/spectrum missing model/src/err")

# --- v0.4.4: the scale is a real dBFS analyzer, not a loudness pin -----------
dn = plugin_api._dbfs_norm
ok(abs(dn(1.0) - 1.0) < 1e-9, "full-scale power -> 1.0")
ok(abs(dn(1e-4) - 50.0 / 90.0) < 1e-9, f"-40 dBFS -> 0.556, got {dn(1e-4)}")
ok(abs(dn(1e-3) - 60.0 / 90.0) < 1e-9, "-30 dBFS -> 0.667")
ok(dn(0.0) == 0.0 and dn(1e-40) == 0.0, "silence -> 0 (the old map's 88 dB floor is GONE)")
ok(dn(10.0) == 1.0, "clips at the top, never overshoots")
ok(dn(1e-4) > dn(1e-5) > dn(1e-6), "monotonic in power")

# --- macro lane through real routes ---------------------------------------------
r = C.post("/macro/save", json={"macro": {"id": "m1", "name": "brighten",
    "targets": [{"cc": 23, "base": 30, "depth": 70, "curve": "s"},
                {"cc": 83, "base": 60, "depth": -40, "curve": "lin"}]}}).json()
ok(r["ok"], f"good macro rejected: {r}")
r2 = C.post("/macro/save", json={"macro": {"id": "mX",
    "targets": [{"cc": 77, "base": 0, "depth": 0}]}}).json()
ok(not r2["ok"] and "allowlist" in r2["errors"][0], f"cc77 saved anyway: {r2}")
ok(len(C.get("/macros").json()["macros"]) == 1, "bad save mutated bank")

C.post("/live", json={"enabled": True})                  # mock LIVE
r = C.post("/macro", json={"id": "m1", "x": 127}).json()
ok(r["ok"] and {s["cc"] for s in r["sends"]} == {23, 83}, f"fan-out sends: {r}")
st = C.get("/state").json()
ok(st["cc"]["23"]["source"] == "macro:m1", f"provenance in state: {st['cc'].get('23')}")
ok(st["macros"][0]["x"] == 127, "fader position not in /state")
r = C.post("/macro", json={"id": "m1", "x": 127}).json()
ok(r["ok"] and r["sends"] == [], f"coalescer restuck at same x: {r}")
r = C.post("/macro", json={"id": "ghost", "x": 5}).json()
ok(not r["ok"], "ghost macro moved")
r = C.post("/macro", json={"id": "m1", "x": "abc"}).json()
ok(not r["ok"], "non-int x accepted")

# --- recorder over real traffic ---------------------------------------------------
r = C.post("/clip/record", json={"action": "arm", "name": "route-ride"}).json()
ok(r["ok"] and C.get("/state").json()["recording"] is True, "arm failed")
for v in (50, 60, 61, 60, 55):
    C.post("/cc", json={"cc": 23, "value": v})
    time.sleep(0.03)
r = C.post("/clip/record", json={"action": "stop"}).json()
ok(r["ok"], f"stop failed: {r}")
clip = C.get("/clips").json()["clips"][0]
ok(clip["schema"] == "mfclip/1" and clip["name"] == "route-ride", f"clip meta: {clip.get('name')}")
c23 = [p["v"] for p in clip["tracks"][0]["points"]]
ok(c23 == [50, 60, 61, 60, 55] or (len(c23) >= 4 and c23[0] == 50 and c23[-1] == 55),
   f"rides not captured in order: {c23}")
ok(not C.post("/clip/record", json={"action": "stop"}).json()["ok"], "double-stop made a clip")
r = C.post("/clip/record", json={"action": "arm"}).json()
r = C.post("/clip/record", json={"action": "stop"}).json()
ok(not r["ok"] and "discarded" in r["error"], f"empty recording saved: {r}")

# --- transport over routes ----------------------------------------------------------
cid = clip["id"]
r = C.post("/clip/play", json={"id": cid, "loop": "once", "stretch": 4}).json()
ok(r["ok"], f"play refused: {r}")
ok(len(C.post("/clip/play", json={"id": cid}).json()) and not C.post("/clip/play", json={"id": cid}).json()["ok"],
   "double transport allowed over routes")
ok(C.get("/state").json()["clips_playing"], "playing not visible in /state")
time.sleep(0.15)
r = C.post("/panic", json={}).json()
ok(r["ok"] and r.get("clips_stopped", 0) >= 0 and C.get("/state").json()["clips_playing"] == [],
   f"panic did not clear transports: {r}")
r = C.post("/clip/play", json={"id": cid, "loop": "yoyo"}).json()
ok(not r["ok"], "bogus loop mode accepted over route")
r = C.post("/clip/play", json={"id": cid, "stretch": 99}).json()
ok(not r["ok"], "stretch 99 accepted over route")

# --- LIVE-off honesty: nothing fakes sends ------------------------------------------
C.post("/live", json={"enabled": False})
r = C.post("/clip/play", json={"id": cid}).json()
ok(not r["ok"] and "LIVE" in r["error"], f"clip played without LIVE: {r}")
r = C.post("/macro", json={"id": "m1", "x": 10}).json()
ok(r["ok"] and r["sends"] == [], f"sends claimed while LIVE off: {r}")
ok(C.get("/state").json()["cc"]["23"]["source"] == "macro:m1", "ui echo missing while not live")

# --- cleanup routes ----------------------------------------------------------------
ok(C.post("/macro/delete", json={"id": "m1"}).json()["ok"], "macro delete failed")
ok(C.post("/clip/delete", json={"id": cid}).json()["ok"], "clip delete failed")
ok(plugin_api.MACROS_PATH.exists() and plugin_api.CLIPS_PATH.exists(), "bank files never written")

# --- v0.5 LOOP STUDIO through real routes -------------------------------------
r = C.post("/loop/record", json={"action": "arm", "name": "route take", "bpm": 120, "spb": 4}).json()
ok(r["ok"] and r["armed"], f"loop arm: {r}")
ok((C.get("/state").json().get("loop_rec") or {}).get("armed") is True, "loop_rec shows the armed take")
ok(not C.post("/loop/record", json={"action": "arm"}).json()["ok"], "double-arm refused")
r2 = C.post("/loop/record", json={"action": "stop"}).json()
ok(not r2["ok"] and "discarded" in r2["error"], f"empty take discarded: {r2}")
C.post("/loop/record", json={"action": "arm", "name": "route take", "bpm": 120})
C.post("/note", json={"note": 60, "velocity": 96})
time.sleep(0.05)
C.post("/note", json={"note": 60, "velocity": 0})
C.post("/note", json={"note": 63, "velocity": 90})       # on only -> rings out at stop
r3 = C.post("/loop/record", json={"action": "stop"}).json()
ok(r3["ok"] and r3["loop"]["notes"] == 2, f"both notes captured: {r3}")
lid = r3["loop"]["id"]
st = C.get("/state").json()
ok(any(l["id"] == lid for l in st["loops"]), "loop visible in /state")
ok(st.get("loop_rec") is None, "loop_rec cleared after save")
ok(st["loops"][0]["bpm"] == 120.0 and st["loops"][0]["bars"] >= 1, "summary shape")
C.post("/live", json={"enabled": True})
r4 = C.post("/loop/play", json={"id": lid, "loop": "once"}).json()
ok(r4["ok"], f"once play accepted: {r4}")
time.sleep(2.0)
ok(C.get("/loops").json()["playing"] == [], "once transport ended + cleaned itself")
C.post("/live", json={"enabled": False})
r5 = C.post("/loop/export", json={"id": lid}).json()
ok(r5["ok"] and r5.get("file", "").endswith(".mid") and Path(r5["path"]).exists() and Path(r5["path"]).read_bytes()[:4] == b"MThd",
   f"export produced MThd: {r5}")
r6 = C.post("/loop/double", json={"id": lid}).json()
ok(r6["ok"] and r6["loop"]["bars"] == r3["loop"]["bars"] * 2 and
   r6["loop"]["notes"] == r3["loop"]["notes"] * 2, f"double: {r6}")
r7 = C.post("/loop/overdub", json={"action": "arm", "id": lid}).json()
ok(r7["ok"] and r7["armed"], f"overdub arm: {r7}")
C.post("/note", json={"note": 70, "velocity": 88})
time.sleep(0.05)
C.post("/note", json={"note": 70, "velocity": 0})
r8 = C.post("/loop/overdub", json={"action": "stop"}).json()
ok(r8["ok"] and r8["loop"]["notes"] > r6["loop"]["notes"], f"overdub merged: {r8}")
r11 = C.post("/loop/halve", json={"id": lid}).json()
ok(r11["ok"] and r11["loop"]["bars"] == 1 and r11["loop"]["notes"] <= r8["loop"]["notes"],
   f"halve truncates: {r11}")
r12 = C.post("/loop/length", json={"id": lid, "bars": 4}).json()
ok(r12["ok"] and r12["loop"]["bars"] == 4 and r12["loop"]["notes"] == r11["loop"]["notes"],
   f"grow = silent tail, never invented notes: {r12}")
ok(not C.post("/loop/length", json={"id": lid, "bars": 99}).json()["ok"], "bar cap enforced")
ok(C.post("/loop/delete", json={"id": lid}).json()["ok"], "delete")
ok(not any(l["id"] == lid for l in C.get("/state").json()["loops"]), "gone from /state")
# pure-CC clip export path: save a small mfclip and push it through /clip/export
cs = C.post("/clip/save", json={"clip": {"schema": "mfclip/1", "id": "cx9", "name": "lane clip",
      "length_ms": 500.0, "tracks": [{"cc": 23, "points": [{"t_ms": 0.0, "v": 10, "curve": "lin"},
                                                            {"t_ms": 400.0, "v": 90, "curve": "lin"}]}]}}).json()
ok(cs["ok"], f"mfclip save: {cs}")
r9 = C.post("/clip/export", json={"id": "cx9"}).json()
ok(r9["ok"] and Path(r9["path"]).read_bytes()[:4] == b"MThd", f"clip export: {r9}")
# /reveal: hijack startfile - the battery must never pop a real Explorer window
import os as _os
_opened = []
_real_startfile = getattr(_os, "startfile", None)
_os.startfile = lambda p: _opened.append(str(p))
rv = C.post("/reveal", json={}).json()
ok(rv["ok"] and Path(rv["path"]).resolve() == plugin_api.LOOPS_PATH.parent.resolve() and _opened == [rv["path"]], f"/reveal data: {rv}")
rv2 = C.post("/reveal", json={"where": "midis"}).json()
ok(rv2["ok"] and Path(rv2["path"]).name == "midis" and len(_opened) == 2, f"/reveal midis: {rv2}")
if _real_startfile:
    _os.startfile = _real_startfile

# panic response must report loop transports stopped
r10 = C.post("/panic", json={}).json()
ok(r10["ok"] and "loops_stopped" in r10, f"panic reports loops: {r10}")

# --- v0.6 THEORY / CHORD LAB / DIAGNOSTICS through real routes ------------------
ok((C.get("/state").json().get("theory") == "mftheory/1"), "/state missing theory tag")
for key in ("chord", "diag"):
    ok(key in C.get("/state").json(), f"/state missing {key}")
th = C.get("/theory").json()
ok(th["ok"] and th["schema"] == "mftheory/1" and len(th["scales"]) == 18, f"/theory payload: {th.get('schema')}")
ok(len(th["qualities"]) > 25 and "drop2" in th["voicings"] and th["paraphony_voices"] == 4, "/theory registry shape")
ok(th["scales"][0]["name"] == "OFF" and th["progressions"], "/theory scale order/progressions")

tc = C.post("/theory/chord", json={"root": 0, "quality": "maj7", "voicing": "close", "octave": 4}).json()
ok(tc["ok"] and tc["notes"] == [60, 64, 67, 71] and tc["label"] == "Cmaj7" and tc["names"][0] == "C4",
   f"/theory/chord: {tc}")
ok(C.post("/theory/chord", json={"root": 0, "quality": "nope"}).json()["ok"] is False, "bad quality accepted")
ok(C.post("/theory/chord", json={"root": 11, "quality": "13", "octave": 8}).json()["ok"] is False,
   "out-of-range voicing accepted")
pb = C.post("/theory/progression", json={"scale": "MAJOR", "root": 2, "name": "ii-V-I (jazz)"}).json()
ok(pb["ok"] and len(pb["chords"]) == 3 and pb["chords"][0]["roman"] == "ii7", f"progression: {pb}")
ok(C.post("/theory/progression", json={"scale": "MAJ PENT", "name": "I-V-vi-IV (pop)"}).json()["ok"] is False,
   "a 5-note scale spelled a vi chord")

# chord lab: LIVE off is honest, nothing claims a send, RELEASE lifts the lot
C.post("/live", json={"enabled": False})
cp = C.post("/chord/play", json={"root": 0, "quality": "major", "action": "hold"}).json()
ok(cp["ok"] and cp["notes"] == [60, 64, 67] and cp["sent"] == [] and cp["virtual"] == [60, 64, 67],
   f"LIVE-off chord claims sends: {cp}")
st = C.get("/state").json()
ok(st["chord"]["held"] == [60, 64, 67] and st["chord"]["last"]["label"] == "C", f"/state chord: {st['chord']}")
ok(len([n for n in st["active_notes"] if n in (60, 64, 67)]) == 3, "held chord not lit in active_notes")
cr = C.post("/chord/release", json={}).json()
ok(cr["ok"] and cr["released"] == 3 and C.get("/state").json()["chord"]["held"] == [], f"release: {cr}")
ok(C.post("/chord/play", json={"notes": list(range(60, 69))}).json()["ok"] is False, "9-note chord accepted")
ok(C.post("/chord/play", json={"notes": [999]}).json()["ok"] is False, "out-of-range note accepted")
ok(C.post("/chord/play", json={"notes": []}).json()["ok"] is False, "empty chord accepted")
ok(C.post("/chord/play", json={"notes": [60], "action": "sideways"}).json()["ok"] is False, "bad action accepted")
# a 5-note voicing plays AND says why it is over the Freak's 4 voices
c5 = C.post("/chord/play", json={"root": 0, "quality": "9"}).json()
ok(c5["ok"] and len(c5["notes"]) == 5 and c5["paraphony"] and "4 paraphonic" in c5["paraphony"],
   f"paraphony warning missing: {c5}")
C.post("/chord/release", json={})
# one chord at a time: a second play lifts the first
C.post("/chord/play", json={"root": 0, "quality": "major"})
ok(C.get("/state").json()["chord"]["held"] == [60, 64, 67], "first chord not held")
C.post("/chord/play", json={"root": 2, "quality": "minor"})
st = C.get("/state").json()
ok(st["chord"]["held"] == [62, 65, 69] and 60 not in st["active_notes"], f"second chord did not replace: {st['chord']}")
# moment chords lift themselves, and PANIC lifts a held chord NOW
C.post("/chord/play", json={"root": 0, "quality": "major", "action": "moment", "duration_ms": 120})
time.sleep(0.5)
ok(C.get("/state").json()["chord"]["held"] == [], "moment chord never expired")
C.post("/chord/play", json={"root": 0, "quality": "major"})
pan = C.post("/panic", json={}).json()
ok(pan["ok"] and pan["chords_released"] == 3 and C.get("/state").json()["chord"]["held"] == [],
   f"panic did not lift the chord: {pan}")

# matrix tutor: read-only, cited, provenance-labeled - and no write path exists
tu = C.get("/tutor").json()
ok(tu["ok"] and tu["schema"] == "mftutor/1" and len(tu["sources"]) == 5 and len(tu["destinations"]) >= 4,
   f"/tutor payload: {tu.get('schema')}")
ok(len(tu["recipes"]) >= 8 and all(r["provenance"] in ("official", "observed", "inferred", "unknown")
                                   for r in tu["recipes"]), "tutor recipes missing provenance labels")
ok(all(s["cite"] for s in tu["sources"] + tu["destinations"]), "tutor rows without citations")
ok("no MIDI path programs the hardware matrix" in tu["law"], "tutor honesty law missing")
ok(not any("/tutor" in r.path for r in app.routes if "POST" in getattr(r, "methods", set())),
   "a WRITE route appeared under /tutor - the tutor must stay read-only")

# diagnostics harness: mock-only, allowlist-refuse-closed, always stoppable
dg = C.get("/diag").json()
ok(dg["ok"] and dg["running"] is False and isinstance(dg["sendlog_rows"], int), f"/diag cold: {dg}")
ok(C.post("/stress/start", json={"pattern": "nope"}).json()["ok"] is False, "bogus pattern accepted")
ok(C.post("/stress/start", json={"pattern": "cc_sweep", "cc": 77}).json()["ok"] is False,
   "allowlist bypass in the harness")
ok(C.post("/stress/start", json={"pattern": "cc_sweep", "duration_s": 999}).json()["ok"] is False,
   "unbounded run accepted")
ok(C.post("/stress/stop", json={}).json()["ok"] is False, "stop with no run claimed ok")
s1 = C.post("/stress/start", json={"pattern": "cc_sweep", "duration_s": 1.2, "rate_hz": 60, "cc": 23}).json()
ok(s1["ok"] and s1["run"]["pattern"] == "cc_sweep", f"stress start: {s1}")
ok(C.post("/stress/start", json={"pattern": "note_burst"}).json()["ok"] is False, "double run accepted")
time.sleep(0.3)
mid = C.get("/diag").json()
ok(mid["running"] and mid["counters"]["attempted"] > 0 and mid["rate_hz_now"] > 5,
   f"running counters: {mid['counters']} rate={mid['rate_hz_now']}")
time.sleep(1.2)
s2 = C.post("/stress/stop", json={}).json()
ok(s2["ok"] and s2["schema"] == "mfstress/1" and s2["attempted"] > 10 and s2["msg_per_s"] > 1,
   f"run summary: {s2}")
ok(s2["run"]["state"] == "stopped" and isinstance(s2["sendlog_rows"], int),
   f"summary honesty: {s2}")   # -1 = no sendlog file at all (mock battery): honest, not fake
ok(C.get("/diag").json()["running"] is False, "run still marked running after stop")
# note burst: stuck-note smoke must come back balanced, and PANIC kills a run
nb = C.post("/stress/start", json={"pattern": "note_burst", "duration_s": 0.8, "rate_hz": 40, "note": 60}).json()
ok(nb["ok"], f"note burst start: {nb}")
time.sleep(1.1)
nb_s = C.post("/stress/stop", json={}).json()
ok(nb_s["stuck_note_check"] is True and nb_s["notes_on"] == nb_s["notes_off"],
   f"stuck-note smoke unbalanced: {nb_s}")
C.post("/stress/start", json={"pattern": "cc_storm", "duration_s": 5, "rate_hz": 100, "cc": 83})
time.sleep(0.3)
pan2 = C.post("/panic", json={}).json()
ok(pan2["stress_stopped"] and C.get("/diag").json()["running"] is False, f"panic left a run going: {pan2}")
ex = C.get("/stress/export").json()
ok(ex["ok"] and "run_id,pattern," in ex["csv"]
   and Path(ex["paths"]["json"]).exists() and Path(ex["paths"]["csv"]).exists()
   and Path(ex["paths"]["csv"]).read_text(encoding="utf-8").startswith("run_id,pattern,"),
   f"export: {ex.get('paths')}")
ok(len(ex["history"]) >= 3 and ex["history"][-1]["run"]["id"] == nb_s["run"]["id"] or len(ex["history"]) >= 3,
   "export history shape")
ok(Path(ex["paths"]["json"]).read_text(encoding="utf-8").find("mfstress/1") > 0,
   "export json missing schema tag")

# ================= v0.6.1 F1: registry + shadow + command routes =================
rg = C.get("/registry").json()
ok(rg.get("ok") and len(rg["params"]) == 21, f"/registry param count: {len(rg.get('params', {}))}")
ok(sum(len(v) for v in rg["sections"].values()) == 21, "/registry sections do not cover 21")
cut = rg["params"]["cutoff"]
ok(cut["cc"] == 23 and cut["display"].get("basis") in ("pane-law", "profile", "none")
   and "aliases" in cut, f"/registry cutoff shape: {cut}")

st0 = C.get("/state").json()
ok("shadow" in st0 and "commands" in st0, "/state missing F1 shadow/commands payload")

C.post("/live", json={"enabled": False})
# earlier LIVE lanes already grounded some CCs; start from a known-stale shadow
plugin_api.SHADOW.invalidate("panel_edit_bulk")
r = C.post("/command", json={"author": "pane", "atomic": True,
                             "changes": [{"param": "cutoff", "value": 64}]}).json()
ok(r["ok"] is False and r["command"]["outcomes"][0]["why"] == "refused",
   f"LIVE-off command must refuse, never fake: {r}")
sh = C.get("/shadow").json()["entries"]
ok(sh.get("23", {}).get("provenance") == "unknown",
   f"LIVE-off command polluted the shadow: {sh.get('23')}")
r = C.post("/command", json={"changes": [{"param": "echo", "value": 4}]}).json()
ok(r["ok"] is False and "21-CC surface" in r["error"], f"off-surface must refuse-closed: {r}")
r = C.post("/command", json={"changes": [{"cc": 77, "value": 4}]}).json()
ok(r["ok"] is False, f"off-surface cc must refuse-closed: {r}")

C.post("/live", json={"enabled": True})
# atomic=True keeps these from coalescing into each other - the merge law is
# pinned in test_commands.py; here we need distinct undoable steps.
r = C.post("/command", json={"author": "pane", "atomic": True,
                             "changes": [{"param": "cutoff", "value": 200}]}).json()
ok(r["ok"] is True and r["command"]["changes"][0]["value"] == 127,
   f"registry clamp to panel max failed: {r}")
sh = C.get("/shadow").json()["entries"]
ok(sh.get("23", {}).get("provenance") == "commanded" and sh["23"]["value"] == 127,
   f"/command must ground shadow: {sh.get('23')}")
C.post("/command", json={"author": "pane", "atomic": True,
                         "changes": [{"param": "cutoff", "value": 60}]}).json()
u = C.post("/command/undo", json={}).json()
ok(u["ok"] and u["command"]["outcomes"][0]["why"] == "restored-pre-value"
   and u["command"]["changes"][0]["value"] == 127, f"undo inverse replay: {u}")
ok("replay" in u.get("note", ""), f"undo must label itself replay, never device rewind: {u}")
sh = C.get("/shadow").json()["entries"]
ok(sh["23"]["value"] == 127 and sh["23"]["provenance"] == "commanded", f"undo shadow state: {sh['23']}")
rd = C.post("/command/redo", json={}).json()
ok(rd["ok"] and rd["command"]["changes"][0]["value"] == 60, f"redo replay forward: {rd}")
C.post("/command", json={"author": "macro:test", "changes": [{"param": "cutoff", "value": 30}]}).json()
hist = C.get("/commands/history").json()
last2 = hist["entries"][-2:]
ok(len(last2) == 2 and last2[0]["author"] != last2[1]["author"],
   f"authors must never coalesce: {[e['author'] for e in last2]}")
ok(hist["load_error"] is None, f"fresh temp history must not fake a load error: {hist['load_error']}")
snap = C.post("/command/snapshot", json={"name": "s1"}).json()
ok(snap["ok"] and snap["snapshot"]["params"], f"snapshot: {snap}")
rs = C.post("/command/restore", json={"name": "s1"}).json()
ok(rs["ok"] and "replay" in rs["note"] and "restore-from-slot" == rs["command"]["action_id"],
   f"restore must claim 'replay slot' only: {rs}")
ok(C.post("/command/restore", json={"name": "nope"}).json()["ok"] is False,
   "restore of unknown slot must refuse")
hb = C.get("/commands/history").json()["entries"][-1]
_hf = Path(os.environ["MF_VIS_COMMANDS"]).read_text(encoding="utf-8")
ok(_hf.strip().startswith("[") and str(hb["id"]) in _hf,
   "command history must persist to user data")
C.post("/live", json={"enabled": False})   # leave LIVE disarmed like the battery found it

if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL OK — routes battery passed (api:4 handshake, fan-out provenance, coalescer, recorder over live traffic, "
      "panic-clears-transport + panic-lifts-chords/stress, LIVE-off honesty, bank persistence, "
      "v0.6 theory/chord-lab/diagnostics over real HTTP incl. stuck-note smoke + mfstress/1 export, "
      "v0.6.1 F1 registry/shadow/command/undo/redo/snapshot incl. LIVE-off non-pollution + refuse-closed surface)")
