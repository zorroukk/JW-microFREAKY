"""modengine — macro bank + clip studio for mf-visualizer v0.4.

Stdlib-only; all device IO happens through an injected `send_fn(cc, value, source)`
supplied by plugin_api (which owns mido/LIVE-gate/sendlog/apply_event). That keeps
every lane behavior-testable with a fake sink and zero hardware.

Persistence (lane rule: user data, NOT device state — never seeded from sendlog):
  %LOCALAPPDATA%/mf-visualizer/macros.json   -> macro bank
  %LOCALAPPDATA%/mf-visualizer/clips.json    -> clip bank (mfclip/1 objects)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from modmath import CURVES, macro_value, play_sample, snap_notes  # snap_notes used by v0.5

SendFn = Callable[[int, int, str], bool]
SendNoteFn = Callable[[int, int, str], bool]  # v0.5 loops: (note, velocity|0, source)

MAX_MACROS = 8
MAX_TARGETS = 8


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt user file must not brick the pane
        pass
    return fallback


# ============================================================ validation ===

def validate_macro(m: dict, allow: Set[int]) -> List[str]:
    errs: List[str] = []
    if not str(m.get("id", "")).strip():
        errs.append("macro id required")
    if not (0 <= int(m.get("x", 0) or 0) <= 127):
        errs.append("macro x out of range")
    tgts = m.get("targets")
    if not isinstance(tgts, list) or not 1 <= len(tgts) <= MAX_TARGETS:
        errs.append(f"targets must be 1..{MAX_TARGETS} entries")
        return errs
    seen_cc = set()
    for t in tgts:
        try:
            cc, base, depth = int(t["cc"]), int(t["base"]), int(t["depth"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"target fields bad: {t!r}")
            continue
        if cc not in allow:
            errs.append(f"cc {cc} not in FW5 allowlist - refusing")
        if cc in seen_cc:
            errs.append(f"cc {cc} duplicated in one macro")
        seen_cc.add(cc)
        if not 0 <= base <= 127:
            errs.append(f"base out of range: {base}")
        if not -127 <= depth <= 127:
            errs.append(f"depth out of range: {depth}")
        if t.get("curve", "lin") not in CURVES:
            errs.append(f"unknown curve {t.get('curve')!r}")
    return errs


def validate_clip(c: dict, allow: Set[int]) -> List[str]:
    errs: List[str] = []
    if c.get("schema") != "mfclip/1":
        errs.append(f"schema must be mfclip/1, got {c.get('schema')!r}")
    if not str(c.get("id", "")).strip():
        errs.append("clip id required")
    if not isinstance(c.get("length_ms"), (int, float)) or not (100 <= c["length_ms"] <= 600_000):
        errs.append(f"length_ms out of 100..600000: {c.get('length_ms')!r}")
    tracks = c.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        errs.append("clip needs at least one cc track")
        return errs
    for tr in tracks:
        cc = tr.get("cc")
        if cc not in allow:
            errs.append(f"cc {cc!r} not in FW5 allowlist - refusing")
        pts = tr.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= 2000:
            errs.append(f"cc{cc} needs 2..2000 points")
            continue
        last_t = -1.0
        for p in pts:
            try:
                t, v = float(p["t_ms"]), int(p["v"])
            except (KeyError, TypeError, ValueError):
                errs.append(f"cc{cc} bad point {p!r}")
                continue
            if t < last_t:
                errs.append(f"cc{cc} points not ascending at t={t}")
            last_t = t
            if not 0 <= v <= 127:
                errs.append(f"cc{cc} point value out of range: {v}")
            if p.get("curve", "lin") not in CURVES:
                errs.append(f"cc{cc} unknown curve {p.get('curve')!r}")
    return errs


# ============================================================== macro bank ==

class MacroBank:
    """8 faders -> nonlinear fan-out over allowlisted CCs. Backend-evaluated so
    a closed pane can never orphan a half-moved stream; positions persist so the
    pane and the device re-sync on reopen."""

    def __init__(self, path: Path, allow: Set[int], send_fn: SendFn,
                 log: Callable[..., None] = lambda *a: None) -> None:
        self.path, self.allow, self.send, self.log = path, allow, send_fn, log
        self.lock = threading.Lock()
        data = _load(path, {"version": 1, "macros": []})
        self.macros: List[dict] = [m for m in data.get("macros", [])
                                   if not validate_macro(m, allow)][:MAX_MACROS]
        self._last_sent: Dict[str, Dict[int, int]] = {}   # macro id -> {cc: value}

    def get(self, mid: str) -> Optional[dict]:
        with self.lock:
            return next((m for m in self.macros if m["id"] == mid), None)

    def save(self, macro: dict) -> List[str]:
        errs = validate_macro(macro, self.allow)
        if errs:
            return errs
        with self.lock:
            for i, m in enumerate(self.macros):
                if m["id"] == macro["id"]:
                    self.macros[i] = macro
                    break
            else:
                if len(self.macros) >= MAX_MACROS:
                    return [f"macro bank full ({MAX_MACROS}) - delete one first"]
                self.macros.append(macro)
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "macros": self.macros}, indent=1))
            except OSError as exc:
                return [f"save failed: {exc}"]
        with self.lock:
            self._last_sent.pop(macro["id"], None)  # config changed: force restick on next move
        return []

    def delete(self, mid: str) -> bool:
        with self.lock:
            before = len(self.macros)
            self.macros = [m for m in self.macros if m["id"] != mid]
            if len(self.macros) == before:
                return False
            self._last_sent.pop(mid, None)
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "macros": self.macros}, indent=1))
            except OSError as exc:
                self.log("macro save failed:", exc)
            return True

    def move(self, mid: str, x: int) -> Dict[str, Any]:
        """Fader moved to x (0..127): evaluate every target, send what CHANGED
        (the coalescer), persist the position. Returns sends for the pane."""
        if not 0 <= x <= 127:
            return {"ok": False, "error": "x out of range 0..127"}
        with self.lock:
            m = next((mm for mm in self.macros if mm["id"] == mid), None)
            if m is None:
                return {"ok": False, "error": f"no macro {mid}"}
            m["x"] = x
            sent = self._last_sent.setdefault(mid, {})
            out = []
            for t in m["targets"]:
                v = macro_value(int(t["base"]), int(t["depth"]), x, t.get("curve", "lin"))
                cc = int(t["cc"])
                if sent.get(cc) == v:
                    continue
                if self.send(cc, v, f"macro:{mid}"):
                    sent[cc] = v
                    out.append({"cc": cc, "value": v})
            try:  # position survives restarts (user data, not device truth)
                _atomic_write(self.path, json.dumps({"version": 1, "macros": self.macros}, indent=1))
            except OSError as exc:
                self.log("macro position save failed:", exc)
        return {"ok": True, "id": mid, "x": x, "sends": out}

    def reset_hardware_echo(self) -> None:
        """Panel/MIDI-Control moves can desync the bank's idea of 'last sent'.
        Called on panic so the next fader tick always resticks."""
        with self.lock:
            self._last_sent.clear()

    def state(self) -> List[dict]:
        with self.lock:
            return [{"id": m["id"], "name": m.get("name", ""), "x": m.get("x", 0),
                     "targets": len(m.get("targets", []))} for m in self.macros]


# =============================================================== recorder ===

class ClipRecorder:
    """Records CC movement observed by the backend while armed. Sources: STATE
    events (panel knob turns via the MIDI-IN listener + ui/live sends + relay +
    macro streams). Reads through injected `events_since(seq)` — no direct
    STATE coupling, so it is testable and can never block the reducer."""

    def __init__(self, events_since: Callable[[int], Sequence[dict]],
                 seq_fn: Callable[[], int],
                 log: Callable[..., None] = lambda *a: None) -> None:
        self.events_since, self.seq_fn, self.log = events_since, seq_fn, log
        self.armed = False
        self._seq0 = 0
        self._t0 = 0.0
        self._acc: Dict[int, List[dict]] = {}
        self.name = ""

    def arm(self, name: str = "") -> None:
        self.armed = True
        self._seq0 = self.seq_fn()
        self._t0 = time.time()
        self._acc = {}
        self.name = name

    def poll(self) -> None:
        """Sweep new CC events into per-cc points (dedupe runs, keep reversals)."""
        if not self.armed:
            return
        for ev in self.events_since(self._seq0):
            if ev.get("kind") != "cc":
                continue
            t_ms = round((float(ev.get("ts", self._t0)) - self._t0) * 1000.0, 1)
            pts = self._acc.setdefault(int(ev["cc"]), [])
            v = int(ev["value"])
            if pts and pts[-1]["v"] == v:
                continue
            pts.append({"t_ms": t_ms, "v": v, "curve": "lin"})

    def stop(self, tail_ms: float = 500.0) -> Optional[dict]:
        """-> mfclip/1 clip or None (nothing recorded). Length = last move + tail."""
        if not self.armed:
            return None
        self.poll()          # v0.4 battery caught: capture the tail WHILE still armed
        self.armed = False
        global_end = self._end_hint()
        tracks = []
        for cc, pts in sorted(self._acc.items()):
            if not pts:
                continue
            if pts[0]["t_ms"] > 0:
                if pts[0]["t_ms"] < 20:
                    pts[0] = {**pts[0], "t_ms": 0.0}   # moved with the arm press: that IS t=0
                else:  # delayed first move: hold the pre-value from clip start
                    pts = [{"t_ms": 0.0, "v": pts[0]["v"], "curve": "lin"}, *pts]
            if len(pts) == 1:  # a lone move is still a clip: hold it across the span
                pts = [*pts, {"t_ms": max(1.0, global_end), "v": pts[0]["v"], "curve": "lin"}]
            last = pts[-1]
            if last["t_ms"] < global_end:
                pts = [*pts, {"t_ms": global_end, "v": last["v"], "curve": "lin"}]
            tracks.append({"cc": cc, "points": pts})
        if not tracks:
            return None
        length = max(p["t_ms"] for tr in tracks for p in tr["points"]) + tail_ms
        return {"schema": "mfclip/1", "id": f"c{int(self._t0 * 1000) % 100000:05d}",
                "name": self.name or time.strftime("clip-%H%M%S"),
                "length_ms": round(length, 1), "tracks": tracks,
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "recorded_source": "sendlog+panel"}

    def _end_hint(self) -> float:
        return max((p["t_ms"] for pts in self._acc.values() for p in pts), default=0.0)


# ================================================================ player ====

class ClipPlayer:
    """Threads-based clip transport (asyncio tasks die between requests on the
    TestClient event loop — lane lesson; threading.Timer/Thread are proven).
    Sends through the injected sink only; the sink owns allowlist/LIVE/sendlog."""

    def __init__(self, clips: "ClipBank", send_fn: SendFn,
                 gen_fn: Callable[[], int], log: Callable[..., None],
                 live_fn: Optional[Callable[[], bool]] = None, mock: bool = False) -> None:
        self.clips, self.send, self.gen_fn, self.log = clips, send_fn, gen_fn, log
        self.live_fn, self.mock = live_fn, mock
        self.lock = threading.Lock()
        self.active: Dict[str, Dict[str, Any]] = {}   # clip id -> session

    def play(self, clip_id: str, loop: str = "loop", stretch: float = 1.0,
             reverse: bool = False, mute: Optional[Set[int]] = None) -> Dict[str, Any]:
        if self.live_fn is not None and not self.live_fn():
            # LIVE arm gates playback even in MOCK: the toggle means "I intend to
            # drive something" — rehearsal muscle memory must match hardware truth.
            return {"ok": False, "error": "clip playback needs LIVE on (it puts a stream on the wire)"}
        clip = self.clips.get(clip_id)
        if clip is None:
            return {"ok": False, "error": f"no clip {clip_id}"}
        if loop not in ("loop", "pingpong", "once"):
            return {"ok": False, "error": f"bad loop mode {loop!r}"}
        if not 0.25 <= stretch <= 8.0:
            return {"ok": False, "error": "stretch out of 0.25..8"}
        with self.lock:
            if clip_id in self.active:
                return {"ok": False, "error": "clip already playing (stop it first)"}
            sess = {"t0": time.monotonic(), "loop": loop, "stretch": float(stretch),
                    "reverse": bool(reverse), "mute": set(mute or ()),
                    "last": {}, "thread": None, "gen": self.gen_fn(),
                    "position_ms": 0.0, "done": False}
            self.active[clip_id] = sess
        th = threading.Thread(target=self._run, args=(clip_id, clip, sess),
                              name=f"mfvis-clip-{clip_id}", daemon=True)
        sess["thread"] = th
        th.start()
        return {"ok": True, "id": clip_id, "loop": loop, "stretch": sess["stretch"],
                "reverse": sess["reverse"], "length_ms": clip["length_ms"]}

    def _run(self, clip_id: str, clip: dict, sess: Dict[str, Any]) -> None:
        wall_end = clip["length_ms"] * sess["stretch"]
        while True:
            with self.lock:
                if self.active.get(clip_id) is not sess:
                    return                       # stopped or replaced
            if sess["gen"] != self.gen_fn():     # panic voids running clips
                with self.lock:
                    self.active.pop(clip_id, None)
                return
            wall = (time.monotonic() - sess["t0"]) * 1000.0
            sess["position_ms"] = min(wall, wall_end) if sess["loop"] == "once" else wall % wall_end
            if sess["loop"] == "once" and wall >= wall_end:
                for tr in clip["tracks"]:   # endpoint commit: the last sampled tick may
                    cc = int(tr["cc"])      # land short of the end (Windows sleep granularity);
                    if cc in sess["mute"]:  # once-mode MUST finish on the clip's own values
                        continue
                    v = play_sample(tr["points"], float(clip["length_ms"]), wall_end,
                                    "once", sess["stretch"], sess["reverse"])
                    if sess["last"].get(cc) != v:
                        try:
                            self.send(cc, v, f"clip:{clip_id}")
                        except Exception:  # noqa: BLE001 - we are already ending
                            pass
                with self.lock:
                    self.active.pop(clip_id, None)
                    sess["done"] = True
                return
            for tr in clip["tracks"]:
                cc = int(tr["cc"])
                if cc in sess["mute"]:
                    continue
                v = play_sample(tr["points"], float(clip["length_ms"]), wall,
                                sess["loop"], sess["stretch"], sess["reverse"])
                if sess["last"].get(cc) == v:
                    continue                     # coalescer: no restick on flat runs
                sess["last"][cc] = v
                try:
                    if not self.send(cc, v, f"clip:{clip_id}"):
                        with self.lock:
                            self.active.pop(clip_id, None)
                        self.log(f"clip {clip_id}: send refused, playback stopped")
                        return
                except Exception as exc:  # noqa: BLE001 - the transport must die, not the app
                    with self.lock:
                        self.active.pop(clip_id, None)
                    self.log(f"clip {clip_id} transport error:", exc)
                    return
            time.sleep(0.02)

    def stop(self, clip_id: str) -> bool:
        with self.lock:
            return self.active.pop(clip_id, None) is not None

    def stop_all(self) -> int:
        with self.lock:
            n = len(self.active)
            self.active.clear()
        return n

    def state(self) -> List[dict]:
        with self.lock:
            return [{"id": cid, "loop": s["loop"], "stretch": s["stretch"],
                     "reverse": s["reverse"], "position_ms": round(s["position_ms"], 0),
                     "mute": sorted(s["mute"])} for cid, s in self.active.items()]


# ================================================================= bank ====

class ClipBank:
    def __init__(self, path: Path, allow: Set[int], log: Callable[..., None] = lambda *a: None) -> None:
        self.path, self.allow, self.log = path, allow, log
        self.lock = threading.Lock()
        data = _load(path, {"version": 1, "clips": []})
        self.clips: List[dict] = [c for c in data.get("clips", [])
                                  if not validate_clip(c, allow)]

    def get(self, cid: str) -> Optional[dict]:
        with self.lock:
            return next((c for c in self.clips if c["id"] == cid), None)

    def save(self, clip: dict) -> List[str]:
        errs = validate_clip(clip, self.allow)
        if errs:
            return errs
        with self.lock:
            for i, c in enumerate(self.clips):
                if c["id"] == clip["id"]:
                    self.clips[i] = clip
                    break
            else:
                self.clips.append(clip)
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "clips": self.clips}, indent=1))
            except OSError as exc:
                return [f"save failed: {exc}"]
        return []

    def delete(self, cid: str) -> bool:
        with self.lock:
            before = len(self.clips)
            self.clips = [c for c in self.clips if c["id"] != cid]
            if len(self.clips) == before:
                return False
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "clips": self.clips}, indent=1))
            except OSError as exc:
                self.log("clip delete save failed:", exc)
            return True

    def summaries(self) -> List[dict]:
        with self.lock:
            return [{"id": c["id"], "name": c.get("name", ""),
                     "length_ms": c["length_ms"], "tracks": sorted(int(t["cc"]) for t in c["tracks"])}
                    for c in self.clips]

# ============================================================= v0.5 loops ===
# LOOP STUDIO: record what you PLAY (note on/off pairs observed through the
# same events_since seam the CC recorder uses), loop/overdub/double them, play
# back through the injected note sink, and export to Standard MIDI Files so
# FL Studio can actually READ the take (clips.json is our schema; FL is not).
# Law carried from v0.4: threading transport (TestClient asyncio lesson),
# panic-gen self-void, LIVE-arm gate, and the TEETER LAW - a transport that
# dies for ANY reason (stop, panic, LIVE drop, exception) MUST release every
# note it took down. An owner-visible held note with no owner holding it is
# what made the hardware arp wander (109 note-ons vs 27 note-offs, 2026-09-19).

from modmath import autoextend, bars_ms, snap_notes, validate_pattern


class NoteRecorder:
    """Note-on/off pairs seen while armed -> mfpat/1 (quantized, auto-lengthed).
    overdub_into=<id> merges the new take into an existing pattern."""

    def __init__(self, events_since: Callable[[int], Sequence[dict]],
                 seq_fn: Callable[[], int],
                 get_base: Optional[Callable[[str], Optional[dict]]] = None,
                 log: Callable[..., None] = lambda *a: None) -> None:
        self.events_since, self.seq_fn, self.get_base, self.log = \
            events_since, seq_fn, get_base, log
        self.armed = False
        self._seq0 = 0
        self._t0 = 0.0
        self._open: Dict[int, List[float]] = {}     # note -> [t_on_ms, vel]
        self._notes: List[dict] = []
        self.name, self.bpm, self.spb, self.over = "", 120.0, 4, None

    def arm(self, name: str = "", bpm: float = 120.0, spb: int = 4,
            overdub_into: Optional[str] = None) -> None:
        self.armed = True
        self._seq0 = self.seq_fn()
        self._t0 = time.time()
        self._open, self._notes = {}, []
        self.name, self.bpm, self.spb, self.over = name, float(bpm), int(spb), overdub_into

    def poll(self) -> None:
        if not self.armed:
            return
        for ev in self.events_since(self._seq0):
            seq = ev.get("seq", 0)
            if seq > self._seq0:
                self._seq0 = seq                      # pairs are consumed EXACTLY once
            if ev.get("kind") != "note":
                continue
            t_ms = round((float(ev.get("ts", self._t0)) - self._t0) * 1000.0, 1)
            n = int(ev["note"]); vel = int(ev.get("velocity", 0))
            if vel > 0:
                prev = self._open.pop(n, None)        # retrigger = the old one ended here
                if prev is not None:
                    self._notes.append({"t_ms": prev[0], "dur": max(1.0, t_ms - prev[0]),
                                        "note": n, "vel": int(prev[1])})
                self._open[n] = [t_ms, vel]
            elif n in self._open:
                on, ovel = self._open.pop(n)
                self._notes.append({"t_ms": on, "dur": max(1.0, t_ms - on), "note": n, "vel": int(ovel)})

    def stop(self, tail_ms: float = 400.0) -> Optional[dict]:
        if not self.armed:
            return None
        self.poll()                                   # capture tail WHILE armed (v0.4 law)
        self.armed = False
        end = max([t for nt in self._notes for t in (nt["t_ms"] + nt["dur"], nt["t_ms"])] +
                  [v[0] for v in self._open.values()] + [0.0])
        for n, (on, vel) in self._open.items():       # still-held at STOP: ring out to end+tail
            self._notes.append({"t_ms": on, "dur": max(1.0, end + tail_ms - on), "note": n, "vel": int(vel)})
        self._open = {}
        base = self.get_base(self.over) if (self.over and self.get_base) else None
        raw = (list(base["notes"]) if base else []) + self._notes
        if not raw:
            return None
        bpm, spb = (base["bpm"], base["spb"]) if base else (self.bpm, self.spb)
        notes = snap_notes(raw, bpm, spb)
        length = max(nt["t_ms"] + nt["dur"] for nt in notes)
        bars = int(base.get("bars", 0)) if base else 0
        if bars == 0:
            while bars and bars < 64 and bars_ms(bpm, bars) < length:
                bars *= 2                             # paranoid: base shorter than merged
            bars = max(1, int(bars or 1))
        span = bars_ms(bpm, bars)
        while span < length and bars < 64:            # overdub past the end: classic doubling
            bars = int(autoextend(span, length) / bars_ms(bpm, 1)) or bars * 2
            bars = max(bars, 1)
            span = bars_ms(bpm, bars)
        return {"schema": "mfpat/1",
                "id": (base["id"] if base else f"L{int(self._t0 * 1000) % 100000:05d}"),
                "name": self.name or (base.get("name") if base else "") or time.strftime("loop-%H%M%S"),
                "bpm": round(float(bpm), 2), "spb": int(spb), "bars": min(64, int(bars)),
                "notes": notes,
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "recorded_source": "events+overdub" if base else "events"}

    def snapshot(self) -> Optional[dict]:
        """/state's loop_rec: what (if anything) is armed right now."""
        if not self.armed:
            return None
        return {"armed": True, "name": self.name, "overdub_into": self.over,
                "bpm": round(float(self.bpm), 2), "spb": int(self.spb)}


