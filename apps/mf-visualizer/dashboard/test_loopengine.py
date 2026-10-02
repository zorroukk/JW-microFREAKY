"""v0.5 loop-engine behavior battery (fake event stream + fake sinks, zero
hardware). The TEETER LAW gets a dedicated red team: every way a transport can
die (stop, once-end, panic-gen bump, LIVE drop, mid-flight exception) MUST
leave every note it took down released at the sink."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from modengine import NoteRecorder, LoopBank, LoopPlayer  # noqa: E402

fails = []
def ok(c, m):
    if not c: fails.append(m)

TMP = Path(tempfile.gettempdir()) / f"mfvis-loops-{int(time.time())}"
TMP.mkdir(exist_ok=True)

# ---------------- recorder ----------------
stream = []          # dicts with kind/note/velocity/ts/seq
seq_ctr = [0]
def ev(note, vel, dt):
    seq_ctr[0] += 1
    stream.append({"kind": "note", "note": note, "velocity": vel, "ts": t0 + dt, "seq": seq_ctr[0]})
def cc_ev(cc, v, dt):
    seq_ctr[0] += 1
    stream.append({"kind": "cc", "cc": cc, "value": v, "ts": t0 + dt, "seq": seq_ctr[0]})

rec = NoteRecorder(lambda s0: [e for e in stream if e["seq"] > s0], lambda: seq_ctr[0])
t0 = time.time()
rec.arm(name="takes", bpm=120.0, spb=4)         # arm FIRST: the recorder consumes seq > arm point (real-world order)
ev(60, 96, 0.00); ev(60, 0, 0.25)             # clean pair
ev(63, 100, 0.20); ev(63, 0, 0.60)            # overlapping pitch
ev(67, 90, 0.40); ev(67, 91, 0.70); ev(67, 0, 0.95)   # retrigger = first one closed at 0.70
cc_ev(23, 44, 0.30)                            # must be ignored by the NOTE recorder
ev(72, 88, 1.10)                               # still held at stop -> rings out
time.sleep(0.05); rec.poll()
pat = rec.stop()
ok(pat is not None and pat["schema"] == "mfpat/1", "pattern schema")
ok(pat["id"].startswith("L") and pat["bars"] >= 1, f"id/bars {pat.get('id')} {pat.get('bars')}")
ns = {(n["note"], round(n["t_ms"])): n for n in pat["notes"]}
ok(60 in [k[0] for k in ns], "60 recorded")
durs = {n["note"]: n["dur"] for n in pat["notes"]}
ok(len(pat["notes"]) >= 5, f"5+ events (pairs+retrigger+ringout): {len(pat['notes'])}")
ok(durs.get(72, 0) >= 200, "still-held note rings out with real duration")
step = 60000.0 / 120.0 / 4
ok(all(abs(n["t_ms"] / step - round(n["t_ms"] / step)) < 1e-6 for n in pat["notes"]), "all ons on grid")
ok(all(n["dur"] >= step for n in pat["notes"]), "durations >= one step")

# empty take = None
rec2 = NoteRecorder(lambda s0: [], lambda: 0)
rec2.arm(bpm=120); ok(rec2.stop() is None, "empty armed stop discards (None)")

# ---------------- bank + overdub + double ----------------
bank = LoopBank(TMP / "loops.json")
errs = bank.save(pat); ok(not errs, f"save: {errs}")
bad = dict(pat); bad["notes"] = [{"t_ms": 0, "dur": 0, "note": 999, "vel": 96}]
ok(bank.save(bad), "invalid pattern must NOT save (refuse-closed)")
base = bank.get(pat["id"])
over_stream = []
oseq = [0]
t1 = time.time()
def oev(note, vel, dt):
    oseq[0] += 1
    over_stream.append({"kind": "note", "note": note, "velocity": vel, "ts": t1 + dt, "seq": oseq[0]})
orec = NoteRecorder(lambda s0: [e for e in over_stream if e["seq"] > s0], lambda: oseq[0],
                    get_base=bank.get)
orec.arm(name="", bpm=120.0, spb=4, overdub_into=pat["id"])   # arm FIRST (seq filter), then play
oev(70, 99, 2.6); oev(70, 0, 2.9)              # played PAST the current loop end -> doubling
time.sleep(0.03); orec.poll()
merged = orec.stop()
ok(merged is not None and merged["id"] == pat["id"], "overdub keeps id")
end2 = max(n["t_ms"] + n["dur"] for n in merged["notes"])
beat = 60000.0 / 120.0                            # ms per quarter at the take's bpm
ok(merged["bars"] >= pat["bars"] and end2 <= merged["bars"] * 4 * beat,
   f"overdub span covers the take (doubling if played past end): bars {pat['bars']}->{merged['bars']}")
ok(any(n["note"] == 70 for n in merged["notes"]), "overdub note present")
ok(all(n["t_ms"] < merged["bars"] * 4 * beat for n in merged["notes"]), "all merged notes inside new span")
ok(bank.save(merged) == [], "merged saves")
d2 = bank.double(merged["id"])
ok(d2 and d2["bars"] == min(64, merged["bars"] * 2) and
   len(d2["notes"]) == 2 * len(merged["notes"]), "double: x2 bars, x2 notes")
ok(bank.summaries()[0]["notes"] == len(d2["notes"]), "summaries reflect doubles")

# ---------------- player: every death path releases everything ----------------
sinks = []
def sink(note, vel, src):
    sinks.append((note, vel, src, time.monotonic()))
    return True
gen = [0]
live = [True]
player = LoopPlayer(bank, sink, lambda: gen[0], lambda *a: None, live_fn=lambda: live[0])

PATX = {"schema": "mfpat/1", "id": "LP01", "name": "pulse", "bpm": 480.0, "spb": 4, "bars": 1,
        "notes": [{"t_ms": 0.0, "dur": 60.0, "note": 60, "vel": 96},
                  {"t_ms": 50.0, "dur": 80.0, "note": 63, "vel": 96},
                  {"t_ms": 90.0, "dur": 300.0, "note": 67, "vel": 96}]}
bank.save(PATX)   # 1 bar @480bpm = 500ms

def released_cleanly(since):
    held = set()
    for (n, v, s, ts) in sinks:
        if ts < since or not s.startswith("loop:"): continue
        held.add(n) if v > 0 else held.discard(n)
    return held

# once-mode completes + cleans
live[0] = True
a = time.monotonic(); r = player.play("LP01", loop="once")
ok(r["ok"], f"once play: {r}")
time.sleep(1.2)
ok(player.state() == [], "once-mode transport exits by itself")
ok(not released_cleanly(a), "once-mode left NO note down")
ons = [x for x in sinks if x[3] >= a and x[1] > 0]
ok([x[0] for x in ons] == [60, 63, 67], f"note order + all 3 present: {[x[0] for x in ons]}")

# LIVE gate law
live[0] = False
r = player.play("LP01", loop="loop")
ok(not r["ok"] and "LIVE" in r["error"], "play refuses without LIVE (even with fake sink)")
live[0] = True

# stop mid-flight releases held
a = time.monotonic()
ok(player.play("LP01", loop="loop")["ok"], "loop play")
time.sleep(0.62)                    # mid second cycle, note 67 of cycle 1 sustained past cycle edge
ok(player.state(), "still playing")
player.stop("LP01")
time.sleep(0.35)
ok(player.state() == [], "stop cleaned active")
ok(not released_cleanly(a), "STOPPED loop left NO note down")

# panic gen bump = death + release
gen[0] += 100
a = time.monotonic()
ok(player.play("LP01", loop="loop")["ok"], "loop play (pre-panic)")
time.sleep(0.15)
gen[0] += 1                          # /panic bumps this
time.sleep(0.4)
ok(player.state() == [], "panic-gen bump voids transport")
ok(not released_cleanly(a), "PANIC'd loop left NO note down")

# LIVE drop mid-loop = death + release (teeter law on the flaky path)
live_holder = {"v": True}
player.live_fn = lambda: live_holder["v"]
a = time.monotonic()
ok(player.play("LP01", loop="loop")["ok"], "loop play (live-drop test)")
time.sleep(0.15)
live_holder["v"] = False
time.sleep(0.4)
ok(player.state() == [], "LIVE drop ends transport")
ok(not released_cleanly(a), "LIVE-DROPPED loop left NO note down")
live_holder["v"] = True

ok(player.play("nope")["ok"] is False, "unknown id refused")
ok(player.play("LP01", stretch=99)["ok"] is False, "stretch domain enforced")

# ---------------- persistence ----------------
bank2 = LoopBank(TMP / "loops.json")
ok([p["id"] for p in bank2.loops] == [p["id"] for p in bank.loops], "loops survive reload")

if fails:
    print("FAILURES:"); [print(" -", f) for f in fails]; raise SystemExit(1)
print("ALL OK — loop-engine battery (recorder pairing/retrigger/ringout/grid, overdub doubling, "
      "double, bank refuse-closed+persistence, LIVE gate, and TEETER LAW on all 4 death paths)")
