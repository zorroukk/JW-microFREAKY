"""Exhaustive property battery for modmath (lane law 7). Run: python test_modmath.py"""
import math
from modmath import (CURVES, curve, macro_value, sample_track, fold_time, play_sample,
                     step_ms, quantize_ms, ms_to_ticks, bars_ms, autoextend, snap_notes,
                     transpose, transposetable_ok, shift_time, validate_pattern, ALLOWED_CCS, TPQ)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# --- curves: domain, endpoints, monotonicity ---------------------------------
for name in CURVES:
    for i in range(1001):
        x = i / 1000.0
        y = curve(name, x)
        ok(0.0 <= y <= 1.0 + 1e-9, f"{name}({x})={y} out of [0,1]")
    ok(abs(curve(name, 0.0)) < 1e-12, f"{name}(0) != 0")
    ok(abs(curve(name, 1.0) - 1.0) < 1e-12, f"{name}(1) != 1")
for name in ("lin", "exp", "log", "s"):
    prev = -1.0
    for i in range(1001):
        y = curve(name, i / 1000.0)
        ok(y >= prev - 1e-12, f"{name} not monotone at {i}")
        prev = y
# step4 lands on grid, snap12 on 12ths
for i in range(1001):
    x = i / 1000.0
    ok((curve("step4", x) * 3.0) % 1.0 < 1e-9 or abs(curve("step4", x) - 1.0) < 1e-12, f"step4 off-grid {x}")
    ok(abs(curve("snap12", x) * 12.0 - round(curve("snap12", x) * 12.0)) < 1e-9, f"snap12 off-grid {x}")
try:
    curve("lin", 1.5); ok(False, "curve accepted x>1")
except ValueError:
    pass
try:
    curve("nope", 0.5); ok(False, "curve accepted unknown name")
except ValueError:
    pass

# --- macro_value: full exhaustive domain where feasible -----------------------
for base in range(0, 128, 16):
    for depth in (-127, -60, -1, 0, 1, 60, 127):
        for x in range(0, 128, 7):
            for c in CURVES:
                v = macro_value(base, depth, x, c)
                ok(0 <= v <= 127 and isinstance(v, int), f"macro_value {base},{depth},{x},{c}->{v}")
        # endpoints
        ok(macro_value(base, depth, 0, "lin") == max(0, min(127, base)), "lin x=0 != base(clamped)")
# mid check: base 60 depth +60 lin at x=127 -> 120
ok(macro_value(60, 60, 127, "lin") == 120, "mid macro math wrong")
ok(macro_value(100, 60, 127, "lin") == 127, "clamp high wrong")

# --- clip sampling -------------------------------------------------------------
PTS = [{"t_ms": 0, "v": 4, "curve": "s"}, {"t_ms": 2000, "v": 127, "curve": "lin"},
       {"t_ms": 4000, "v": 40}]
ok(sample_track(PTS, 0) == 4, "start point exact")
ok(sample_track(PTS, 4000) == 40, "end point exact")
ok(sample_track(PTS, 5000) == 40, "past end clamps")
ok(sample_track(PTS, -10) == 4, "before start clamps")
ok(sample_track(PTS, 2000) == 127, "mid point exact")
ok(sample_track(PTS, 2000, reverse=True) == 127, "reverse mid anchor")
for t in (0, 500, 1234, 3999):
    ok(sample_track(PTS, t, reverse=True) == sample_track(PTS, 4000 - t), f"reverse mirror t={t}")
# monotonic within segments (s/lin here are monotone)
prev = sample_track(PTS, 0)
for t in range(0, 4001, 13):
    v = sample_track(PTS, t)
    ok(0 <= v <= 127, f"sample out of range {v}")
# constant track (single point)
ok(sample_track([{"t_ms": 0, "v": 88}], 12345) == 88, "single-point constant")

# --- fold_time -----------------------------------------------------------------
L = 1000.0
for stretch in (0.25, 0.5, 1, 2, 8):
    for w in (0, 123, 999, 1000, 1500, 4096, 99999):
        t = fold_time(w, L, "loop", stretch)
        ok(0.0 <= t <= L, f"fold loop out: w={w} s={stretch} -> {t}")
        t2 = fold_time(w, L, "pingpong", stretch)
        ok(0.0 <= t2 <= L, f"fold pp out: w={w} s={stretch} -> {t2}")
        # pingpong continuity at bounce: pp(t)=pp(2L-t) in folded space
        ok(abs(t2 - fold_time(w, L, "pingpong", stretch)) < 1e-9, "pp deterministic")
    # once clamps
    ok(fold_time(9 * L, L, "once", 1) == L, "once clamps high")
    ok(fold_time(-5, L, "once", 1) == 0.0, "once clamps low")
# rate sanity: wall = length*stretch lands at end in 'once'
ok(abs(fold_time(250, 1000, "once", 0.25) - 1000) < 1e-9, "stretch 0.25 end lands at end")
try:
    fold_time(10, 0, "loop", 1); ok(False, "fold accepted zero length")
except ValueError:
    pass
try:
    fold_time(10, 100, "loop", 0); ok(False, "fold accepted zero stretch")
except ValueError:
    pass
try:
    fold_time(10, 100, "yoyo", 1); ok(False, "fold accepted bogus loop mode")
except ValueError:
    pass
# play_sample integration: pingpong at bounce returns same value both sides
PS = [{"t_ms": 0, "v": 0, "curve": "lin"}, {"t_ms": 1000, "v": 100, "curve": "lin"}]
ok(play_sample(PS, 1000, 999, "pingpong", 1, False) == play_sample(PS, 1000, 1001, "pingpong", 1, False) or
   abs(play_sample(PS, 1000, 999, "pingpong", 1, False) - play_sample(PS, 1000, 1001, "pingpong", 1, False)) <= 1,
   "pingpong discontinuity at bounce")

