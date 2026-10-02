"""mf-visualizer backend — Arturia MicroFreak panel state for the Hermes desktop pane.

State model (the device cannot report live values, so "state" is a merge of):
  1. the shared mf-mcp send log  -> everything WE send (Hermes tools, FL relay)
  2. a passive MIDI-IN listener   -> panel knob turns, when the MF is in MIDI Control mode
  3. optimistic UI moves          -> virtual knobs dragged in the pane while LIVE is off
All three funnel through one reducer (`State.apply_event`), keyed by CC number,
so last-write-wins is auditable and each value carries its provenance.

Routes mount under /api/plugins/mf-visualizer/ (FastAPI).
Env overrides: MF_MCP_SENDLOG, MF_MCP_PROFILE, MF_VIS_MOCK=1 (no MIDI at all).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

# Sibling module import (preset_codec) works no matter how the dashboard
# imports this file (importlib by path has no package context).
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))

from modengine import (ClipBank, ClipPlayer, ClipRecorder, LoopBank, LoopPlayer,  # noqa: E402
                      MacroBank, NoteRecorder)
from chordlab import ChordLab  # noqa: E402  (v0.6 F2.5 chord engine)
from stress import StressHarness  # noqa: E402  (v0.6 F0a diagnostics harness)
import theory as mftheory  # noqa: E402  (v0.6 F2 theory backend - the ONE home)
import registry as mfregistry  # noqa: E402  (v0.6.1 F1 registry v2 - control authority, fails loud)
from commands import ShadowState, CommandBus  # noqa: E402  (v0.6.1 F1 shadow state + TX command layer)
from smf import format1 as smf_format1  # noqa: E402

router = APIRouter()

# --------------------------------------------------------------------------
# Paths (identical defaults to mf-mcp so we share its audit log)
# --------------------------------------------------------------------------
SENDLOG = Path(os.environ.get(
    "MF_MCP_SENDLOG",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-mcp" / "sendlog.jsonl"),
))
PROFILE_PATH = Path(os.environ.get(
    "MF_MCP_PROFILE",
    str(Path(__file__).resolve().parent / "control-profile-v5.json"),
))
MATRIX_PATH = Path(os.environ.get(
    "MF_VIS_MATRIX",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "matrix.json"),
))
MACROS_PATH = Path(os.environ.get(
    "MF_VIS_MACROS",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "macros.json"),
))
TUTOR_PATH = Path(__file__).resolve().parent / "tutor.json"   # v0.6 F2b: grounded matrix knowledge
CLIPS_PATH = Path(os.environ.get(
    "MF_VIS_CLIPS",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "clips.json"),
))
COMMANDS_PATH = Path(os.environ.get(
    "MF_VIS_COMMANDS",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "command-history.json"),
))
SNAPS_PATH = Path(os.environ.get(
    "MF_VIS_SNAPS",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "command-snapshots.json"),
))
MOCK = os.environ.get("MF_VIS_MOCK") == "1"

PORT_HINT = "MicroFreak"  # same name-gate as mf-mcp transport: never a first-port fallback

# CC -> short display source labels
SOURCES = {"sendlog": "hermes", "relay": "fl", "panel": "panel", "ui": "ui", "visualizer": "hermes-live"}


def _log(*msg: str) -> None:
    print("[mf-visualizer]", *msg, flush=True)


# --------------------------------------------------------------------------
# Control profile (21 FW5 controls from mf-mcp)
# --------------------------------------------------------------------------
def load_profile() -> Dict[str, Any]:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return data.get("parameters", {}) or {}
    except Exception as exc:  # noqa: BLE001 - profile is advisory, pane has a built-in fallback
        _log("profile load failed:", exc)
        return {}


PARAMS = load_profile()
CC_TO_NAME: Dict[int, str] = {v["cc"]: k for k, v in PARAMS.items() if isinstance(v, dict) and "cc" in v}


# --------------------------------------------------------------------------
# v0.6.1 F1: commanded shadow state (master plan state law). The device never
# reports, so every CC carries provenance: `commanded` (we/our audit trail sent
# it), `observed` (panel echo or grounded preset read), `unknown`. An optimistic
# UI move (LIVE off, source "ui") grounds NOTHING - a value that never reached
# the wire must not masquerade as a command. Grounding runs from the single
# reducer (lane law: one choke point, per-callsite bookkeeping drifts).
# --------------------------------------------------------------------------
SHADOW = ShadowState()
_SHADOW_OBSERVED = {"panel", "preset"}


def _shadow_ground(cc: int, ev: Dict[str, Any]) -> None:
    val = ev.get("value")
    if val is None:
        return
    src = str(ev.get("source") or "unknown")
    if src in _SHADOW_OBSERVED:
        SHADOW.set_observed(cc, int(val), src)
    elif src == "ui":
        return                                    # optimistic, un-sent: no claim
    else:
        SHADOW.set_commanded(cc, int(val))        # visualizer/macro:/clip:/cmd:/relay/sendlog lanes


# --------------------------------------------------------------------------
# Central state
# --------------------------------------------------------------------------
class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()   # reentrant: v0.3.2 fixed a /state deadlock, this is the net under it
        self.seq = 0
        self.cc: Dict[int, Dict[str, Any]] = {}   # cc -> {value, source, ts, name}
        self.events: deque = deque(maxlen=400)     # ring of recent events (for the pane feed)
        self.notes: Dict[int, float] = {}          # active notes -> ts (for keyboard lighting)
        self.hold_notes: set = set()               # keyboard-hold latched notes (exempt from stuck-note sweep)
        self.panic_gen = 0                         # /panic bumps it; stale _note_gate timers check it before firing
        self._seeding = False                      # true only during startup replay: notes are moments, not memory
        self.program: Optional[int] = None
        self.bank = 0                                   # CC0 bank over the 4x128 slot map
        self.listening = False
        self.listen_error: Optional[str] = None
        self.live = False                          # outgoing CC sends allowed
        self.in_port: Optional[str] = None
        self.out_port: Optional[str] = None
        self._log_offset = 0
        self._listener: Optional["_MidiInListener"] = None
        self._out_port_obj = None
        self.current_preset: Optional[Dict[str, Any]] = None  # last import/capture
        self.preset_error: Optional[str] = None
        self.note_stats: Dict[int, Dict[str, Any]] = {}
        self.chord_stats: Dict[str, Dict[str, Any]] = {}
        self.pressure = 0                              # live channel-aftertouch 0..127
        self.pressure_ts = 0.0
        self._pressure_feed_at = 0.0
        self.read_session: Optional[Dict[str, Any]] = None  # active /preset/read transfer
        self.syx_log: deque = deque(maxlen=48)           # last frames in/out of the MF SysEx pipe

    # --- single reducer -------------------------------------------------
    def apply_event(self, ev: Dict[str, Any]) -> None:
        ev = dict(ev)
        ev.setdefault("ts", time.time())
        with self.lock:
            self.seq += 1
            ev["seq"] = self.seq
            kind = ev.get("kind")
            if kind == "cc" and "cc" in ev:
                cc = int(ev["cc"])
                prev = self.cc.get(cc)
                if prev and prev.get("value") == ev.get("value") and prev.get("source") == ev.get("source"):
                    prev["ts"] = ev["ts"]   # refresh recency, don't spam the feed
                    return
                self.cc[cc] = {
                    "value": ev.get("value"),
                    "source": ev.get("source", "unknown"),
                    "ts": ev["ts"],
                    "name": CC_TO_NAME.get(cc, f"cc{cc}"),
                }
                _shadow_ground(cc, ev)
            elif kind == "note":
                note = int(ev.get("note", -1))
                vel = int(ev.get("velocity", 0) or 0)
                if vel:
                    self.notes[note] = ev["ts"]
                    st = self.note_stats.setdefault(note, {
                        "count": 0, "avg_vel": 0.0, "max_vel": 0, "total_ms": 0.0,
                        "last_ts": 0.0, "last_vel": 0, "last_with": []})
                    st["count"] += 1
                    st["last_vel"] = vel
                    st["max_vel"] = max(st["max_vel"], vel)
                    st["avg_vel"] = st["avg_vel"] + (vel - st["avg_vel"]) / st["count"]
                    st["last_ts"] = ev["ts"]
                    others = sorted(self.notes)
                    st["last_with"] = [n for n in others if n != note]
                    # chord fingerprint: pitch classes sounding together at this attack
                    pcs = tuple(sorted({n % 12 for n in others} | {note % 12}))
                    key = ",".join(str(p) for p in pcs)
                    ch = self.chord_stats.setdefault(key, {"count": 0, "pcs": list(pcs),
                                                           "last_ts": 0.0, "top_note": note})
                    ch["count"] += 1
                    ch["last_ts"] = ev["ts"]
                    ch["top_note"] = note
                else:
                    self.hold_notes.discard(note)  # any real release clears the latch mark
                    on_ts = self.notes.pop(note, None)
                    if on_ts is not None and note in self.note_stats:
                        self.note_stats[note]["total_ms"] += (ev["ts"] - on_ts) * 1000.0
                    # releases carry no information the feed needs - one line per press
                    return
            elif kind == "program":
                self.program = int(ev.get("program", -1))
                if ev.get("bank") is not None:
                    self.bank = int(ev["bank"]) & 0x7F
            elif kind == "pressure":
                self.pressure = int(ev.get("value", 0))
                self.pressure_ts = ev["ts"]
                if self._pressure_feed_at and ev["ts"] - self._pressure_feed_at < 0.5:
                    return  # debounce the feed; the strip itself always updates
                self._pressure_feed_at = ev["ts"]
                ev = dict(ev)
            self.events.append(ev)

    # --- sendlog tail ---------------------------------------------------
    def tail_sendlog(self) -> None:
        """Pick up lines appended to the shared mf-mcp send log (Hermes + relay writes).

        Binary read + last-newline cutoff: v0.3.2 - the text-mode version consumed the
        offset PAST a partial trailing line (reader catching another process mid-append),
        so the completed line was skipped and lost from state forever.
        """
        try:
            if not SENDLOG.exists():
                return
            size = SENDLOG.stat().st_size
            if size < self._log_offset:      # truncated/rotated
                self._log_offset = 0
            with SENDLOG.open("rb") as fh:
                fh.seek(self._log_offset)
                data = fh.read()
            cut = data.rfind(b"\n")
            if cut < 0:
                return                       # no complete line yet: leave offset alone
            self._log_offset += cut + 1
            for raw in data[:cut].split(b"\n"):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue          # append-only audit store: skip malformed, never brick
                entry.setdefault("source", "relay" if entry.get("source") == "relay" else "sendlog")
                if self._seeding and entry.get("kind") not in ("cc", "program"):
                    continue   # v0.2.6b: replaying logged note-ons lit the WHOLE keyboard
                               # at startup (releases are never logged) - CC/program state
                               # persists, note/pressure state must not
                self.apply_event(entry)
        except OSError as exc:
            _log("sendlog tail error:", exc)

    def seed_sendlog_history(self) -> None:
        """Full parse once at startup so knob positions survive backend restarts."""
        try:
            if not SENDLOG.exists():
                return
            self._log_offset = 0
            self._seeding = True
            try:
                self.tail_sendlog()
            finally:
                self._seeding = False
        except Exception as exc:  # noqa: BLE001
            _log("sendlog seed failed:", exc)

    def sweep_stuck(self, now: float) -> None:
        """Stuck notes must not light keys forever; hold-latched notes are deliberate.
        Callers must hold self.lock. Runs from BOTH the delta pump and every /state read:
        _pump only exists while a WebSocket connects, and the watchdog-polling pane has
        no socket - v0.2.6b: without the /state call the sweep never ran on that path."""
        for n in [k for k, v in self.notes.items()
                  if now - v > 30 and k not in self.hold_notes]:
            self.notes.pop(n, None)


STATE = State()


def _read_tick(now: float) -> None:
    """Expire a stalled /preset/read (one op-1C retry, then honest timeout error).
    v0.3.2: lived ONLY in the WS pump before - and the OAuth renderer never opens a
    socket, so a silent device meant 'reading' stuck forever on the polling pane."""
    sess = STATE.read_session
    if sess is None or now <= sess["deadline"]:
        return
    if sess.get("got", 0) == 0 and not sess.get("retried"):
        sess["retried"] = True
        sess["deadline"] = now + 4.0
        try:
            _send_sysex(0x1C, b"\x01")   # MCC opens sessions with 1C before touching anything
            _send_sysex(0x19, bytes((sess["bank"], sess["prog"], 0)))
        except Exception:  # noqa: BLE001
            STATE.read_session = None
    else:
        STATE.read_session = None
        STATE.preset_error = (f"read slot {sess['slot']} timed out at stage "
                              f"'{sess['stage']}' - {sess.get('got', 0)} SysEx frames "
                              "came back from the device")


def _pressure_tick(now: float) -> None:
    """Strip decays when the panel falls silent (also pump-independent since v0.3.2)."""
    if now - STATE.pressure_ts > 0.6 and STATE.pressure:
        STATE.apply_event({"kind": "pressure", "value": 0, "source": "panel", "ts": now})


# --------------------------------------------------------------------------
# Passive MIDI-IN listener (panel knob turns in MIDI Control mode)
# --------------------------------------------------------------------------
class _MidiInListener(threading.Thread):
    def __init__(self, port_name: str) -> None:
        super().__init__(daemon=True, name="mf-visualizer-midiin")
        self.port_name = port_name
        self._stop = threading.Event()
        self._syx_header: Optional[bytes] = None
        self._syx_parts: List[bytes] = []

    def run(self) -> None:  # pragma: no cover - depends on hardware
        try:
            import mido
            port = mido.open_input(self.port_name, callback=self._on_msg)
        except Exception as exc:  # noqa: BLE001
            STATE.listening = False
            STATE.listen_error = f"{type(exc).__name__}: {exc}"
            _log("listener open failed:", exc)
            return
        try:
            while not self._stop.is_set():
                self._stop.wait(0.25)
        finally:
            try:
                port.close()
            except Exception:  # noqa: BLE001
                pass

    def _on_msg(self, msg) -> None:  # rtmidi callback thread
        t = msg.type
        if t == "sysex":
            self._on_sysex(bytes(msg.data))
        elif t == "control_change":
            STATE.apply_event({"kind": "cc", "cc": msg.control, "value": msg.value, "source": "panel"})
        elif t in ("note_on", "note_off"):
            STATE.apply_event({"kind": "note", "note": msg.note,
                               "velocity": msg.velocity if t == "note_on" else 0,
                               "source": "panel"})
        elif t == "program_change":
            STATE.apply_event({"kind": "program", "program": msg.program, "source": "panel"})
        elif t == "channel_aftertouch":
            STATE.apply_event({"kind": "pressure", "value": msg.value, "source": "panel"})

    def stop(self) -> None:
        self._stop.set()

    # --- SysEx capture / request-driven preset read -----------------------
    # Envelope: F0 00 20 6B 07 01 SS LL OP [LL payload bytes] F7.
    # Read flow (hardware-verified protocol, captured from MIDI Control Center):
    #   19 [bank,prog,0] -> 52 header(35B)
    #   19 [bank,prog,1] -> 15 transfer-start
    #   18 [00]          -> 16 part(32B)   x145, then one more 18 -> 17 final part
    def _on_sysex(self, data: bytes) -> None:
        if len(data) < 10 or data[0] != 0xF0 or data[-1] != 0xF7:
            return
        if data[1:4] != b"\x00\x20\x6b" or data[4] != 0x07:
            return
        op = data[8]
        payload = data[9:-1]
        sess = STATE.read_session
        STATE.syx_log.append(f"in {op:02X} {len(payload)}B")   # debug lane: what the device ACTUALLY says
        if sess is not None:
            sess["got"] = sess.get("got", 0) + 1
        if op == 0x52 and len(payload) >= 35:
            self._syx_header = bytes(payload[:35])
            self._syx_parts = []
            if sess is not None:
                sess["deadline"] = time.time() + 4.0
                sess["stage"] = "header"
                if payload[3] & 0x08:            # Init/empty slot: header is all
                    _finish_read(sess, self._syx_header, b"")
                else:
                    _send_sysex(0x19, bytes((sess["bank"], sess["prog"], 1)))
                    sess["stage"] = "start"
        elif op == 0x15 and sess is not None and sess["stage"] == "start":
            sess["stage"] = "parts"
            sess["deadline"] = time.time() + 4.0
            self._syx_parts = []
            _send_sysex(0x18, b"\x00")
        elif op in (0x16, 0x17) and self._syx_header is not None:
            if len(payload) >= 32:
                self._syx_parts.append(bytes(payload[:32]))
            if sess is not None:
                sess["deadline"] = time.time() + 4.0
                if len(self._syx_parts) >= 146:
                    _finish_read(sess, self._syx_header, b"".join(self._syx_parts[:146]))
                    self._syx_parts = []
                    self._syx_header = None
                else:
                    _send_sysex(0x18, b"\x00")
            elif len(self._syx_parts) >= 146:      # passive full dump (e.g. MCC)
                _ingest_preset_dump(self._syx_header, b"".join(self._syx_parts[:146]))
                self._syx_parts = []
                self._syx_header = None


_SYX_SEQ = 0
_OUT_LOCK = threading.Lock()


def _send_sysex(op: int, payload: bytes) -> None:
    """Emit one protocol frame on the MicroFreak OUT port (sequence auto-increments)."""
    global _SYX_SEQ
    import mido
    with _OUT_LOCK:                     # the lock also serializes the counter: single sender
        _SYX_SEQ = (_SYX_SEQ + 1) & 0x7F
        body = [_SYX_SEQ, len(payload) + 1, op, *[b & 0x7F for b in payload]]
        _ensure_out().send(mido.Message("sysex", data=(0x00, 0x20, 0x6B, 0x07, 0x01, *body)))


def _finish_read(sess: Dict[str, Any], header: Optional[bytes], body: bytes) -> None:
    """A requested read completed (or an empty Init header arrived): ingest + clear."""
    STATE.read_session = None
    _ingest_preset_dump(header, body, slot=sess["slot"])


def _ingest_preset_dump(header: Optional[bytes], body: bytes, slot: Optional[int] = None) -> None:
    """A complete 4672-byte preset arrived over MIDI-IN: decode + light the panel.

    Read-only by design: we mirror the preset's knob positions into the visual
    state; we never write back unless the user hits a control with LIVE on.
    """
    try:
        from preset_codec import assign_labels, matrix_from_fields, parse_structured, to_cc_state
        fields = parse_structured(body) if body else []
        if body and not fields:
            STATE.preset_error = "preset body is not FW5-tagged (legacy firmware?)"
            return
        name = ""
        derived_slot = slot
        if header and len(header) >= 26:
            name = bytes(b for b in header[12:26] if b).decode("latin-1", "replace")
            if derived_slot is None:
                derived_slot = header[0] * 128 + header[1] + 1
            if header[3] & 0x08:
                name = name or "Init"
        with STATE.lock:
            STATE.current_preset = {
                "name": name, "fields": len(fields),
                "matrix": matrix_from_fields(fields),
                "assigns": assign_labels(fields),
                "slot": derived_slot,
                "empty": bool(header and len(header) > 3 and header[3] & 0x08),
            }
        STATE.preset_error = None
        for cc, meta in to_cc_state(fields).items():
            STATE.apply_event({"kind": "cc", "cc": cc, "value": meta["value"], "source": "preset"})
        if derived_slot is not None:
            STATE.apply_event({"kind": "program", "program": (derived_slot - 1) & 0x7F,
                               "bank": (derived_slot - 1) >> 7, "source": "preset"})
        STATE.apply_event({"kind": "preset", "name": name, "source": "panel"})
    except Exception as exc:  # noqa: BLE001 - capture must never kill the listener
        STATE.preset_error = f"{type(exc).__name__}: {exc}"
        _log("preset ingest failed:", exc)


def _find(name_part: str, ports: List[str]) -> Optional[str]:
    hits = [p for p in ports if name_part.lower() in p.lower()]
    return hits[0] if hits else None


# --------------------------------------------------------------------------
# Outgoing CC (LIVE mode) — deliberately narrow: allowlisted FW5 CCs only
# --------------------------------------------------------------------------
def _ensure_out():
    import mido
    if STATE._out_port_obj is not None:
        return STATE._out_port_obj
    out = _find(PORT_HINT, mido.get_output_names())
    if not out:
        raise RuntimeError("no MicroFreak output port (device off/unplugged?) - refusing fallback")
    STATE._out_port_obj = mido.open_output(out)
    STATE.out_port = out
    return STATE._out_port_obj


class CcBodyError(Exception):
    pass


@router.get("/state")
async def get_state(since: int = 0) -> Dict[str, Any]:
    STATE.tail_sendlog()
    RECORDER.poll()      # v0.4: keep the arm->stop accumulator ahead of the 400-event ring
    LOOP_REC.poll()      # v0.5: the note recorder reads the same ring - same law
    now = time.time()
    _read_tick(now)        # polling path must self-heal too (see helper docstrings)
    _pressure_tick(now)
    spec = _spectrum_payload()   # v0.3.2 CRITICAL: model bands take STATE.lock themselves;
    mat = load_matrix()          # building them INSIDE the lock below was a hard deadlock
    macs = MACRO_BANK.state()    # v0.4: same law — mod lanes take their OWN locks; never
    clips = CLIP_BANK.summaries()# nest them inside STATE.lock (macro move() locks STATE
    playing = CLIP_PLAYER.state()  # while holding the bank lock -> inversion deadlock
    loops_ = LOOP_BANK.summaries()   # v0.5 loop studio: OWN locks only - same law
    loops_playing = LOOP_PLAYER.state()
    ports_in, ports_out = _ports("input"), _ports("output")   # (non-reentrant Lock) on every poll
    chord_lab = CHORD_LAB.state()   # v0.6: own lock, computed OUTSIDE STATE.lock (same law)
    diag_ = STRESS.state()          # v0.6: harness readout, own lock, never inside STATE.lock
    shadow_ = {str(cc): e for cc, e in SHADOW.items()}   # v0.6.1 F1: shadow's OWN lock - same law
    cmds_ = {"depth": len(COMMANDS.history), "redo": bool(COMMANDS.redo_stack),
             "streams": COMMANDS.stream_count,
             "load_error": COMMANDS.load_error, "save_error": COMMANDS.save_error}
    with STATE.lock:
        STATE.sweep_stuck(now)
        feed = [e for e in STATE.events if e.get("seq", 0) > since][-80:]
        return {
            "api": 4,                                   # pane uses this to detect a stale (pre-WS) backend
            "seq": STATE.seq,
            "cc": STATE.cc,
            "events": feed,
            "active_notes": sorted(STATE.notes),
            "program": STATE.program,
            "bank": STATE.bank,
            "listening": STATE.listening,
            "listen_error": STATE.listen_error,
            "live": STATE.live,
            "in_port": STATE.in_port,
            "out_port": STATE.out_port,
            "mock": MOCK,
            "params": PARAMS,
            "ports_in": ports_in,
            "ports_out": ports_out,
            "matrix": mat,
            "sendlog": str(SENDLOG),
            "preset": STATE.current_preset,
            "preset_error": STATE.preset_error,
            "syx": list(STATE.syx_log),
            "note_stats": {str(n): st for n, st in STATE.note_stats.items()},
            "chords": sorted(STATE.chord_stats.values(), key=lambda c: -c["count"])[:12],
            "pressure": STATE.pressure,
            "reading": STATE.read_session is not None,
            "spectrum": spec,
            "macros": macs,                              # v0.4 mod lanes (computed OUTSIDE the lock)
            "clips": clips,
            "clips_playing": playing,
            "recording": RECORDER.armed,
            "loops": loops_,                                  # v0.5 loop studio
            "loops_playing": loops_playing,
            "loop_rec": LOOP_REC.snapshot(),                  # armed recorder (name/over target) or None
            "chord": chord_lab,                               # v0.6 Chord Lab (held chord/label)
            "diag": {"running": diag_["running"], "run": diag_["run"],
                     "rate_hz_now": diag_["rate_hz_now"], "counters": diag_["counters"],
                     "sendlog_rows": diag_["sendlog_rows"]},  # v0.6 F0a harness readout
            "shadow": shadow_,                               # v0.6.1 F1 commanded|observed|unknown law
            "commands": cmds_,                               # v0.6.1 F1 history/undo availability
            "theory": "mftheory/1",
        }


@router.get("/spectrum")
async def get_spectrum() -> Dict[str, Any]:
    # v0.4.2 FAST analyzer feed: the pane polls THIS at ~16 Hz instead of riding
    # spectrum on the heavy 4 Hz /state. Lock law (v0.3.2): _spectrum_payload()
    # takes STATE.lock only in model mode — never call this while holding any
    # other lock, and never nest it inside STATE.lock.
    return _spectrum_payload()


# ---------------------------------------------------------------------------
# v0.3.0 spectrum analyzer. The MicroFreak speaks analog audio; the PC hears it
# only if the signal path crosses the machine (speakers -> WASAPI loopback, or
# line-in/interface -> capture input). We enumerate loopback + inputs at boot,
# prefer MF_VIS_AUDIO_DEVICE (substring match) then default-output loopback.
# If nothing yields samples, /state reports model=True with MIDI-implied bands
# so the pane can be honest about what it is drawing.
N_BANDS = 64
# v0.4.4 SPECTRUM ACCURACY: true dBFS scale, tunable via env. A full-scale sine
# now reads ~1.0, -60 dBFS reads ~0.33, silence reads 0 - the old unnormalized
# (db+88)/80 mapping pinned ~everything at 1.0 (owner: "its just loud").
SPEC_FLOOR_DB = float(os.environ.get("MF_VIS_SPEC_FLOOR", "-90"))
SPEC_SPAN_DB = float(os.environ.get("MF_VIS_SPEC_SPAN", "90"))
# v0.5.1: seconds for the DISPLAY to fall from full scale to zero. Fixed time in
# the display domain, so the recede rate no longer depends on FFT frame rate.
# v0.5.1a (owner, 2026-09-24: "snail's pace... it needs to be SNAPPY"): 0.45 ->
# 0.2 s. The 0.5 s ceiling still holds with frame-grid margin: 0.2 s on the
# 46.44 ms grid clears at 5 frames = 0.232 s. Tune without a rebuild via
# MF_VIS_SPEC_RELEASE (test pins: <=0.5 ceiling, >=0.2 implausibly-fast floor).
SPEC_RELEASE_S = float(os.environ.get("MF_VIS_SPEC_RELEASE", "0.2"))
SPEC_RELEASE_PER_S = 1.0 / max(0.05, SPEC_RELEASE_S)
AUDIO_LOCK = threading.Lock()
AUDIO: Dict[str, Any] = {"src": None, "bands": [0.0] * N_BANDS, "t": 0.0, "err": None, "hz": 0}

def _band_edges() -> List[float]:
    lo, hi, n = 24.0, 20000.0, N_BANDS
    import math
    return [lo * (hi / lo) ** (i / n) for i in range(n + 1)]

def _model_bands() -> List[float]:
    """MIDI-implied spectrum: harmonic stacks for active notes. A model, not a
    measurement - the pane labels it as such wherever it renders it."""
    import bisect
    edges = _band_edges()
    bands = [0.0] * N_BANDS
    with STATE.lock:
        active = list(STATE.notes.keys())
    for n in active:
        f = 440.0 * 2.0 ** ((n - 69) / 12.0)
        h = 1
        while h < 28:
            fh = f * h
            if fh >= edges[-1]:
                break
            i = max(0, bisect.bisect_right(edges, fh) - 1)
            bands[i] = min(1.0, bands[i] + (0.85 ** h) / h)
            h += 1
    return [round(b, 3) for b in bands]

def _audio_worker() -> None:  # pragma: no cover - depends on audio hardware
    try:
        import numpy as np
        import pyaudiowpatch as pa
    except Exception as e:
        with AUDIO_LOCK:
            AUDIO["err"] = f"deps:{type(e).__name__}"
        return
    try:
        p = pa.PyAudio()
        want = (os.environ.get("MF_VIS_AUDIO_DEVICE") or "").strip().lower()
        info = None
        cands = list(p.get_loopback_device_info_generator())
        if want:
            info = next((d for d in cands if want in str(d.get("name", "")).lower()), None)
            if info is None:
                info = next((d for d in p.get_device_info_generator()
                             if not d.get('isLoopbackDevice') and int(d.get('maxInputChannels', 0)) > 0
                             and want in str(d.get('name', '')).lower()), None)
        if info is None:
            try:
                info = p.get_default_wasapi_loopback()
            except Exception:
                info = None
        if info is None and cands:
            info = cands[0]
        if info is None:
            AUDIO["err"] = "no-loopback-device"
            return
        ch = max(1, int(info["maxInputChannels"]))
        rate = int(info["defaultSampleRate"])
        block = 2048
        stream = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                        frames_per_buffer=block, input_device_index=int(info["index"]))
        kind = "loopback" if info.get("isLoopbackDevice") else "in"
        with AUDIO_LOCK:
            AUDIO["src"] = f"{kind}:{info['name']}"
            AUDIO["hz"] = rate
            AUDIO["err"] = None
        # v0.4.4 fixes three lies in one: hanning(2N)[:N] was a RISING RAMP (smeared
        # spectrum), raw rfft power has +50ish dB offsets (everything pinned), and
        # zero time-integration made 23 fps frames read as hyperactivity. Now:
        # full Hann + coherent-gain normalization -> true dBFS power.
        # v0.5.1 ballistics fix (owner: "after a frequency is reached it goes down
        # too slowly ... no more than half a second until it goes back to 0").
        # OLD LAW, WRONG: k=0.06 release smoothed in the POWER domain. Exponential
        # power decay = LINEAR dB decay of 10*log10(1/0.94) = 0.269 dB/frame, i.e.
        # ~5.8 dB/s, so a 90 dB display window took ~15 s to clear no matter how
        # the pane smoothed it. The pane (tau=0.15 s) was never the slow part.
        # NEW LAW: the fast power follower exists ONLY to kill frame jitter; the
        # displayed value rises instantly and falls LINEARLY in the display domain
        # at SPEC_RELEASE_PER_S, so full-scale -> 0 is exactly SPEC_RELEASE_S
        # (default 0.2 s, owner "SNAPPY" ruling) and any lower peak clears
        # sooner. Fall time is now a
        # property of the display, not of the FFT frame rate.
        win = np.hanning(block).astype(np.float32)
        cg2 = float(win.sum() / 2.0) ** 2
        edges = np.asarray(_band_edges())
        avg = np.zeros(N_BANDS)    # power follower: jitter control only
        disp = np.zeros(N_BANDS)   # display ballistics: instant rise, timed fall
        last_t = time.time()
        while True:
            try:
                x = np.frombuffer(stream.read(block, exception_on_overflow=False), dtype=np.float32)
                if ch > 1:
                    x = x.reshape(-1, ch).mean(axis=1)   # standard 0.5L+0.5R downmix
                spec = np.abs(np.fft.rfft(x * win[: len(x)])) ** 2 / cg2
                freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
                idx = np.clip(np.searchsorted(edges, freqs, side="right") - 1, 0, N_BANDS - 1)
                sums = np.bincount(idx, weights=spec, minlength=N_BANDS)
                cnts = np.bincount(idx, minlength=N_BANDS).astype(float)
                power = np.where(cnts > 0, sums / np.maximum(cnts, 1.0), 0.0)
                k = np.where(power > avg, 0.5, 0.75)
                avg = avg + (power - avg) * k
                norm = np.clip((10.0 * np.log10(avg + 1e-14) - SPEC_FLOOR_DB) / SPEC_SPAN_DB, 0.0, 1.0)
                now = time.time()
                dt = min(0.25, max(1e-3, now - last_t))
                last_t = now
                disp = _spec_ballistics(disp, norm, dt)
                with AUDIO_LOCK:
                    AUDIO["bands"] = [round(float(v), 3) for v in disp]
                    AUDIO["t"] = now
            except Exception as e:
                with AUDIO_LOCK:
                    AUDIO["err"] = f"read:{type(e).__name__}:{e}"
                time.sleep(2.0)
    except Exception as e:
        with AUDIO_LOCK:
            AUDIO["err"] = f"init:{type(e).__name__}:{e}"
            AUDIO["src"] = None

def _dbfs_norm(power: float) -> float:
    """Scalar twin of the worker's band mapping (unit-testable, numpy-free).
    `power` is mean squared AMPLITUDE normalized so a full-scale sine = 1.0."""
    import math
    db = 10.0 * math.log10(max(float(power), 1e-14))
    return max(0.0, min(1.0, (db - SPEC_FLOOR_DB) / SPEC_SPAN_DB))


def _spec_ballistics(prev, norm, dt, release_per_s: float | None = None):
    """One display-ballistics step: instant rise, LINEAR fall, floored at 0.

    Works on scalars or numpy arrays. The owner spec (v0.5.1) is that a band
    which has reached a peak is back to 0 within SPEC_RELEASE_S (default 0.2 s),
    so the fall is timed in the DISPLAY domain. The old law smoothed in the
    power domain, where exponential decay is linear in dB at only ~5.8 dB/s -
    a 90 dB window therefore took ~15 s to clear (see test_spectrum.py)."""
    r = SPEC_RELEASE_PER_S if release_per_s is None else float(release_per_s)
    d = max(0.0, float(dt))
    fall = r * d
    if isinstance(prev, (int, float)) and isinstance(norm, (int, float)):
        return max(float(norm), float(prev) - fall)      # scalar path: numpy-free
    import numpy as np                                   # array path (worker's law)
    return np.maximum(np.asarray(norm, dtype=float), np.asarray(prev, dtype=float) - fall)


def _spectrum_payload() -> Dict[str, Any]:
    with AUDIO_LOCK:
        src_, bands_, t_, err_, hz_ = AUDIO["src"], list(AUDIO["bands"]), AUDIO["t"], AUDIO["err"], AUDIO["hz"]
    if src_ and time.time() - t_ < 1.5:
        return {"src": src_, "bands": bands_, "model": False, "err": err_, "hz": hz_}
    return {"src": "midi-model", "bands": _model_bands(), "model": True, "err": err_, "hz": 0}

_PORTS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {"input": [], "output": []}}


def _ports(kind: str) -> List[str]:
    if MOCK:
        return []
    # rtmidi enumeration costs tens of ms per call - it ran twice per 700ms poll
    # and was a visible share of the pane's lag. Cache for 5s.
    now = time.time()
    if now - _PORTS_CACHE["ts"] > 5.0:
        try:
            import mido
            _PORTS_CACHE["data"] = {
                "input": mido.get_input_names() or [],
                "output": mido.get_output_names() or [],
            }
        except Exception:  # noqa: BLE001
            _PORTS_CACHE["data"] = {"input": [], "output": []}
        _PORTS_CACHE["ts"] = now
    return _PORTS_CACHE["data"].get(kind, [])


@router.post("/listen")
async def set_listen(body: Dict[str, Any]) -> Dict[str, Any]:
    enable = bool(body.get("enabled", True))
    if MOCK:
        STATE.listening = enable
        return {"ok": True, "listening": enable, "mock": True}
    if enable:
        if STATE.listening:
            return {"ok": True, "listening": True}
        inp = _find(PORT_HINT, _ports("input"))
        if not inp:
            STATE.listen_error = "no MicroFreak input port found"
            return {"ok": False, "error": STATE.listen_error}
        STATE.listen_error = None
        STATE._listener = _MidiInListener(inp)
        STATE._listener.start()
        STATE.listening = True
        STATE.in_port = inp
    else:
        if STATE._listener:
            STATE._listener.stop()
            STATE._listener = None
        STATE.listening = False
    return {"ok": True, "listening": STATE.listening, "in_port": STATE.in_port,
            "error": STATE.listen_error}


@router.post("/preset/read")
async def read_preset(body: Dict[str, Any]) -> Dict[str, Any]:
    """Request the SAVED preset in slot N over SysEx (op 19 -> header/body/parts).

    Non-destructive read (device transmits; we never write here), but it does
    put bytes on the wire, so it requires LIVE armed + a real device. The read
    mirrors the SAVED slot: if you have unsaved panel edits, press SAVE on the
    device first or the picture is the pre-edit state.
    """
    if MOCK:
        return {"ok": True, "mock": True, "note": "mock: no transfer"}
    if not STATE.live:
        STATE.preset_error = "Read needs LIVE on (it puts a request on the wire)"
        return {"ok": False, "error": STATE.preset_error}
    try:
        # v0.3.2: a 512-slot device addresses slot = bank*128 + program + 1; the old
        # default ignored bank and happily read the wrong preset from bank 0.
        slot = int(body.get("slot",
                            (STATE.bank or 0) * 128 + (STATE.program + 1 if STATE.program is not None else 1)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "slot must be an int"}
    if not 1 <= slot <= 512:
        return {"ok": False, "error": "slot out of range 1..512"}
    if not STATE.listening:
        await set_listen({"enabled": True})
    if not STATE.listening:
        return {"ok": False, "error": STATE.listen_error or "MIDI IN listener could not start"}
    if STATE.read_session is not None:
        return {"ok": False, "error": "a read is already in flight"}
    bank, prog = divmod(slot - 1, 128)
    STATE.preset_error = None
    STATE.read_session = {"slot": slot, "bank": bank, "prog": prog, "got": 0,
                          "stage": "header-req", "deadline": time.time() + 4.0}
    try:
        _send_sysex(0x19, bytes((bank, prog, 0)))
    except Exception as exc:  # noqa: BLE001
        STATE.read_session = None
        return {"ok": False, "error": f"send failed: {exc}"}
    return {"ok": True, "slot": slot}


@router.post("/live")
async def set_live(body: Dict[str, Any]) -> Dict[str, Any]:
    enable = bool(body.get("enabled", False))
    if not enable:
        STATE.live = False
        try:
            if STATE._out_port_obj is not None:
                STATE._out_port_obj.close()
        except Exception:  # noqa: BLE001
            pass
        STATE._out_port_obj = None
        return {"ok": True, "live": False}
    if MOCK:
        STATE.live = True
        return {"ok": True, "live": True, "mock": True}
    try:
        _ensure_out()
        STATE.live = True
        STATE.listen_error = None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "live": True, "out_port": STATE.out_port}


@router.post("/cc")
async def send_cc(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = int(body.get("value", -1))
    except (TypeError, ValueError):
        return {"ok": False, "error": "value must be an int"}
    if value < 0 or value > 127:
        return {"ok": False, "error": "value out of range 0..127"}
    cc = body.get("cc")
    if cc is None and body.get("name"):
        meta = PARAMS.get(body["name"])
        cc = meta["cc"] if isinstance(meta, dict) and "cc" in meta else None
    if cc is None or int(cc) not in CC_TO_NAME:
        return {"ok": False, "error": f"{body.get('name') or body.get('cc')} is not in the FW5 allowlist - refusing"}
    cc = int(cc)
    value = max(0, min(127, value))
    ev = {"kind": "cc", "cc": cc, "value": value,
          "source": "visualizer" if STATE.live else "ui"}
    if STATE.live and not MOCK:
        try:
            import mido
            port = _ensure_out()
            port.send(mido.Message("control_change", control=cc, value=value, channel=0))
        except Exception as exc:  # noqa: BLE001
            STATE.live = False
            SHADOW.invalidate("disconnect")   # v0.6.1 F1: device walked away mid-send
            return {"ok": False, "error": f"send failed, LIVE disabled: {exc}"}
        ev["source"] = "visualizer"
        _append_sendlog(ev)
    STATE.apply_event(ev)
    return {"ok": True, "sent": STATE.live, "cc": cc, "value": value}


def _append_sendlog(ev: Dict[str, Any]) -> None:
    """Mirror live sends into the shared mf-mcp send log (same JSONL schema).
    v0.4: honors the event's own source tag (macro:<id>/clip:<id>); the hardcoded
    "visualizer" stays as the default for everything that predates the mod lanes."""
    try:
        SENDLOG.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": ev.get("ts", time.time()), "kind": ev.get("kind", "cc"),
                "source": ev.get("source") or "visualizer"}
        for k in ("cc", "value", "note", "velocity", "duration_ms", "program"):
            if k in ev:
                line[k] = ev[k]
        with SENDLOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError as exc:
        _log("sendlog append failed:", exc)


# ---------------------------------------------------------------------------
# v0.4 mod lanes: macro bank + clip studio (engines live in modengine.py;
# everything reaching the wire passes through ONE sink below, so the FW5
# allowlist, LIVE gate, sendlog provenance and reducer apply stay law).
# ---------------------------------------------------------------------------
ALLOWED_CCS = set(CC_TO_NAME) or {2, 5, 9, 10, 12, 13, 23, 24, 26, 28, 29, 64,
                                  83, 91, 92, 93, 94, 102, 103, 105, 106}


def _engine_send(cc: int, value: int, source: str) -> bool:
    """Sink for macro/clip streams. True ONLY when the value actually reached
    the device (or mock claims it): bank coalescers then trust their echo.
    LIVE off => nothing sends, nothing fakes; send exception => LIVE drops,
    the same contract the /cc route has always kept."""
    with STATE.lock:
        if not STATE.live:
            STATE.cc[int(cc)] = {"value": int(value), "source": source,
                                 "ts": time.time(), "name": CC_TO_NAME.get(int(cc), f"cc{cc}")}
            return False
    if MOCK:
        STATE.apply_event({"kind": "cc", "cc": int(cc), "value": int(value), "source": source})
        return True
    try:
        import mido
        _ensure_out().send(mido.Message("control_change", control=int(cc), value=int(value), channel=0))
    except Exception as exc:  # noqa: BLE001
        STATE.live = False
        # v0.6.1 F1: a mid-stream send failure means the device walked away -
        # everything we thought we commanded is stale until re-grounded.
        SHADOW.invalidate("disconnect")
        _log(f"{source} send failed, LIVE disabled: {exc}")
        return False
    STATE.apply_event({"kind": "cc", "cc": int(cc), "value": int(value), "source": source})
    _append_sendlog({"kind": "cc", "cc": int(cc), "value": int(value),
                     "source": source, "ts": time.time()})
    return True


def _events_since(seq0: int) -> List[dict]:
    with STATE.lock:
        return [e for e in STATE.events if int(e.get("seq", 0)) > seq0]


MACRO_BANK = MacroBank(MACROS_PATH, ALLOWED_CCS, _engine_send, _log)
CLIP_BANK = ClipBank(CLIPS_PATH, ALLOWED_CCS, _log)
RECORDER = ClipRecorder(_events_since, lambda: STATE.seq, _log)
CLIP_PLAYER = ClipPlayer(CLIP_BANK, _engine_send, lambda: STATE.panic_gen, _log,
                         live_fn=lambda: STATE.live, mock=MOCK)

# v0.6.1 F1: the TX command bus. Egress is _engine_send itself - the LIVE gate,
# FW5 allowlist and sendlog law stay in the ONE sink; the bus adds per-send
# outcomes, the commanded-shadow stamping and the coalescing history on top.
COMMANDS = CommandBus(SHADOW, _engine_send, history_path=str(COMMANDS_PATH))


@router.post("/note")
async def play_note(body: Dict[str, Any]) -> Dict[str, Any]:
    try:                                # v0.3.2: the endpoint is localhost-open; garbage in
        note = int(body.get("note"))    # used to poison state (note 999) or crash mid-send
        velocity = int(body.get("velocity", 96))
    except (TypeError, ValueError):
        return {"ok": False, "error": "note must be an int, velocity an int"}
    if not 0 <= note <= 127 or not 0 <= velocity <= 127:
        return {"ok": False, "error": "note/velocity out of range 0..127"}
    duration_ms = max(30, min(60000, int(body.get("duration_ms", 300) or 300)))
    if velocity == 0:  # explicit release (keyboard-hold latch toggle-off)
        STATE.hold_notes.discard(note)
        STATE.apply_event({"kind": "note", "note": note, "velocity": 0, "source": "ui"})
        if STATE.live and not MOCK:
            try:
                import mido
                _ensure_out().send(mido.Message("note_off", note=note, velocity=0, channel=0))
                _append_sendlog({"kind": "note", "note": note, "velocity": 0,
                                 "ts": time.time(), "source": "visualizer"})
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        return {"ok": True, "released": True}
    STATE.apply_event({"kind": "note", "note": note, "velocity": velocity, "source": "ui"})
    sent = False
    if STATE.live and not MOCK:
        try:
            import mido
            port = _ensure_out()
            port.send(mido.Message("note_on", note=note, velocity=velocity, channel=0))
            sent = True
            _append_sendlog({"kind": "note", "note": note, "velocity": velocity,
                             "duration_ms": duration_ms, "ts": time.time(),
                             "source": "visualizer"})
        except Exception as exc:  # noqa: BLE001
            STATE.live = False
            STATE.apply_event({"kind": "note", "note": note, "velocity": 0, "source": "ui"})
            return {"ok": False, "error": str(exc)}
    if body.get("hold") and sent:
        # keyboard-hold latch: NO release timer, no auto note-off - the
        # device sustains and the light stays until an explicit velocity-0.
        # v0.2.6b: only while the note actually went to hardware (sent) -
        # a latch with LIVE off is a light with no audio behind it; not-live
        # requests fall through to the normal timer release instead.
        STATE.hold_notes.add(note)
        return {"ok": True, "sent": sent, "hold": True}
    # gate the response on nothing: release happens in a background timer thread
    # (NOT an asyncio task: the shared loop only runs during requests, so a
    # task created here never fires between calls)
    threading.Timer(max(0.03, duration_ms / 1000.0),
                    _note_gate, args=(note, sent, STATE.panic_gen)).start()
    return {"ok": True, "sent": sent}


@router.post("/panic")
async def panic(body: Dict[str, Any]) -> Dict[str, Any]:
    """v0.3.1 Class B kill switch - documented messages ONLY: note_off for every
    tracked/latched note (+0..127 when body.all) and CC64->0. No CC123 all-notes-off
    (not in the documented 21, unverified on device - see synth-mod-catalog). The
    device's own arp/seq has no documented stop and keeps running; the response says
    so instead of pretending. NOTE: no `with STATE.lock` here - apply_event locks."""
    STATE.panic_gen += 1                        # void pending release timers FIRST
    stopped = CLIP_PLAYER.stop_all()            # v0.4: running clip transports die here too;
    stopped_loops = LOOP_PLAYER.stop_all()      # v0.5: loop transports die here too - their
    chords = CHORD_LAB.release_all()            # v0.6: a held Chord Lab chord is lifted here
    stress = STRESS.stop(why="panic") if STRESS.running() else None   # v0.6: a running
    MACRO_BANK.reset_hardware_echo()            # diagnostic run is cancelled by PANIC too.
                                                # the gen-check makes their threads self-void,
                                                # and the echo reset re-sticks faders on next move
    all_notes = bool(body.get("all"))
    with STATE.lock:                            # v0.3.2: snapshot under the lock (the panel
        targets = sorted(set(STATE.notes) | STATE.hold_notes  # listener can note-on mid-iteration
                         | (set(range(128)) if all_notes else set()))
    live = STATE.live and not MOCK
    sent, err = 0, None
    if live:
        try:
            import mido
            port = _ensure_out()
            port.send(mido.Message("control_change", control=64, value=0, channel=0))
            _append_sendlog({"kind": "cc", "cc": 64, "value": 0, "ts": time.time(), "source": "visualizer"})
            for n in targets:
                await asyncio.sleep(0.02)       # runtime_policy minimum_inter_message_delay_ms
                port.send(mido.Message("note_off", note=n, velocity=0, channel=0))
                _append_sendlog({"kind": "note", "note": n, "velocity": 0, "ts": time.time(), "source": "visualizer"})
                sent += 1
        except Exception as exc:  # noqa: BLE001
            STATE.live = False
            err = f"panic send failed, LIVE disabled: {exc}"
            # v0.3.2: never return before cleaning state - a kill switch that leaves the
            # pane lit and latches set after a failed send is worse than no switch at all.
    with STATE.lock:
        cleared = sorted(set(STATE.notes) | STATE.hold_notes)
        STATE.hold_notes.clear()
    for n in cleared:
        STATE.apply_event({"kind": "note", "note": n, "velocity": 0, "source": "ui"})
    if not (live and err is None):
        STATE.apply_event({"kind": "cc", "cc": 64, "value": 0, "source": "ui"})
    return {"ok": err is None, **({"error": err} if err else {}),
            "sent": live and err is None, "released": sent if (live and err is None) else len(targets),
            "cleared": len(cleared), "gen": STATE.panic_gen, "clips_stopped": stopped,
            "loops_stopped": stopped_loops, "chords_released": chords,
            "stress_stopped": bool(stress and stress.get("ok")),
            "scope": "pane-tracked notes + Chord Lab + CC64 + running clips/loops/diagnostics; "
                     "device arp/seq unaffected (no documented stop)"}


def _note_gate(note: int, send_off: bool, gen: Optional[int] = None) -> None:
    """After the gate, release the note (device and/or our state).
    v0.3.1 gen-gate: a /panic bumps STATE.panic_gen and voids every timer scheduled
    before it - a stale release must never kill a re-pressed note right after panic."""
    if gen is not None and gen != STATE.panic_gen:
        return
    if send_off and STATE._out_port_obj is not None:   # v0.3.2: _ensure_out() here silently
        try:                                          # RE-ARMED a port the user had closed by
            import mido                               # flipping LIVE off. Send if it is open;
            STATE._out_port_obj.send(                 # if not, only state clears (PANIC is the
                mido.Message("note_off", note=note, velocity=0, channel=0))
        except Exception:  # noqa: BLE001 - port died mid-send
            pass
    STATE.apply_event({"kind": "note", "note": note, "velocity": 0, "source": "ui"})


# --------------------------------------------------------------------------
# Mod matrix representation (visualizer-side; the MF never reports matrix
# state over CC, and SysEx preset dumps are a later phase)
# --------------------------------------------------------------------------
MATRIX_DEFAULT = {
    "sources": ["Cycling Env", "ENV", "LFO", "Pressure/Vel", "Key/Arp/Seq"],
    "dest_fixed": ["Pitch", "Wave", "Timbre", "Cutoff"],
    "dest_custom": ["User 5", "User 6", "User 7"],
    "amounts": [[0] * 7 for _ in range(5)],   # bipolar -64..64 for display only
    "custom_dest": ["", "", ""],
}


def load_matrix() -> Dict[str, Any]:
    try:
        if MATRIX_PATH.exists():
            data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "amounts" in data:
                return {**MATRIX_DEFAULT, **data}
    except Exception as exc:  # noqa: BLE001
        _log("matrix load failed:", exc)
    return dict(MATRIX_DEFAULT)


@router.post("/matrix")
async def save_matrix(body: Dict[str, Any]) -> Dict[str, Any]:
    cur = load_matrix()
    if "amounts" in body and isinstance(body["amounts"], list):
        amounts = []
        for row in body["amounts"][:5]:
            amounts.append([max(-64, min(64, int(v))) for v in (row or [])[:7]][::1])
            while len(amounts[-1]) < 7:
                amounts[-1].append(0)
        while len(amounts) < 5:
            amounts.append([0] * 7)
        cur["amounts"] = amounts
    for key in ("custom_dest", "sources", "dest_fixed"):
        if isinstance(body.get(key), list):
            cur[key] = [str(x)[:24] for x in body[key][:7]]
    try:
        MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
        MATRIX_PATH.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "matrix": cur}


@router.post("/program")
async def send_program(body: Dict[str, Any]) -> Dict[str, Any]:
    """Program change by 1-based SLOT (1..512) or legacy 0-based program.

    Doubles as the rollback primitive: re-sending the current slot reloads the
    preset and resets the edit buffer. A 512-slot device needs the CC0 bank
    Select *with* the PC - sending a bare PC only ever addresses bank 0.
    """
    slot = body.get("slot")
    if slot is not None:
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return {"ok": False, "error": "slot must be an int"}
        if not 1 <= slot <= 512:
            return {"ok": False, "error": "slot out of range 1..512"}
        bank, program = divmod(slot - 1, 128)
    else:
        try:
            program = int(body.get("program", -1))
        except (TypeError, ValueError):
            return {"ok": False, "error": "program must be an int"}
        if program < 0 or program > 127:
            return {"ok": False, "error": "program out of range 0..127"}
        try:
            bank = int(body.get("bank", STATE.bank if STATE.bank is not None else 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bank must be an int"}
        if not 0 <= bank <= 127:
            return {"ok": False, "error": "bank out of range 0..127"}
    ev = {"kind": "program", "program": program, "bank": bank,
          "source": "visualizer" if STATE.live else "ui"}
    if STATE.live and not MOCK:
        try:
            import mido
            port = _ensure_out()
            port.send(mido.Message("control_change", control=0, value=bank, channel=0))
            port.send(mido.Message("program_change", program=program, channel=0))
            SENDLOG.parent.mkdir(parents=True, exist_ok=True)
            with SENDLOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.time(), "kind": "program",
                                     "program": program, "bank": bank,
                                     "source": "visualizer"}) + "\n")
        except Exception as exc:  # noqa: BLE001
            STATE.live = False
            SHADOW.invalidate("disconnect")   # v0.6.1 F1: device walked away mid-send
            return {"ok": False, "error": f"send failed, LIVE disabled: {exc}"}
    STATE.apply_event(ev)
    return {"ok": True, "sent": STATE.live, "program": program, "bank": bank,
            "slot": bank * 128 + program + 1}


@router.post("/preset/import")
async def import_preset(body: Dict[str, Any]) -> Dict[str, Any]:
    """Import a .mfp preset from base64 content (pane 'Load .mfp' button).

    Read-only import: lights the panel from the preset's own values; never
    touches the device.
    """
    import base64
    try:
        from preset_codec import matrix_from_fields, parse_mfp, parse_structured, to_cc_state
        data = base64.b64decode(str(body.get("data_b64", "")))
        if not data:
            return {"ok": False, "error": "empty payload"}
        # .mfp (Boost archive) vs raw 4672-byte SysEx body
        if data.lstrip().startswith(b"22 serialization::archive"):
            p = parse_mfp(data)
            fields = parse_structured(p["data"])
            name = p["name"]
        else:
            fields = parse_structured(data)
            name = str(body.get("name") or "")
        if not fields:
            return {"ok": False, "error": "no FW5 tagged fields in that file"}
        with STATE.lock:
            STATE.current_preset = {"name": name, "fields": len(fields),
                                    "matrix": matrix_from_fields(fields)}
        for cc, meta in to_cc_state(fields).items():
            STATE.apply_event({"kind": "cc", "cc": cc, "value": meta["value"], "source": "preset"})
        STATE.apply_event({"kind": "preset", "name": name, "source": "ui"})
        return {"ok": True, "name": name, "fields": len(fields),
                "cc_set": sorted(to_cc_state(fields))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/history")
async def history(limit: int = 60) -> Dict[str, Any]:
    STATE.tail_sendlog()
    limit = max(1, min(int(limit), 400))   # v0.3.2: a negative limit used to slice from the front
    with STATE.lock:
        evs = list(STATE.events)[-limit:]
    return {"events": evs, "total_seq": STATE.seq}


# ---------------------------------------------------------------------------
# v0.6.1 F1: registry v2 + shadow + TX command routes (master plan F1).
# Same law as every existing write lane: route -> registry validate/clamp ->
# CommandBus -> _engine_send (the ONE egress: LIVE gate + FW5 allowlist +
# sendlog). undo/redo replay INVERSE commands - values re-sent forward, the
# device has no memory of a rewind; snapshots restore as 'replay-from-slot'.
# ---------------------------------------------------------------------------
_SNAPS_LOCK = threading.Lock()


def _load_snaps() -> Dict[str, Any]:
    try:
        if SNAPS_PATH.exists():
            raw = json.loads(SNAPS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception as exc:  # noqa: BLE001 - user data; corrupt never bricks
        _log("snapshots load failed:", exc)
    return {}


def _save_snaps(snaps: Dict[str, Any]) -> None:
    try:
        SNAPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPS_PATH.write_text(json.dumps(snaps, indent=1), encoding="utf-8")
    except OSError as exc:
        _log("snapshots save failed:", exc)


def _resolve_change(ch: Any):
    """{param|cc, value} -> (cc, clamped, name, why) or (None, error). Refuse-closed."""
    if not isinstance(ch, dict):
        return None, f"bad change {ch!r}"
    key = ch.get("param", ch.get("cc"))
    reg = mfregistry.REGISTRY
    p = None
    if isinstance(key, str):
        p = reg.lookup(key)
    elif isinstance(key, int) and not isinstance(key, bool):
        p = reg.get(key)
    if p is None:
        return None, f"unknown control {key!r} - not on the 21-CC surface, refusing"
    good, clamped, why = reg.validate(p.cc, ch.get("value"))
    if not good:
        return None, f"{p.name}: {why}"
    return (p.cc, clamped, p.name, why), None


@router.get("/registry")
async def registry_route() -> Dict[str, Any]:
    reg = mfregistry.REGISTRY
    return {"ok": True, "params": {p.name: p.as_dict() for p in reg.params.values()},
            "sections": reg.sections()}


@router.get("/shadow")
async def shadow_route() -> Dict[str, Any]:
    return {"ok": True, "entries": {str(cc): e for cc, e in SHADOW.items()}}


@router.post("/command")
async def command_route(body: Dict[str, Any]) -> Dict[str, Any]:
    author = str(body.get("author") or "pane").strip() or "pane"
    changes_in = body.get("changes")
    if not isinstance(changes_in, list) or not changes_in:
        return {"ok": False, "error": "changes: non-empty list of {param|cc, value} required"}
    changes, errs = [], []
    for ch in changes_in:
        got, err = _resolve_change(ch)
        if err:
            errs.append(err)
            continue
        cc, val, name, why = got
        changes.append({"cc": cc, "value": val})
    if errs:
        return {"ok": False, "error": "; ".join(errs)}          # whole-command refuse-closed
    cmd = COMMANDS.execute({"author": author,
                            "action_id": str(body.get("action_id") or "command"),
                            "atomic": bool(body.get("atomic")),
                            "note": str(body.get("note") or ""),
                            "changes": changes})
    return {"ok": bool(cmd["outcomes"]) and all(o["ok"] for o in cmd["outcomes"]),
            "command": COMMANDS._ser(cmd)}


@router.post("/command/undo")
async def command_undo(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    author = (body or {}).get("author")
    cmd = COMMANDS.undo(None if not author else str(author))
    if cmd is None:
        return {"ok": False, "error": "nothing to undo"}
    return {"ok": all(o["ok"] for o in cmd["outcomes"]),
            "command": COMMANDS._ser(cmd),
            "note": "inverse replay: previous values re-SENT - never a device rewind"}


@router.post("/command/redo")
async def command_redo(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cmd = COMMANDS.redo()
    if cmd is None:
        return {"ok": False, "error": "nothing to redo"}
    return {"ok": all(o["ok"] for o in cmd["outcomes"]),
            "command": COMMANDS._ser(cmd),
            "note": "replay forward - the device keeps no undo stack"}


@router.get("/commands/history")
async def commands_history(limit: int = 60) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    return {"ok": True, "entries": COMMANDS.export()[-limit:],
            "load_error": COMMANDS.load_error, "save_error": COMMANDS.save_error}


@router.post("/command/snapshot")
async def command_snapshot(body: Dict[str, Any]) -> Dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "snapshot needs a name"}
    snap = COMMANDS.snapshot(name)
    with _SNAPS_LOCK:
        snaps = _load_snaps()
        snaps[name] = snap
        _save_snaps(snaps)
    return {"ok": True, "snapshot": snap}


@router.get("/command/snapshots")
async def command_snapshots() -> Dict[str, Any]:
    with _SNAPS_LOCK:
        snaps = _load_snaps()
    return {"ok": True,
            "snapshots": {k: {"params": len(v.get("params") or {}),
                              "skipped": len(v.get("skipped") or []), "ts": v.get("ts")}
                          for k, v in snaps.items()}}


@router.post("/command/restore")
async def command_restore(body: Dict[str, Any]) -> Dict[str, Any]:
    name = str(body.get("name") or "").strip()
    with _SNAPS_LOCK:
        snap = _load_snaps().get(name)
    if snap is None:
        return {"ok": False, "error": f"no snapshot '{name}'"}
    cmd = COMMANDS.restore(snap, author=str(body.get("author") or "restore"))
    return {"ok": bool(cmd["outcomes"]) and all(o["ok"] for o in cmd["outcomes"]),
            "command": COMMANDS._ser(cmd), "note": cmd.get("note", "")}


@router.get("/ports")
async def ports() -> Dict[str, Any]:
    return {"in": _ports("input"), "out": _ports("output")}


# ---------------------------------------------------------------------------
# v0.4 mod lanes. Class B contract holds end-to-end: human UI action -> route ->
# engine -> _engine_send (LIVE gate + FW5 allowlist + sendlog provenance).
# Save routes write USER data (macro layouts, clips) - never seeded from device.
# ---------------------------------------------------------------------------
@router.get("/macros")
async def macros() -> Dict[str, Any]:
    with MACRO_BANK.lock:
        return {"ok": True, "macros": MACRO_BANK.macros}


@router.post("/macro")
async def macro_move(body: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(body.get("id", ""))
    try:
        x = int(body.get("x"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "x must be an int 0..127"}
    return MACRO_BANK.move(mid, x)


@router.post("/macro/save")
async def macro_save(body: Dict[str, Any]) -> Dict[str, Any]:
    errs = MACRO_BANK.save(body.get("macro") or {})
    return {"ok": not errs, "errors": errs}


@router.post("/macro/delete")
async def macro_delete(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": MACRO_BANK.delete(str(body.get("id", "")))}


@router.get("/clips")
async def clips() -> Dict[str, Any]:
    with CLIP_BANK.lock:
        return {"ok": True, "clips": CLIP_BANK.clips}


@router.post("/clip/record")
async def clip_record(body: Dict[str, Any]) -> Dict[str, Any]:
    action = str(body.get("action", ""))
    if action == "arm":
        if RECORDER.armed:
            return {"ok": False, "error": "a recording is already armed"}
        RECORDER.arm(str(body.get("name", ""))[:40])
        return {"ok": True, "armed": True,
                "note": "move real knobs (MIDI Control mode) or pane/macro controls; STOP to save"}
    if action == "stop":
        clip = RECORDER.stop()
        if clip is None:
            return {"ok": False, "error": "nothing was moved while armed - clip discarded"}
        errs = CLIP_BANK.save(clip)
        if errs:  # a recorded clip failing its own validator is a bug, said out loud
            return {"ok": False, "errors": [f"internal: recorded clip invalid: {e}" for e in errs]}
        return {"ok": True, "clip": {"id": clip["id"], "name": clip["name"],
                                     "length_ms": clip["length_ms"],
                                     "tracks": sorted(int(t["cc"]) for t in clip["tracks"])}}
    return {"ok": False, "error": "action must be arm|stop"}


@router.post("/clip/save")
async def clip_save(body: Dict[str, Any]) -> Dict[str, Any]:
    errs = CLIP_BANK.save(body.get("clip") or {})
    return {"ok": not errs, "errors": errs}


@router.post("/clip/delete")
async def clip_delete(body: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(body.get("id", ""))
    CLIP_PLAYER.stop(cid)
    return {"ok": CLIP_BANK.delete(cid)}


@router.post("/clip/play")
async def clip_play(body: Dict[str, Any]) -> Dict[str, Any]:
    mute = {int(c) for c in body.get("mute", []) if str(c).lstrip("-").isdigit()}
    try:
        stretch = float(body.get("stretch", 1.0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "stretch must be a number"}
    return CLIP_PLAYER.play(str(body.get("id", "")), loop=str(body.get("loop", "loop")),
                            stretch=stretch, reverse=bool(body.get("reverse", False)),
                            mute=mute)


@router.post("/clip/stop")
async def clip_stop(body: Dict[str, Any]) -> Dict[str, Any]:
    if body.get("all"):
        return {"ok": True, "stopped": CLIP_PLAYER.stop_all()}
    return {"ok": CLIP_PLAYER.stop(str(body.get("id", "")))}



# ---------------------------------------------------------------------------
# v0.5 LOOP STUDIO: what-you-played loops (mfpat/1) + SMF export + folder reveal
# ---------------------------------------------------------------------------
LOOPS_PATH = Path(os.environ.get(
    "MF_VIS_LOOPS",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mf-visualizer" / "loops.json"),
))


def _note_send(note: int, velocity: int, source: str) -> bool:
    """Note sink for the loop transport. Same trust contract as _engine_send:
    True ONLY when the message actually reached the device (or mock claims it);
    LIVE-off updates state visually; a send exception drops LIVE. velocity 0
    releases. TEETER LAW: whatever this function takes down, the player is
    obligated to lift again on EVERY exit path."""
    n, v = int(note), int(velocity)
    if not (0 <= n <= 127 and 0 <= v <= 127):
        return False                                     # refuse-closed, never to the wire
    if not STATE.live:
        STATE.apply_event({"kind": "note", "note": n, "velocity": v, "source": source})
        return False
    if MOCK:
        STATE.apply_event({"kind": "note", "note": n, "velocity": v, "source": source})
        return True
    try:
        import mido
        msg = (mido.Message("note_on", note=n, velocity=max(1, v), channel=0) if v > 0
               else mido.Message("note_off", note=n, velocity=0, channel=0))
        _ensure_out().send(msg)
    except Exception as exc:  # noqa: BLE001
        STATE.live = False
        _log(f"{source} note send failed, LIVE disabled: {exc}")
        STATE.apply_event({"kind": "note", "note": n, "velocity": 0, "source": source})
        return False
    STATE.apply_event({"kind": "note", "note": n, "velocity": v, "source": source})
    _append_sendlog({"kind": "note", "note": n, "velocity": v, "ts": time.time(), "source": source})
    return True


LOOP_BANK = LoopBank(LOOPS_PATH, log=_log)
LOOP_PLAYER = LoopPlayer(LOOP_BANK, _note_send, lambda: STATE.panic_gen, _log,
                         live_fn=lambda: STATE.live)
LOOP_REC = NoteRecorder(_events_since, lambda: STATE.seq, get_base=LOOP_BANK.get, log=_log)


# ---------------------------------------------------------------------------
# v0.6 THEORY (F2) + CHORD LAB (F2.5) + DIAGNOSTICS HARNESS (F0a)
# Three pure-software lanes, all riding the EXISTING sinks:
#   theory   = read-only math (no MIDI, no state)
#   chordlab = note events through _note_send (LIVE gate + sendlog, source chord:*)
#   stress   = diagnostics through _engine_send/_note_send (source stress:*)
# Nothing here adds a second egress, an allowlist bypass, or a device read.
# ---------------------------------------------------------------------------
CHORD_LAB = ChordLab(_note_send, lambda: STATE.panic_gen, lambda: STATE.live, _log)
STRESS = StressHarness(_engine_send, _note_send, lambda: STATE.live,
                       lambda: STATE.panic_gen, ALLOWED_CCS, _log)
DIAG_DIR = LOOPS_PATH.parent / "diag"


@router.get("/theory")
async def theory_registry() -> Dict[str, Any]:
    """Scales, chord qualities, voicing styles, progressions - the pane's map.

    Read-only: this never sends, never stores, and never claims device state."""
    return {"ok": True, **mftheory.registry()}


@router.post("/theory/chord")
async def theory_chord(body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a voicing WITHOUT playing it (Chord Lab's 'show me' path)."""
    try:
        notes = mftheory.voicing(
            int(body.get("root", 0)), str(body.get("quality", "major")),
            str(body.get("voicing", "close")), int(body.get("inversion", 0)),
            int(body.get("octave", 4)), bool(body.get("bass_lock", False)),
            intervals=body.get("intervals") or None)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "notes": notes, "names": [mftheory.note_name(n) for n in notes],
            "label": mftheory.chord_label(notes, notes[0]),
            "paraphony": mftheory.paraphony_warning(notes)}


