"""Behavior battery for modengine (macro bank / recorder / player / banks).
Fake sink + fake event stream — zero hardware, zero fastapi. Run: python test_modengine.py"""
import tempfile, time
from pathlib import Path

from modengine import (MacroBank, ClipBank, ClipRecorder, ClipPlayer,
                       validate_macro, validate_clip, MAX_MACROS)

ALLOW = {2, 5, 9, 10, 12, 13, 23, 24, 26, 28, 29, 64, 83, 91, 92, 93, 94, 102, 103, 105, 106}
fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

TMP = Path(tempfile.mkdtemp(prefix="mfvis-test-"))
SENT = []          # (cc, value, source)
REFUSE = {"set": set()}

def sink(cc, value, source):
    if cc in REFUSE["set"]:
        return False
    SENT.append((cc, value, source))
    return True

def bank_macro(n=0):
    return MacroBank(TMP / f"macros{n}.json", ALLOW, sink)

def bank_clips(n=0):
    return ClipBank(TMP / f"clips{n}.json", ALLOW)

# --- validation refuse-closed --------------------------------------------------
ok(validate_macro({"id": "", "targets": []}, ALLOW), "empty macro accepted")
ok(validate_macro({"id": "m1", "targets": [{"cc": 77, "base": 0, "depth": 10}]}, ALLOW), "cc77 slipped")
ok(validate_macro({"id": "m1", "targets": [{"cc": 23, "base": -5, "depth": 0}]}, ALLOW), "base -5 accepted")
ok(validate_macro({"id": "m1", "targets": [{"cc": 23, "base": 0, "depth": 999}]}, ALLOW), "depth 999 accepted")
ok(validate_macro({"id": "m1", "targets": [{"cc": 23, "base": 0, "depth": 10, "curve": "bezeir"}]}, ALLOW), "bogus curve accepted")
ok(validate_macro({"id": "m1", "targets": [{"cc": 23, "base": 0, "depth": 0}, {"cc": 23, "base": 1, "depth": 0}]}, ALLOW), "dupe cc accepted")
ok(not validate_macro({"id": "m1", "targets": [{"cc": 106, "base": 64, "depth": -63, "curve": "snap12"}]}, ALLOW), "legit macro rejected")
ok(validate_clip({"schema": "mfpat/9", "id": "c", "length_ms": 200, "tracks": [{"cc": 23, "points": [{"t_ms": 0, "v": 1}, {"t_ms": 100, "v": 2}]}]}, ALLOW), "bad schema accepted")
ok(validate_clip({"schema": "mfclip/1", "id": "c", "length_ms": 50, "tracks": [{"cc": 23, "points": [{"t_ms": 0, "v": 1}, {"t_ms": 100, "v": 2}]}]}, ALLOW), "50ms clip accepted")
ok(validate_clip({"schema": "mfclip/1", "id": "c", "length_ms": 200, "tracks": [{"cc": 23, "points": [{"t_ms": 100, "v": 1}, {"t_ms": 0, "v": 2}]}]}, ALLOW), "descending points accepted")
ok(validate_clip({"schema": "mfclip/1", "id": "c", "length_ms": 200, "tracks": [{"cc": 23, "points": [{"t_ms": 0, "v": 300}, {"t_ms": 100, "v": 2}]}]}, ALLOW), "value 300 accepted")

# --- MacroBank -----------------------------------------------------------------
mb = bank_macro(1)
errs = mb.save({"id": "m1", "name": "brighten", "x": 0,
                "targets": [{"cc": 23, "base": 40, "depth": 60, "curve": "lin"},
                            {"cc": 83, "base": 20, "depth": -20, "curve": "s"}]})
ok(not errs, f"save rejected good macro: {errs}")
errs2 = mb.save({"id": "m2", "targets": [{"cc": 77, "base": 0, "depth": 0}]})
ok(errs2 and len(mb.macros) == 1, "bad macro mutated the bank")
SENT.clear()
r = mb.move("m1", 0)
ok(r["ok"] and [(c, v) for c, v, _ in SENT] == [(23, 40), (83, 20)], f"x=0 fanout wrong: {SENT}")
SENT.clear()
r = mb.move("m1", 0)
ok(SENT == [], f"coalescer restuck identical values: {SENT}")
SENT.clear()
r = mb.move("m1", 127)
ok(sorted((c, v) for c, v, _ in SENT) == [(23, 100), (83, 0)], f"x=127 fanout wrong: {SENT}")
ok(all(s == "macro:m1" for _, _, s in SENT), "provenance tag missing")
SENT.clear()
mb.reset_hardware_echo()
mb.move("m1", 127)
ok(len(SENT) == 2, "echo reset did not force restick")
# persistence round-trip
mb2 = bank_macro(1)
ok(mb2.get("m1") and mb2.get("m1")["x"] == 127, "macro x not persisted")
# bank cap
for i in range(3, MAX_MACROS + 3):
    mb2.save({"id": f"x{i}", "targets": [{"cc": 23, "base": 0, "depth": 0}]})
ok(len(mb2.macros) == MAX_MACROS, f"bank exceeded {MAX_MACROS}")
ok(mb2.delete("m1") and not mb2.delete("m1"), "delete idempotence wrong")
ok(mb2.move("nope", 5)["ok"] is False, "moved a ghost macro")
ok(mb2.move("x3", 200)["ok"] is False, "x=200 accepted")

# --- ClipRecorder ---------------------------------------------------------------
EVENTS = []   # dicts with seq implicit by index
def events_since(seq0):
    return EVENTS[seq0:]