class LoopBank:
    """mfpat/1 store at loops.json - user data, never seeded from sendlog."""

    def __init__(self, path: Path, log: Callable[..., None] = lambda *a: None) -> None:
        self.path, self.log = Path(path), log
        self.lock = threading.Lock()
        data = _load(self.path, {"version": 1, "loops": []})
        raw = data.get("loops", []) if isinstance(data, dict) else []
        self.loops = [p for p in raw if isinstance(p, dict) and not validate_pattern(p)]

    def get(self, lid: str) -> Optional[dict]:
        with self.lock:
            return next((p for p in self.loops if p.get("id") == lid), None)

    def save(self, pattern: dict) -> List[str]:       # upsert by id
        errs = validate_pattern(pattern)
        if errs:
            return errs
        with self.lock:
            before = len(self.loops)
            self.loops[:] = [p for p in self.loops if p.get("id") != pattern["id"]]
            self.loops.append(json.loads(json.dumps(pattern)))   # deep copy, freeze
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "loops": self.loops}, indent=1))
            except OSError as exc:
                self.log("loop save failed:", exc)
                return [f"write failed: {exc}"]
            self.log(f"loop {pattern['id']} saved ({len(self.loops)} total, was {before})")
        return []

    def delete(self, lid: str) -> bool:
        with self.lock:
            before = len(self.loops)
            self.loops[:] = [p for p in self.loops if p.get("id") != lid]
            if len(self.loops) == before:
                return False
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "loops": self.loops}, indent=1))
            except OSError as exc:
                self.log("loop delete save failed:", exc)
            return True

    def double(self, lid: str) -> Optional[dict]:     # copy notes +length, bars x2
        with self.lock:
            pat = next((p for p in self.loops if p.get("id") == lid), None)
            if pat is None or pat["bars"] >= 64:
                return None
            span = bars_ms(pat["bpm"], pat["bars"])
            dup = json.loads(json.dumps(pat))
            dup["bars"] = pat["bars"] * 2
            dup["notes"] = pat["notes"] + [
                {**nt, "t_ms": round(nt["t_ms"] + span, 1)} for nt in pat["notes"]]
            errs = validate_pattern(dup)
            if errs:
                self.log("double produced invalid pattern:", errs)
                return None
            dup["cc_tracks"] = pat.get("cc_tracks", [])
            self.loops[:] = [dup if p.get("id") == lid else p for p in self.loops]
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "loops": self.loops}, indent=1))
            except OSError as exc:
                self.log("loop double save failed:", exc)
                return None
            return dup

    def resize(self, lid: str, bars: int) -> Optional[dict]:
        """Set the loop to `bars` long (1..64). SHRINK truncates notes at the new
        span (dur clamped so nothing writes past the end); GROW leaves a silent
        tail to fill with ✎ overdub. Returns the new pattern or None."""
        bars = int(bars)
        with self.lock:
            pat = next((p for p in self.loops if p.get("id") == lid), None)
            if pat is None or not (1 <= bars <= 64):
                return None
            new_span = bars_ms(pat["bpm"], bars)
            res = json.loads(json.dumps(pat))
            res["bars"] = bars
            res["notes"] = [{**nt, "t_ms": nt["t_ms"],
                             "dur": max(1.0, min(nt["dur"], new_span - nt["t_ms"]))}
                            for nt in pat["notes"] if nt["t_ms"] < new_span]
            res["cc_tracks"] = [
                {**t, "points": [p for p in t.get("points", []) if p["t_ms"] < new_span]}
                for t in pat.get("cc_tracks", [])]
            errs = validate_pattern(res)
            if errs:
                self.log("resize produced invalid pattern:", errs)
                return None
            self.loops[:] = [res if p.get("id") == lid else p for p in self.loops]
            try:
                _atomic_write(self.path, json.dumps({"version": 1, "loops": self.loops}, indent=1))
            except OSError as exc:
                self.log("loop resize save failed:", exc)
                return None
            return res

    def summaries(self) -> List[dict]:
        with self.lock:
            return [{"id": p["id"], "name": p.get("name", ""), "bpm": p["bpm"],
                     "spb": p["spb"], "bars": p["bars"], "notes": len(p.get("notes", [])),
                     "length_ms": round(bars_ms(p["bpm"], p["bars"]), 1)} for p in self.loops]


