"""Spectrum ballistics battery (v0.5.1 owner spec). Run: python test_spectrum.py

Owner report: "after a frequency is reached it goes downwards too slowly, it
needs to recede much faster ... i wouldnt want more than half a second of time
to be until it goes back to a value of 0."

This battery pins both the DIAGNOSIS of the old law and the new contract, so a
future change cannot quietly reintroduce a slow tail.
"""
import importlib
import math
import os

import plugin_api as pa

fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)


BLOCK = 2048
RATE = 44100
FRAME_DT = BLOCK / RATE          # ~0.04644 s per FFT frame in the worker

# --- the contract -------------------------------------------------------------
# The owner's ceiling is 0.5 s ("no more than half a second"); the v0.5.1a
# default is 0.2 s ("rn its at a snail's pace, it needs to be SNAPPY",
# 2026-09-24). The tough assertions below are against 0.5, not against
# the configured default.
OWNER_CEILING_S = 0.5
ok(pa.SPEC_RELEASE_S <= OWNER_CEILING_S,
   f"SPEC_RELEASE_S {pa.SPEC_RELEASE_S} exceeds the owner ceiling {OWNER_CEILING_S}")
ok(pa.SPEC_RELEASE_S >= 0.2, f"SPEC_RELEASE_S {pa.SPEC_RELEASE_S} is implausibly fast")
ok(abs(pa.SPEC_RELEASE_S - 0.2) < 1e-9,
   f"v0.5.1a snappy default moved: SPEC_RELEASE_S is {pa.SPEC_RELEASE_S}, expected 0.2")
ok(abs(pa.SPEC_RELEASE_PER_S - 1.0 / pa.SPEC_RELEASE_S) < 1e-9,
   "SPEC_RELEASE_PER_S is not the reciprocal of SPEC_RELEASE_S")
# frame-grid margin: a full-scale band must be at 0 within the ceiling even
# when the clear time rounds UP to the next FFT frame boundary.
_grid = math.ceil(pa.SPEC_RELEASE_S / FRAME_DT) * FRAME_DT
ok(_grid <= OWNER_CEILING_S, f"grid-rounded clear time {_grid:.3f}s breaks the ceiling")

# --- OLD LAW (the bug), reproduced from first principles ----------------------
# k=0.06 power-domain release -> dB falls 10*log10(1/(1-0.06)) per frame.
old_db_per_frame = 10.0 * math.log10(1.0 / (1.0 - 0.06))
old_clear_s = (pa.SPEC_SPAN_DB / old_db_per_frame) * FRAME_DT
ok(old_clear_s > 10.0, f"old-law reproduction wrong: {old_clear_s:.2f}s (expected >10s)")
print(f"  old law: {old_db_per_frame:.3f} dB/frame -> {old_clear_s:.2f} s to clear "
      f"a {pa.SPEC_SPAN_DB:.0f} dB window  <-- the owner's complaint")

# --- NEW LAW: full scale -> 0 within SPEC_RELEASE_S ---------------------------
peaks = [1.0, 0.75, 0.5, 0.2, 0.05]
for peak in peaks:
    v, t = peak, 0.0
    while t < 1.0 and v > 0.0:
        v = float(pa._spec_ballistics(v, 0.0, FRAME_DT))
        t += FRAME_DT
    ok(v == 0.0, f"peak {peak} never reached 0")
    ok(t <= OWNER_CEILING_S + 1e-9,
       f"peak {peak} cleared in {t:.3f}s > owner ceiling {OWNER_CEILING_S}s")
    # and it must not clear absurdly early either (that would be a visible snap)
    ok(t >= pa.SPEC_RELEASE_S * peak - FRAME_DT - 1e-9,
       f"peak {peak} cleared too early at {t:.3f}s (snap, not a recede)")

# worst case: a full-scale peak must be at 0 within the owner ceiling
v = 1.0
steps = int(math.ceil(OWNER_CEILING_S / FRAME_DT))
for _ in range(steps):
    v = float(pa._spec_ballistics(v, 0.0, FRAME_DT))
ok(v == 0.0, f"full scale not at 0 after {OWNER_CEILING_S}s of silence (v={v})")

# --- instant rise, no attack lag ---------------------------------------------
ok(float(pa._spec_ballistics(0.0, 1.0, FRAME_DT)) == 1.0, "rise is not instant")
ok(float(pa._spec_ballistics(0.3, 0.9, 1e-3)) == 0.9, "rise clamped a stronger signal")

# --- a sounding band never fades under itself --------------------------------
v = 0.0
for _ in range(200):
    v = float(pa._spec_ballistics(v, 0.42, FRAME_DT))
ok(abs(v - 0.42) < 1e-9, f"sustained tone drifted to {v} (should hold 0.42)")

# --- floor, monotonicity, degenerate dt --------------------------------------
v = 0.0
for _ in range(50):
    v = float(pa._spec_ballistics(v, 0.0, 0.25))
ok(v == 0.0, "fell below zero")
seq = [1.0]
for _ in range(20):
    seq.append(float(pa._spec_ballistics(seq[-1], 0.0, FRAME_DT)))
ok(all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1)), "fall is not monotone")
ok(float(pa._spec_ballistics(0.5, 0.0, 0.0)) == 0.5, "zero dt moved the value")
ok(float(pa._spec_ballistics(0.5, 0.0, -3.0)) == 0.5, "negative dt moved the value")

# --- frame-rate independence: the point of timing it in the display domain ----
def clear_time(dt):
    v, t = 1.0, 0.0
    while v > 0.0 and t < 5.0:
        v = float(pa._spec_ballistics(v, 0.0, dt))
        t += dt
    return t


# The difference between two frame grids is bounded by ONE coarse frame on each
# side; an absolute tolerance would just encode the old 0.45 s default (it broke
# honestly when v0.5.1a tightened release to 0.2 s). Pin the law, not the number.
t_fast, t_slow = clear_time(0.010), clear_time(FRAME_DT)
ok(abs(t_fast - t_slow) <= FRAME_DT + 1e-9,
   f"clear time depends on frame rate: {t_fast:.3f}s @10ms vs {t_slow:.3f}s @{FRAME_DT*1000:.1f}ms")

# --- env override -------------------------------------------------------------
os.environ["MF_VIS_SPEC_RELEASE"] = "0.25"
reloaded = importlib.reload(pa)
ok(abs(reloaded.SPEC_RELEASE_S - 0.25) < 1e-9, "MF_VIS_SPEC_RELEASE override ignored")
ok(abs(reloaded.SPEC_RELEASE_PER_S - 4.0) < 1e-9, "override did not reach the rate")
v, t = 1.0, 0.0
while v > 0.0 and t < 5.0:
    v = float(reloaded._spec_ballistics(v, 0.0, 0.01))
    t += 0.01
ok(t <= 0.25 + 1e-9, f"override clear time {t:.3f}s > 0.25s")
del os.environ["MF_VIS_SPEC_RELEASE"]
importlib.reload(pa)

# --- the dB mapping the display rides on --------------------------------------
ok(pa._dbfs_norm(1e-9) == 0.0, "-90 dBFS should sit at the floor (0)")
ok(abs(pa._dbfs_norm(1.0) - 1.0) < 1e-9, "0 dBFS should sit at 1.0")
ok(0.0 <= pa._dbfs_norm(1e-6) <= 1.0, "mid mapping out of range")

if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print(f"ALL OK — spectrum battery passed (clear<= {pa.SPEC_RELEASE_S}s, instant rise, "
      f"hold-under-tone, floor, frame-rate independence, env override, dB map)")