@router.post("/theory/progression")
async def theory_prog(body: Dict[str, Any]) -> Dict[str, Any]:
    """A named progression spelled out in the requested scale/key (read-only)."""
    try:
        chords = mftheory.progression(str(body.get("scale", "MAJOR")), int(body.get("root", 0)),
                                      str(body.get("name", "ii-V-I (jazz)")),
                                      bool(body.get("seventh", True)), int(body.get("octave", 4)))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "progression": mftheory.PROGRESSIONS[str(body.get("name", "ii-V-I (jazz)"))],
            "chords": chords}


@router.get("/chord")
async def chord_state() -> Dict[str, Any]:
    return {"ok": True, "lab": CHORD_LAB.state(), "qualities": list(mftheory.QUALITIES),
            "voicings": list(mftheory.VOICING_STYLES), "scales": list(mftheory.SCALE_NAMES),
            "paraphony_voices": mftheory.PARAPHONY_VOICES}


@router.post("/chord/play")
async def chord_play(body: Dict[str, Any]) -> Dict[str, Any]:
    """Audition a chord. Class B: a human click -> theory -> _note_send.

    LIVE off is honest (notes light the pane, nothing reaches the device);
    every note that goes down is tracked for release, and PANIC lifts them."""
    if isinstance(body.get("notes"), list):
        try:
            notes = [int(n) for n in body["notes"]]
        except (TypeError, ValueError):
            return {"ok": False, "error": "notes must be ints"}
        bad = [n for n in notes if not 0 <= n <= 127]
        if bad:
            return {"ok": False, "error": f"note out of range 0..127: {bad}"}
        label = str(body.get("label", "")) or (mftheory.chord_label(notes, notes[0]) if notes else "")
    else:
        try:
            notes = mftheory.voicing(
                int(body.get("root", 0)), str(body.get("quality", "major")),
                str(body.get("voicing", "close")), int(body.get("inversion", 0)),
                int(body.get("octave", 4)), bool(body.get("bass_lock", False)),
                intervals=body.get("intervals") or None)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        label = str(body.get("label", "")) or (mftheory.chord_label(notes, notes[0]) or "")
    return CHORD_LAB.play(notes, label=label, action=str(body.get("action", "hold")),
                          duration_ms=int(body.get("duration_ms", 1400) or 1400),
                          source=str(body.get("source", "") or "") or None)