class LoopPlayer:
    """One thread per loop session. SCHEDULER NEVER LEAVES A NOTE DOWN: every
    exit path (stop/once/panic-gen/LIVE-drop/exception) releases held notes."""

    def __init__(self, bank: LoopBank, note_send: SendNoteFn,
                 gen_fn: Callable[[], int], log: Callable[..., None],
                 live_fn: Optional[Callable[[], bool]] = None) -> None:
        self.bank, self.send, self.gen_fn, self.log = bank, note_send, gen_fn, log
        self.live_fn = live_fn
        self.lock = threading.Lock()
        self.active: Dict[str, Dict[str, Any]] = {}

    def play(self, lid: str, loop: str = "loop", stretch: float = 1.0) -> Dict[str, Any]:
        if self.live_fn is not None and not self.live_fn():
            return {"ok": False, "error": "loop playback needs LIVE on (it puts notes on the wire)"}
        pat = self.bank.get(lid)
        if pat is None:
            return {"ok": False, "error": f"no loop {lid}"}
        if not (0.25 <= float(stretch) <= 8.0):
            return {"ok": False, "error": "stretch out of 0.25..8"}
        with self.lock:
            if lid in self.active:
                return {"ok": False, "error": "already playing - stop it first"}
            sess = {"stop": threading.Event(), "gen": self.gen_fn(), "held": set()}
            self.active[lid] = sess
        t = threading.Thread(target=self._run, args=(lid, dict(pat), sess, loop, float(stretch)),
                             name=f"mfvis-loop-{lid}", daemon=True)
        t.start()
        return {"ok": True, "playing": lid}

    def _run(self, lid: str, pat: dict, sess: dict, loop: str, stretch: float) -> None:
        span = bars_ms(pat["bpm"], pat["bars"]) * stretch
        evs = []
        for nt in pat["notes"]:
            evs.append((nt["t_ms"] * stretch, 1, nt["note"], int(nt.get("vel", 96))))
            evs.append(((nt["t_ms"] + nt["dur"]) * stretch, 0, nt["note"], 0))
        evs.sort(key=lambda e: (e[0], e[1] == 0))     # offs before ons at the same instant
        stop, held = sess["stop"], sess["held"]
        try:
            cycles = 1 if loop == "once" else 10 ** 6
            for k in range(cycles):
                base_ms = k * span
                t0 = time.monotonic()
                for (t_ms, is_on, n, v) in evs:
                    due = t0 + (t_ms) / 1000.0
                    while time.monotonic() < due:
                        if stop.is_set() or self.gen_fn() != sess["gen"] or \
                                (self.live_fn is not None and not self.live_fn()):
                            return                       # finally-block releases everything
                        time.sleep(min(0.02, due - time.monotonic()))
                    if is_on:
                        held.add(n)
                        self.send(n, v, f"loop:{lid}")
                    else:
                        held.discard(n)
                        self.send(n, 0, f"loop:{lid}")
                    if stop.is_set() or self.gen_fn() != sess["gen"]:
                        return
        except Exception as exc:  # noqa: BLE001 - the loop must never strand notes
            self.log(f"loop {lid} transport crashed (state cleaned): {exc}")
        finally:
            for n in list(held):
                try:
                    self.send(n, 0, f"loop:{lid}")       # TEETER LAW: no note leaves down
                except Exception:  # noqa: BLE001
                    pass
            held.clear()
            with self.lock:
                if self.active.get(lid) is sess:
                    del self.active[lid]

    def stop(self, lid: str) -> bool:
        with self.lock:
            sess = self.active.get(lid)
        if sess is None:
            return False
        sess["stop"].set()
        return True

    def stop_all(self) -> int:
        with self.lock:
            sids = list(self.active.keys())
        for lid in sids:
            self.stop(lid)
        return len(sids)

    def state(self) -> List[dict]:
        with self.lock:
            return [{"id": lid, "held": sorted(sess["held"])} for lid, sess in self.active.items()]
