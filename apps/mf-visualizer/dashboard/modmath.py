"""modmath — pure modulation math for mf-visualizer v0.4 (macros/clips) and v0.5 (loops).

Stdlib-only, deterministic, no I/O. Every function here is property-tested
(test_modmath.py) — lane law 7: new pure math gets exhaustive tests, not curated
examples. All curve functions map 0.0 -> 0.0 and 1.0 -> 1.0 unless inherently
discrete (step/snapping preserve endpoint exactness by design).
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

# ---------------------------------------------------------------- curves ----

CURVES = ("lin", "exp", "log", "s", "step4", "snap12")


def curve(name: str, x: float) -> float:
    """Normalize x in [0,1] through the named shaping curve -> [0,1]."""
    if not (0.0 <= x <= 1.0):  # callers must clamp upstream; refuse loud
        raise ValueError(f"curve input out of range: {x}")
    if name == "lin":
        return x
    if name == "exp":
        return x * x
    if name == "log":
        return 1.0 - (1.0 - x) * (1.0 - x)
    if name == "s":  # smoothstep
        return x * x * (3.0 - 2.0 * x)
    if name == "step4":
        return math.floor(x * 4.0) / 3.0 if x < 1.0 else 1.0
    if name == "snap12":  # 12 musical detents, endpoints exact
        return round(x * 12.0) / 12.0
    raise ValueError(f"unknown curve: {name}")


def macro_value(base: int, depth: int, x7: int, cname: str) -> int:
    """Macro fader -> CC value. base 0..127, depth -127..127, x7 0..127."""
    if not (0 <= base <= 127 and -127 <= depth <= 127 and 0 <= x7 <= 127):
        raise ValueError("macro args out of domain")
    v = base + curve(cname, x7 / 127.0) * depth
    return max(0, min(127, int(round(v))))


# ------------------------------------------------------- clip sampling ------
# track points: [{"t_ms": float, "v": int, "curve": name}] ascending in t.
# curve on a point = shape from THIS point toward the NEXT one.

def _seg(curve_name: str, a: float, b: float, u: float) -> float:
    return a + (b - a) * curve(curve_name, u)


def sample_track(points: Sequence[dict], t_ms: float, reverse: bool = False) -> int:
    """Sample a CC track at absolute clip time (pre-loop-fold). Endpoints exact."""
    if not points:
        raise ValueError("empty track")
    length = points[-1]["t_ms"]
    if length <= 0:  # single-point or zero-length clip: constant
        return int(points[0]["v"])
    if reverse:
        t_ms = length - t_ms
    t_ms = max(0.0, min(float(length), t_ms))
    for i in range(len(points) - 1):
        p, q = points[i], points[i + 1]
        if p["t_ms"] <= t_ms <= q["t_ms"]:
            span = q["t_ms"] - p["t_ms"]
            if span <= 0:
                return int(p["v"])
            u = (t_ms - p["t_ms"]) / span
            return int(round(_seg(p.get("curve", "lin"), float(p["v"]), float(q["v"]), u)))
    return int(points[-1]["v"])


def fold_time(t_ms: float, length_ms: float, loop: str, stretch: float) -> float:
    """Map wall time -> in-clip time with rate, loop mode. Returns t in [0,length]."""
    if length_ms <= 0:
        raise ValueError("length must be positive")
    if stretch <= 0:
        raise ValueError("stretch must be positive")
    t = t_ms / stretch
    if loop == "once":
        return max(0.0, min(float(length_ms), t))
    if loop == "loop":
        return t % length_ms
    if loop == "pingpong":
        period = 2.0 * length_ms
        t %= period
        return t if t <= length_ms else period - t
    raise ValueError(f"unknown loop mode: {loop}")


def play_sample(points: Sequence[dict], length_ms: float, wall_ms: float,
                loop: str, stretch: float, reverse: bool) -> int:
    return sample_track(points, fold_time(wall_ms, length_ms, loop, stretch), reverse=reverse)


# ------------------------------------------------------------ grid/BPM ------

TPQ = 1920  # MIDI ticks per quarter note for export


def step_ms(bpm: float, spb: int = 4) -> float:
    """Milliseconds per quantize step (spb = steps per beat)."""
    if not (1 <= spb <= 96) or not (10.0 <= bpm <= 999.0):
        raise ValueError("bpm/spb out of domain")
    return 60000.0 / bpm / spb


def quantize_ms(t_ms: float, bpm: float, spb: int = 4) -> float:
    return round(t_ms / step_ms(bpm, spb)) * step_ms(bpm, spb)


def ms_to_ticks(t_ms: float, bpm: float) -> int:
    return int(round(t_ms / 60000.0 * bpm * TPQ))


def note_to_ticks(dur_ms: float, bpm: float) -> int:
    return max(1, ms_to_ticks(dur_ms, bpm))  # zero-length notes would eat the next event


def bars_ms(bpm: float, bars: int, beats_per_bar: int = 4) -> float:
    return 60000.0 / bpm * bars * beats_per_bar


def autoextend(loop_ms: float, played_past_ms: float) -> float:
    """Classic doubling: when playback runs past the loop end, double the length."""
    if played_past_ms <= loop_ms:
        return loop_ms
    n = loop_ms
    while n < played_past_ms:
        n *= 2
    return n


# ------------------------------------------------------- note grid ops ------

def snap_notes(events: Iterable[dict], bpm: float, spb: int = 4,
               min_dur_steps: int = 1) -> List[dict]:
    """Quantize {t_ms,dur,note,vel} events to the grid; merge exact-on collisions
    (later wins), clamp durations to at least min_dur_steps, never overlap a
    note with its own next on (ends before next on of the SAME pitch when
    quantized there)."""
    steps = step_ms(bpm, spb)
    snapped: dict = {}
    for e in sorted(events, key=lambda x: (x["t_ms"], x.get("note", 0))):
        t = round(e["t_ms"] / steps) * steps
        d = max(min_dur_steps * steps, round(e["dur"] / steps) * steps)
        snapped[(t, e["note"])] = {**e, "t_ms": t, "dur": d}
    out = sorted(snapped.values(), key=lambda x: (x["t_ms"], x["note"]))
    # per-pitch: end before the next on of the same pitch
    last_off: dict = {}
    for ev in out:
        nxt_on = ev["t_ms"]
        prev_off = last_off.get(ev["note"])
        if prev_off is not None and prev_off > nxt_on:
            pass  # already emitted durations fixed below via two-pass
        last_off[ev["note"]] = ev["t_ms"] + ev["dur"]
    # proper two-pass clamp
    by_pitch: dict = {}
    for ev in out:
        by_pitch.setdefault(ev["note"], []).append(ev)
    for pitch, evs in by_pitch.items():
        evs.sort(key=lambda x: x["t_ms"])
        for i in range(len(evs) - 1):
            gap = evs[i + 1]["t_ms"] - evs[i]["t_ms"]
            if evs[i]["dur"] > gap:
                evs[i]["dur"] = max(1, int(gap))
    return out


def transposetable_ok(events: Sequence[dict], amount: int) -> bool:
    """True iff shifting all notes by `amount` keeps them inside 0..127."""
    return all(0 <= e["note"] + amount <= 127 for e in events)


def transpose(events: List[dict], amount: int) -> List[dict]:
    if not transposetable_ok(events, amount):
        raise ValueError(f"transpose {amount:+d} escapes 0..127")
    return [{**e, "note": e["note"] + amount} for e in events]


def shift_time(events: List[dict], delta_ms: float) -> List[dict]:
    """Rotate/steps events; negative clamps at 0 (start of clip)."""
    return [{**e, "t_ms": max(0.0, e["t_ms"] + delta_ms)} for e in events]


# ------------------------------------------------- mfpat/1 validation ------

ALLOWED_CCS = frozenset({2, 5, 9, 10, 12, 13, 23, 24, 26, 28, 29, 64, 83,
                         91, 92, 93, 94, 102, 103, 105, 106})  # FW5 Appendix D


def validate_pattern(p: dict) -> List[str]:
    """Return a list of human-readable schema errors; empty = valid."""
    errs: List[str] = []
    if p.get("schema") != "mfpat/1":
        errs.append(f"schema must be mfpat/1, got {p.get('schema')!r}")
    bpm = p.get("bpm")
    if not isinstance(bpm, (int, float)) or not (10.0 <= bpm <= 999.0):
        errs.append(f"bpm out of domain: {bpm!r}")
    if p.get("bars", 1) < 1 or p.get("bars", 1) > 64:
        errs.append(f"bars out of 1..64: {p.get('bars')!r}")
    if p.get("spb", 4) not in (1, 2, 3, 4, 6, 8, 12, 16, 24, 48):
        errs.append(f"spb not a musically-sane division: {p.get('spb')!r}")
    for e in p.get("notes", []):
        if not (0 <= e.get("note", -1) <= 127):
            errs.append(f"note out of range: {e!r}")
        if not (1 <= e.get("vel", 0) <= 127):
            errs.append(f"velocity out of range: {e!r}")
        if e.get("t_ms", -1) < 0 or e.get("dur", 0) <= 0:
            errs.append(f"bad timing: {e!r}")
    for tr in p.get("cc_tracks", []):
        cc = tr.get("cc")
        if cc not in ALLOWED_CCS:
            errs.append(f"cc {cc!r} not in FW5 allowlist (refuse-closed)")
        for pt in tr.get("points", []):
            if not (0 <= pt.get("v", -1) <= 127):
                errs.append(f"cc{cc} point value out of range: {pt!r}")
            if pt.get("curve", "lin") not in CURVES:
                errs.append(f"cc{cc} unknown curve {pt.get('curve')!r}")
    if p.get("pc") is not None and not (0 <= p["pc"] <= 127):
        errs.append(f"pc out of range: {p['pc']!r}")
    return errs