@router.post("/chord/release")
async def chord_release(body: Dict[str, Any]) -> Dict[str, Any]:
    if body.get("all"):
        return {"ok": True, "released": CHORD_LAB.release_all()}
    r = CHORD_LAB.release()
    return {"ok": True, "released": r["released"]}


@router.get("/tutor")
async def tutor() -> Dict[str, Any]:
    """The Mod-Matrix Tutor knowledge base (F2b) - read-only, grounded, cited.

    Content lives in dashboard/tutor.json, authored against the FW5 manual in the
    source captures (owner-side) derived from the Arturia forum; every row carries a provenance label (official /
    observed / inferred / unknown) and a citation. This route EXPLAINS the device
    matrix. There is no write path here and none is planned: the app cannot (and
    must not claim to) program the matrix over MIDI."""
    try:
        data = json.loads(TUTOR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"tutor data unreadable: {exc}"}
    return {"ok": True, **data,
            "law": "explains and labels only - no MIDI path programs the hardware matrix "
                   "(see the synth-mod catalog)"}


@router.get("/diag")
async def diag() -> Dict[str, Any]:
    """The F0a harness readout: current run, sliding send rate, counters.

    Evidence only - the numbers come from the harness's own attempts plus the
    real sendlog rows (source stress:*)."""
    return {"ok": True, **STRESS.state(), "live": STATE.live, "mock": MOCK,
            "sendlog": str(SENDLOG)}


