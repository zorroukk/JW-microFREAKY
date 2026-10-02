"""FastMCP entry point for the MicroFreak MCP server.

Tool surface (v0.1): ping, status, list_controls, cc, cc_group, note,
program, rollback. Write tools are grouped into change units so rollback
restores exactly the previous unit, mirroring the fl_studio_mcp discipline.

Honest-state model: the MicroFreak cannot report its parameter values back
(no MIDI 2.0 on this unit), so "before" values come from OUR send log. A CC
never sent in this log has unknown prior state; rolling it back requires a
preset reload (mf_program) instead - tools say so explicitly rather than
pretending.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import __version__
from .profile import Profile, ProfileError
from .transport import LiveBackend, MockBackend, SendLog, find_port

logger = logging.getLogger("mf_mcp")

LOG_PATH = Path(
    os.environ.get(
        "MF_MCP_SENDLOG",
        Path(os.environ.get("LOCALAPPDATA", ".")) / "mf-mcp" / "sendlog.jsonl",
    )
)


class ServerState:
    def __init__(self) -> None:
        self.profile = Profile.load()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.log = SendLog(path=LOG_PATH)
        self.mock = os.environ.get("MF_MCP_MOCK") == "1"
        self._backend = None
        self._lock = threading.Lock()
        # change units: list of {"id", "writes":[{cc, before, after}]}
        self._units: list[dict[str, Any]] = []

    def backend(self):
        if self._backend is None:
            if self.mock:
                self._backend = MockBackend(self.log)
            else:
                self._backend = LiveBackend(self.log)
        return self._backend


STATE: ServerState | None = None


def _state() -> ServerState:
    global STATE
    if STATE is None:
        STATE = ServerState()
    return STATE


def _record_write(st: ServerState, cc: int, value: int) -> dict[str, Any]:
    before = st.log.last_cc(cc)
    st.backend().send_cc(cc, value)
    return {"cc": cc, "before": before, "after": value}


mcp = FastMCP(name="microfreak", version=__version__)


@mcp.tool()
def mf_ping() -> dict:
    """Is a MicroFreak-named MIDI output port present? Opens nothing. Read-Only."""
    outs = []
    try:
        import mido

        outs = mido.get_output_names()
    except Exception as exc:  # noqa: BLE001 - ping reports, never crashes
        return {"ok": False, "error": str(exc)}
    port = find_port(outs)
    return {"ok": port is not None, "port": port, "all_outputs": outs,
            "mock": os.environ.get("MF_MCP_MOCK") == "1",
            "sendlog": str(LOG_PATH)}


@mcp.tool()
def mf_status() -> dict:
    """Live-ness, channel, profile id, and the last 20 send-log entries. Read-Only."""
    st = _state()
    snap = st.log.snapshot()
    return {
        "profile": st.profile.meta.get("profile_id"),
        "firmware_basis": st.profile.meta.get("firmware_basis"),
        "backend": type(st.backend()).__name__ if st._backend else "unopened",
        "channel": st.backend().channel if st._backend else 0,
        "sendlog_total": len(snap),
        "recent": snap[-20:],
    }


@mcp.tool()
def mf_list_controls() -> dict:
    """The FW5 CC profile: 21 controls with cc ranges + verified endpoint notes. Read-Only."""
    st = _state()
    return {"parameters": st.profile.params, "aliases": st.profile.aliases}


@mcp.tool()
def mf_cc(name: str, value: int) -> dict:
    """Set one control by name (or alias) to a 0..127 value. Write-Safe (logged, rollback-able).

    name must exist in mf_list_controls; value is clamped to the control's
    min..max. The device cannot echo its state back - the before/after pair
    here is OUR bookkeeping from the send log, not a read from the synth.
    """
    st = _state()
    with st._lock:
        try:
            key, spec = st.profile.resolve(name)
        except ProfileError as exc:
            return {"ok": False, "error": str(exc)}
        v = st.profile.clamp(key, spec, value)
        w = _record_write(st, spec["cc"], v)
        w["name"] = key
        st._units.append({"id": f"chg_{len(st._units) + 1}", "writes": [w]})
        return {"ok": True, "change": w,
                "note": "before=None means never sent in this session; "
                        "preset reload (mf_program) is the only reset for it."}


@mcp.tool()
def mf_cc_group(writes: list[dict]) -> dict:
    """Apply an ordered list of {name, value} CCs as ONE rollback unit. Write-Safe.

    Sequentially paced by the transport gap (20 ms). Any unknown name aborts
    the whole group BEFORE anything is sent (validate-then-send).
    """
    st = _state()
    resolved = []
    for i, w in enumerate(writes):
        try:
            key, spec = st.profile.resolve(str(w.get("name", "")))
        except ProfileError as exc:
            return {"ok": False, "aborted_before_send": True,
                    "error": f"write #{i}: {exc}"}
        resolved.append((key, spec, st.profile.clamp(key, spec, int(w.get("value", 0)))))
    with st._lock:
        unit = {"id": f"chg_{len(st._units) + 1}", "writes": []}
        for key, spec, v in resolved:
            rec = _record_write(st, spec["cc"], v)
            rec["name"] = key
            unit["writes"].append(rec)
        st._units.append(unit)
        return {"ok": True, "unit": unit["id"], "applied": unit["writes"]}


@mcp.tool()
def mf_note(note: int = 60, velocity: int = 64, duration_ms: int = 300) -> dict:
    """Momentary note on/off (does not touch stored state; not a rollback unit)."""
    st = _state()
    with st._lock:
        return {"ok": True, **st.backend().send_note(note, velocity, duration_ms)}


@mcp.tool()
def mf_program(program: int, name: str = "") -> dict:
    """Load a preset (0..127). The nuclear rollback: restores the stored patch, wiping edit-buffer drift. Write-Safe (logged)."""
    st = _state()
    with st._lock:
        r = st.backend().program_change(program)
    return {"ok": True, "preset": r, "hint": "device loads preset immediately; edit buffer resets."}


@mcp.tool()
def mf_rollback_last_change() -> dict:
    """Undo the most recent mf_cc / mf_cc_group unit by re-sending prior logged values. Write-Safe.

    Any CC in the unit whose 'before' is None (never sent in this session)
    cannot be restored from the log - it is reported as needing mf_program.
    """
    st = _state()
    with st._lock:
        if not st._units:
            return {"ok": False, "error": "no recorded change units in this session"}
        unit = st._units.pop()
        undone, unknown = [], []
        for w in reversed(unit["writes"]):
            if w["before"] is None:
                unknown.append(w["name"])
                continue
            st.backend().send_cc(w["cc"], int(w["before"]))
            undone.append({"name": w["name"], "cc": w["cc"], "restored": w["before"]})
        return {"ok": not unknown, "unit": unit["id"], "restored": undone,
                "needs_preset_reload": unknown}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _state()  # fail fast on profile/log-path problems
    logger.info("mf-mcp %s starting (sendlog=%s)", __version__, LOG_PATH)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