rec = ClipRecorder(events_since, lambda: len(EVENTS))
for base_v in (): pass
mbase = len(EVENTS)
rec.arm("ride")
t_anchor = time.time()
for i, v in enumerate([30, 30, 31, 33, 33, 33, 40, 40, 55]):
    time.sleep(0.001)
    EVENTS.append({"kind": "cc", "cc": 23, "value": v, "ts": t_anchor + i * 0.1, "seq": mbase + i + 1})
EVENTS.append({"kind": "note", "note": 60, "velocity": 90, "ts": t_anchor + 0.5})  # must be ignored
clip = rec.stop(tail_ms=200.0)
ok(clip is not None, "recorder returned nothing")
tr = clip["tracks"][0]
vals = [p["v"] for p in tr["points"]]
ok(vals == [30, 31, 33, 40, 55], f"dedupe/anchor wrong: {vals}")
ok(tr["points"][0]["t_ms"] == 0.0, "no t=0 anchor")
ok(abs(tr["points"][1]["t_ms"] - 200) < 30, f"timing skewed: {tr['points'][1]['t_ms']}")
# delayed first move must hold its pre-value from t=0 (anchor point prepended)
EVD = []
rec_d = ClipRecorder(lambda s: EVD[s:], lambda: len(EVD))
rec_d.arm()
t0d = time.time()
EVD.append({"kind": "cc", "cc": 13, "value": 44, "ts": t0d + 0.35})
EVD.append({"kind": "cc", "cc": 13, "value": 90, "ts": t0d + 0.6})
cd = rec_d.stop(tail_ms=100.0)
td = cd["tracks"][0]["points"]
ok(td[0]["t_ms"] == 0.0 and td[0]["v"] == 44, f"delayed start not anchored at pre-value: {td[0]}")
ok(abs(td[1]["t_ms"] - 350) < 40 and td[1]["v"] == 44, f"delayed first move misplaced: {td[1]}")
ok(validate_clip(cd, ALLOW) == [], "delayed-start clip failed validator")
ok(clip["schema"] == "mfclip/1" and abs(clip["length_ms"] - (800 + 200)) < 30, f"length wrong: {clip['length_ms']}")
ok(len(clip["tracks"]) == 1, "note event leaked into tracks")
ok(validate_clip(clip, ALLOW) == [], "recorded clip fails its own validator")
ok(rec.stop() is None, "un-armed stop produced a clip")
rec2 = ClipRecorder(lambda s: [], lambda: 0); rec2.arm("empty")
ok(rec2.stop() is None, "empty recording produced a clip")

# --- ClipPlayer ------------------------------------------------------------------
cb = bank_clips(1)
CLIP = {"schema": "mfclip/1", "id": "c1", "name": "swell", "length_ms": 200.0,
        "tracks": [{"cc": 23, "points": [{"t_ms": 0, "v": 0, "curve": "lin"},
                                          {"t_ms": 200, "v": 100, "curve": "lin"}]},
                   {"cc": 83, "points": [{"t_ms": 0, "v": 50, "curve": "lin"},
                                          {"t_ms": 200, "v": 50, "curve": "lin"}]}]}
ok(cb.save(CLIP) == [], "good clip rejected")
GEN = {"g": 0}
player = ClipPlayer(cb, sink, lambda: GEN["g"], lambda *a: None)
SENT.clear()
r = player.play("c1", loop="once", stretch=0.25)   # 50 ms run
ok(r["ok"], f"play refused: {r}")
time.sleep(0.6)
ok(player.state() == [], "once-mode never terminated")
c23 = [v for c, v, s in SENT if c == 23]
ok(c23 and c23[0] >= 0 and c23[-1] >= 90, f"once sweep wrong ends: {c23[:2]}...{c23[-2:]}")
flat83 = [v for c, v, s in SENT if c == 83]
ok(len(flat83) <= 1, f"flat track resticked {len(flat83)} times (coalescer failed)")
ok(all(s == "clip:c1" for _, _, s in SENT), "clip provenance tag missing")
# bad args
ok(not player.play("ghost")["ok"], "ghost clip played")
ok(not player.play("c1", loop="yoyo")["ok"], "bogus loop mode accepted")
ok(not player.play("c1", stretch=20)["ok"], "stretch 20 accepted")
# panic gen-void
SENT.clear(); GEN["g"] += 1
r = player.play("c1", loop="loop")
ok(r["ok"], "loop play refused")
time.sleep(0.05)
GEN["g"] += 1                     # /panic equivalent
time.sleep(0.25)
ok(player.state() == [], "gen bump did not void running clip")
# double-play guard + stop + mute
GEN["g"] += 0
r1 = player.play("c1", loop="loop"); r2 = player.play("c1", loop="loop")
ok(r1["ok"] and not r2["ok"], "double transport allowed")
ok(player.stop("c1") and not player.stop("c1"), "stop idempotence wrong")
SENT.clear()
player.play("c1", loop="once", stretch=0.25, mute={23})
time.sleep(0.5)
ok([1 for c, v, s in SENT if c == 23] == [], "mute did not mute")
# send refusal stops transport
REFUSE["set"] = {23}
SENT.clear()
player.play("c1", loop="loop", stretch=4)
time.sleep(0.4)
ok(player.state() == [], "refused send did not stop transport")
REFUSE["set"] = set()
n = player.play("c1", loop="loop", stretch=4)
time.sleep(0.1)
ok(player.stop_all() == 1 and player.state() == [], "stop_all wrong")

# --- ClipBank persistence ---------------------------------------------------------
cb2 = bank_clips(1)
ok([c["id"] for c in cb2.summaries()] == ["c1"], "clip bank did not persist")
ok(cb2.delete("c1") and cb2.summaries() == [], "clip delete failed")

if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL OK — modengine battery passed (validation refuse-closed, fan-out, coalescer, persistence, recorder, transport, panic-void, mute, refusal-stop)")