@router.post("/stress/start")
async def stress_start(body: Dict[str, Any]) -> Dict[str, Any]:
    return STRESS.start(pattern=str(body.get("pattern", "cc_sweep")),
                        duration_s=float(body.get("duration_s", 5.0) or 5.0),
                        rate_hz=float(body.get("rate_hz", 20.0) or 20.0),
                        cc=int(body.get("cc", 23)), note=int(body.get("note", 60)))


@router.post("/stress/stop")
async def stress_stop(body: Dict[str, Any]) -> Dict[str, Any]:
    return STRESS.stop(why=str(body.get("why", "user") or "user"))


@router.get("/stress/export")
async def stress_export() -> Dict[str, Any]:
    """mfstress/1 JSON + a CSV summary written beside the other user data."""
    out = STRESS.export()
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = DIAG_DIR / f"mfstress-{stamp}.json"
    csv_path = DIAG_DIR / f"mfstress-{stamp}.csv"
    try:
        json_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        csv_path.write_text(out["csv"], encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc), **out}
    # NB: out already carries "csv" as the CSV TEXT - the file paths live under
    # their own key so a path can never be mistaken for the export body.
    return {"ok": True, **out, "paths": {"json": str(json_path), "csv": str(csv_path)},
            "dir": str(DIAG_DIR)}