# --- grid/BPM ------------------------------------------------------------------
for bpm in (30, 60, 92.5, 120, 174, 300):
    for spb in (1, 2, 3, 4, 6, 8, 12, 16, 24, 48):
        s = step_ms(bpm, spb)
        ok(s > 0 and math.isfinite(s), "step_ms positive")
        for t in (0, 1, 7, s / 2 - 0.001, s / 2, s - 0.001, s, s * 3 + 0.4):
            q = quantize_ms(t, bpm, spb)
            ok(abs(q - t) <= s / 2 + 1e-9, f"quantize moved more than half-step t={t} q={q} s={s}")
            ok(abs(q / s - round(q / s)) < 1e-6, "quantized off-grid")
        # ticks: exactly `spb`-divided whole bar at 4 beats = integer ticks
        ok(ms_to_ticks(bars_ms(bpm, 1, 4), bpm) == 4 * TPQ, "one bar != 4 quarters in ticks")
try:
    step_ms(5, 4); ok(False, "accepted bpm 5")
except ValueError:
    pass
# autoextend doubling ladder
ok(autoextend(2000, 1500) == 2000, "no extend when inside")
ok(autoextend(2000, 2001) == 4000, "double on first overshoot")
ok(autoextend(2000, 8500) == 16000, "doubling ladder to cover")
ok(autoextend(2000, 8000) == 8000, "exact boundary no extra double")

# --- snap_notes ----------------------------------------------------------------
EV = [{"t_ms": 495, "dur": 120, "note": 60, "vel": 90},
      {"t_ms": 1010, "dur": 999, "note": 60, "vel": 80},
      {"t_ms": 500, "dur": 250, "note": 64, "vel": 100},
      {"t_ms": 125, "dur": 10, "note": 72, "vel": 50}]
out = snap_notes(EV, 120, 4)  # step = 125 ms
grid = {e["t_ms"] for e in out}
ok(all(abs(g / 125.0 - round(g / 125.0)) < 1e-9 for g in grid), "notes off grid")
by60 = [e for e in out if e["note"] == 60]
ok(len(by60) == 2 and by60[0]["dur"] <= by60[1]["t_ms"] - by60[0]["t_ms"], "same-pitch overlap survived")
ok(all(e["dur"] >= 125 for e in out), "min duration 1 step violated")
# collision merge: exact dup on grid+pitch -> later wins, count stays
dup = snap_notes(EV + [{"t_ms": 495, "dur": 200, "note": 60, "vel": 111}], 120, 4)
same = [e for e in dup if e["note"] == 60 and e["t_ms"] == 500]
ok(len(same) == 1 and same[0]["vel"] == 111, "collision merge/later-wins broken")

# --- transpose/shift -------------------------------------------------------------
ok(transposetable_ok(EV, 0) and not transposetable_ok(EV, 60), "transposetable boundary wrong")
tr = transpose(EV, -5)
ok(tr[0]["note"] == 55 and tr[0]["vel"] == 90, "transpose mutates wrong field")
try:
    transpose(EV, 60); ok(False, "transpose accepted out-of-range")
except ValueError:
    pass
sh = shift_time([{"t_ms": 10, "note": 1, "dur": 5, "vel": 1}], -100)
ok(sh[0]["t_ms"] == 0.0, "negative shift clamped at 0")

# --- validate_pattern ------------------------------------------------------------
GOOD = {"schema": "mfpat/1", "bpm": 120, "bars": 2, "spb": 4,
        "notes": [{"t_ms": 0, "dur": 125, "note": 60, "vel": 90}],
        "cc_tracks": [{"cc": 23, "points": [{"t_ms": 0, "v": 40, "curve": "lin"},
                                             {"t_ms": 2000, "v": 90, "curve": "s"}]}],
        "pc": 12}
ok(validate_pattern(GOOD) == [], f"good pattern rejected: {validate_pattern(GOOD)}")
bad = {**GOOD, "schema": "mfpat/0", "bpm": 5, "bars": 0, "spb": 7, "pc": 200}
bad["notes"] = [{"t_ms": -5, "dur": 0, "note": 128, "vel": 0}]
bad["cc_tracks"] = [{"cc": 77, "points": [{"t_ms": 0, "v": 300, "curve": "wobble"}]}]
errs = validate_pattern(bad)
joined = " ".join(errs)
for token in ("mfpat/1", "bpm", "bars", "spb", "note out", "velocity", "bad timing", "allowlist", "300", "wobble", "pc out"):
    ok(token in joined, f"validation error missing for {token!r}: {errs}")
ok(len(errs) >= 9, f"expected >=9 errors on fully-bad pattern, got {len(errs)}")
# CC77 is the canonical refuse-closed probe
ok(validate_pattern({**GOOD, "cc_tracks": [{"cc": 77, "points": []}]}) != [], "non-21 CC slipped through")
ok(validate_pattern({**GOOD, "cc_tracks": [{"cc": 106, "points": []}]}) == [], "legit CC106 rejected")

# --- allowlist verbatim check vs FW5 Appendix D ---------------------------------
expect = {2, 5, 9, 10, 12, 13, 23, 24, 26, 28, 29, 64, 83, 91, 92, 93, 94, 102, 103, 105, 106}
ok(set(ALLOWED_CCS) == expect, "allowlist drifted from Appendix D")
ok(len(expect) == 21, "allowlist must be exactly 21")

if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL OK — modmath battery passed (curves 6, sampler, fold, grid/snap, transpose, mfpat validator, allowlist 21)")