def _slug(s: str) -> str:
    keep = [c if (c.isalnum() or c in "-_ ") else "" for c in str(s)]
    return "".join(keep).strip().replace(" ", "_")[:40] or "loop"


@router.get("/loops")
async def loops() -> Dict[str, Any]:
    return {"ok": True, "loops": LOOP_BANK.summaries(), "playing": LOOP_PLAYER.state()}


@router.post("/loop/record")
async def loop_record(body: Dict[str, Any]) -> Dict[str, Any]:
    action = str(body.get("action", ""))
    if action == "arm":
        if LOOP_REC.armed:
            return {"ok": False, "error": "a loop recording is already armed"}
        if RECORDER.armed:
            return {"ok": False, "error": "clip recorder is armed - stop it first (one ear at a time)"}
        try:
            bpm = float(body.get("bpm", 120.0))
            spb = int(body.get("spb", 4))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bpm/spb must be numbers"}
        LOOP_REC.arm(str(body.get("name", ""))[:40], bpm=bpm, spb=spb)
        return {"ok": True, "armed": True,
                "note": "play on the panel or the pane keys; STOP closes open notes and quantizes"}
    if action == "stop":
        pat = LOOP_REC.stop()
        if pat is None:
            return {"ok": False, "error": "nothing was played while armed - take discarded"}
        errs = LOOP_BANK.save(pat)
        if errs:
            return {"ok": False, "errors": [f"internal: recorded loop invalid: {e}" for e in errs]}
        return {"ok": True, "loop": {"id": pat["id"], "name": pat["name"], "bpm": pat["bpm"],
                                     "bars": pat["bars"], "notes": len(pat["notes"])}}
    return {"ok": False, "error": "action must be arm|stop"}


@router.post("/loop/overdub")
async def loop_overdub(body: Dict[str, Any]) -> Dict[str, Any]:
    action = str(body.get("action", ""))
    lid = str(body.get("id", ""))
    if action == "arm":
        base = LOOP_BANK.get(lid)
        if base is None:
            return {"ok": False, "error": f"no loop {lid}"}
        if LOOP_REC.armed or RECORDER.armed:
            return {"ok": False, "error": "a recorder is already armed - stop it first"}
        if lid in [p["id"] for p in LOOP_PLAYER.state()]:
            return {"ok": False, "error": "stop playback before overdubbing - one thread owns the grid"}
        LOOP_REC.arm(str(body.get("name", ""))[:40] or base["name"],
                     bpm=base["bpm"], spb=base["spb"], overdub_into=lid)
        return {"ok": True, "armed": True, "over": lid,
                "note": "play MORE - STOP merges and doubles the loop if you ran past the end"}
    if action == "stop":
        if not (LOOP_REC.armed and LOOP_REC.over):
            return {"ok": False, "error": "no overdub armed"}
        pat = LOOP_REC.stop()
        if pat is None:
            return {"ok": True, "note": "nothing added - loop unchanged"}
        errs = LOOP_BANK.save(pat)
        if errs:
            return {"ok": False, "errors": errs}
        return {"ok": True, "loop": {"id": pat["id"], "name": pat["name"], "bpm": pat["bpm"],
                                     "bars": pat["bars"], "notes": len(pat["notes"])}}
    return {"ok": False, "error": "action must be arm|stop"}


@router.post("/loop/play")
async def loop_play(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        stretch = float(body.get("stretch", 1.0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "stretch must be a number"}
    return LOOP_PLAYER.play(str(body.get("id", "")), loop=str(body.get("loop", "loop")),
                            stretch=stretch)


@router.post("/loop/stop")
async def loop_stop(body: Dict[str, Any]) -> Dict[str, Any]:
    if body.get("all"):
        return {"ok": True, "stopped": LOOP_PLAYER.stop_all()}
    return {"ok": LOOP_PLAYER.stop(str(body.get("id", "")))}


@router.post("/loop/double")
async def loop_double(body: Dict[str, Any]) -> Dict[str, Any]:
    d = LOOP_BANK.double(str(body.get("id", "")))
    if d is None:
        return {"ok": False, "error": "cannot double (missing loop or 64-bar cap)"}
    return {"ok": True, "loop": {"id": d["id"], "name": d["name"], "bpm": d["bpm"],
                                 "bars": d["bars"], "notes": len(d["notes"])}}


@router.post("/loop/halve")
async def loop_halve(body: Dict[str, Any]) -> Dict[str, Any]:
    lid = str(body.get("id", ""))
    base = LOOP_BANK.get(lid)
    if base is None:
        return {"ok": False, "error": "no such loop"}
    if lid in [p["id"] for p in LOOP_PLAYER.state()]:
        return {"ok": False, "error": "stop playback before resizing (the running transport owns the old span)"}
    d = LOOP_BANK.resize(lid, max(1, base["bars"] // 2))
    return {"ok": True, "loop": {"id": d["id"], "name": d["name"], "bpm": d["bpm"],
                                 "bars": d["bars"], "notes": len(d["notes"])}} if d \
        else {"ok": False, "error": "cannot halve"}


@router.post("/loop/length")
async def loop_length(body: Dict[str, Any]) -> Dict[str, Any]:
    lid = str(body.get("id", ""))
    if lid in [p["id"] for p in LOOP_PLAYER.state()]:
        return {"ok": False, "error": "stop playback before resizing (the running transport owns the old span)"}
    try:
        bars = int(body.get("bars", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bars must be a number"}
    d = LOOP_BANK.resize(lid, bars)
    return {"ok": True, "loop": {"id": d["id"], "name": d["name"], "bpm": d["bpm"],
                                 "bars": d["bars"], "notes": len(d["notes"])}} if d \
        else {"ok": False, "error": "cannot set length (missing loop or bars outside 1-64)"}


@router.post("/loop/delete")
async def loop_delete(body: Dict[str, Any]) -> Dict[str, Any]:
    lid = str(body.get("id", ""))
    LOOP_PLAYER.stop(lid)
    return {"ok": LOOP_BANK.delete(lid)}


@router.post("/loop/export")
async def loop_export(body: Dict[str, Any]) -> Dict[str, Any]:
    """mfpat/1 -> format-1 .mid beside loops.json. THE FL Studio door: FL imports
    .mid natively; it cannot read our JSON. CC lanes flatten onto the grid too."""
    pat = LOOP_BANK.get(str(body.get("id", "")))
    if pat is None:
        return {"ok": False, "error": "no such loop"}
    try:
        blob = smf_format1(pat)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"export failed: {exc}"}
    out_dir = LOOPS_PATH.parent / "midis"
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = out_dir / f"{_slug(pat.get('name') or pat['id'])}.mid"
    tmp = Path(str(fn) + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, fn)
    return {"ok": True, "path": str(fn), "file": fn.name, "bytes": len(blob)}


@router.post("/clip/export")
async def clip_export(body: Dict[str, Any]) -> Dict[str, Any]:
    """A pure CC clip (mfclip/1) exports too: wrapped as an mfpat with no notes."""
    cid = str(body.get("id", ""))
    with CLIP_BANK.lock:
        clip = next((c for c in CLIP_BANK.clips if c.get("id") == cid), None)
    if clip is None:
        return {"ok": False, "error": "no such clip"}
    bpm = float(clip.get("bpm", 0) or 0)
    if not (10.0 <= bpm <= 999.0):
        bpm = 120.0    # honest default: the label says what it used
    pat = {"schema": "mfpat/1", "id": clip["id"], "name": clip.get("name", "clip"),
           "bpm": bpm, "spb": 4,
           "bars": max(1, min(64, int(float(clip["length_ms"]) / (240000.0 / bpm) + 0.999))),
           "notes": [], "cc_tracks": clip["tracks"]}
    blob = smf_format1(pat)
    out_dir = LOOPS_PATH.parent / "midis"
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = out_dir / f"{_slug(pat['name'])}.mid"
    tmp = Path(str(fn) + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, fn)
    return {"ok": True, "path": str(fn), "file": fn.name, "bytes": len(blob), "bpm_used": bpm}


@router.post("/reveal")
async def reveal_folder(body: Dict[str, Any]) -> Dict[str, Any]:
    """Opens Explorer on OUR data folder (Windows-only, zero egress). This is the
    'get the .mids to FL' handoff: exported files live in midis/ inside it."""
    where = str(body.get("where", "data"))
    d = (LOOPS_PATH.parent / "midis") if where == "midis" else LOOPS_PATH.parent
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(d))                      # noqa: S606 - deliberate reveal, our own dir
        return {"ok": True, "path": str(d)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": str(d)}


# --------------------------------------------------------------------------
# Live push: /events WebSocket + delta pump. The pane used to re-fetch the
# whole /state every 700ms (and the backend re-enumerated MIDI ports inside
# it) - that polling was the visible knob/key lag. Now a 120ms pump diffs
# STATE and pushes only what changed; the pane keeps a slow poll as fallback.
# --------------------------------------------------------------------------
_WS_CLIENTS: set = set()
_PUMP_TASK = None


def _ws_auth_ok_local(ws) -> bool:
    try:
        from hermes_cli.web_server_chat import _ws_auth_ok  # same-process helper
        return bool(_ws_auth_ok(ws))
    except Exception:  # noqa: BLE001 - helper moved/absent: fail closed
        return False


@router.websocket("/events")
async def events_ws(ws) -> None:
    if not _ws_auth_ok_local(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    _WS_CLIENTS.add(ws)
    _ensure_pump()
    try:
        while True:
            await ws.receive_text()  # clients are silent; this blocks until drop
    except Exception:  # noqa: BLE001 - WebSocketDisconnect and transport errors
        pass
    finally:
        _WS_CLIENTS.discard(ws)


def _ensure_pump() -> None:
    global _PUMP_TASK
    import asyncio
    if _PUMP_TASK is None or _PUMP_TASK.done():
        try:
            _PUMP_TASK = asyncio.get_running_loop().create_task(_pump())
        except RuntimeError:
            pass  # no loop (unit harness); REST path still works


async def _pump() -> None:
    import asyncio
    last = -1
    snap_notes: List[int] = []
    snap_flags = None
    snap_pressure = 0
    while True:
        await asyncio.sleep(0.12)
        try:
            STATE.tail_sendlog()  # Hermes/FL sends land in the send log; nobody may be polling now
            RECORDER.poll()       # v0.4: recording must survive a pane with no WS and no polls
            LOOP_REC.poll()       # v0.5: same for the note recorder
            now = time.time()
            _read_tick(now)
            _pressure_tick(now)
            with STATE.lock:
                STATE.sweep_stuck(now)
                seq = STATE.seq
                flags = (STATE.live, STATE.listening, STATE.listen_error,
                         STATE.program, STATE.bank, (STATE.current_preset or {}).get("name"),
                         STATE.read_session is not None)
                if seq == last and sorted(STATE.notes) == snap_notes and flags == snap_flags \
                        and STATE.pressure == snap_pressure:
                    continue
                delta = {
                    "type": "delta",
                    "seq": seq,
                    "cc": {str(k): v for k, v in STATE.cc.items()},
                    "events": [e for e in STATE.events if e.get("seq", 0) > last][-12:],
                    "active_notes": sorted(STATE.notes),
                    "program": STATE.program,
                    "bank": STATE.bank,
                    "live": STATE.live,
                    "listening": STATE.listening,
                    "listen_error": STATE.listen_error,
                    "preset": STATE.current_preset,
                    "preset_error": STATE.preset_error,
                    "pressure": STATE.pressure,
                    "reading": STATE.read_session is not None,
                    "note_stats": {str(n): st for n, st in STATE.note_stats.items()},
                    "chords": sorted(STATE.chord_stats.values(), key=lambda c: -c["count"])[:12],
                }
                last, snap_notes, snap_flags = seq, delta["active_notes"], flags
                snap_pressure = STATE.pressure
            if not _WS_CLIENTS:
                continue
            dead = []
            payload = json.dumps(delta)
            for ws in list(_WS_CLIENTS):
                try:
                    await ws.send_text(payload)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                _WS_CLIENTS.discard(ws)
        except Exception as exc:  # noqa: BLE001 - the pump must never die
            _log("pump tick failed:", exc)


# --------------------------------------------------------------------------
# Startup: seed history from the shared send log so knobs land where we last left them
# --------------------------------------------------------------------------
if os.environ.get('MF_VIS_AUDIO_OFF') != '1':
    threading.Thread(target=_audio_worker, name='mfvis-spectrum', daemon=True).start()
STATE.seed_sendlog_history()
_log(f"backend ready - {len(PARAMS)} FW5 controls; sendlog {SENDLOG} ({'mock' if MOCK else 'live MIDI available'})")
