"""MIDI transport for MicroFreak with the lane's safety contract.

Contract (mirrors the lane runtime_policy + the 2026-09-06 P0 probe):
- open ONLY a port whose name contains "MicroFreak"; never fall back to the
  first available output (device_required gate).
- No-readback device: every CC write is appended to an ordered send log so
  state is reconstructible; rollback = reload the stored preset (program
  change) or replay inverse CCs from the log.
- No SysEx, no pitch bend, no aftertouch - this module simply doesn't
  implement them; the server surface only exposes CCs/notes/program.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mido

PORT_HINT = "MicroFreak"


class TransportError(RuntimeError):
    pass


def find_port(names: list[str], hint: str = PORT_HINT) -> str | None:
    for n in names:
        if hint.lower() in n.lower():
            return n
    return None


@dataclass
class SendLog:
    path: Path | None = None
    _entries: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # Load persisted history so `last_cc` survives process restarts: the
        # last value WE sent is the best-known device state for rollback,
        # even in a fresh session. Malformed lines are skipped (audit
        # store is append-only; a torn write must not brick the server).
        if self.path is not None and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and "kind" in e:
                    self._entries.append(e)

    def append(self, kind: str, detail: dict[str, Any]) -> dict[str, Any]:
        entry = {"ts": time.time(), "kind": kind, **detail}
        with self._lock:
            self._entries.append(entry)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
        return entry

    def last_cc(self, cc: int) -> int | None:
        with self._lock:
            for e in reversed(self._entries):
                if e["kind"] == "cc" and e.get("cc") == cc:
                    return int(e["value"])
        return None

    @staticmethod
    def default_path() -> Path:
        import os

        override = os.environ.get("MF_MCP_SENDLOG")
        if override:
            return Path(override)
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "mf-mcp" / "sendlog.jsonl"

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)


class Backend:
    """Common send-side for live and mock backends (validation + logging)."""

    def __init__(self, log: SendLog, channel: int = 0, min_gap_ms: int = 20):
        if not 0 <= channel <= 15:
            raise TransportError("channel must be 0..15")
        self.log = log
        self.channel = channel
        self.min_gap = min_gap_ms / 1000.0
        self._last_send = 0.0
        self._lock = threading.Lock()

    def _pace(self) -> None:
        wait = self.min_gap - (time.time() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        self._last_send = time.time()

    def send_cc(self, cc: int, value: int) -> dict[str, Any]:
        cc, value = int(cc), int(value)
        if not 0 <= cc <= 127 or not 0 <= value <= 127:
            raise TransportError("cc and value must be 0..127")
        with self._lock:
            self._pace()
            self._emit(mido.Message("control_change", channel=self.channel,
                                     control=cc, value=value))
        return self.log.append("cc", {"cc": cc, "value": value})

    def send_note(self, note: int, velocity: int, duration_ms: int) -> dict[str, Any]:
        note, velocity = int(note), int(velocity)
        if not 0 <= note <= 127 or not 1 <= velocity <= 127:
            raise TransportError("note 0..127, velocity 1..127")
        duration_ms = max(20, min(4000, int(duration_ms)))
        with self._lock:
            self._pace()
            self._emit(mido.Message("note_on", channel=self.channel,
                                     note=note, velocity=velocity))
            time.sleep(duration_ms / 1000.0)
            self._emit(mido.Message("note_off", channel=self.channel,
                                     note=note, velocity=0))
        return self.log.append("note", {"note": note, "velocity": velocity,
                                        "duration_ms": duration_ms})

    def program_change(self, program: int) -> dict[str, Any]:
        program = int(program)
        if not 0 <= program <= 127:
            raise TransportError("program must be 0..127")
        with self._lock:
            self._pace()
            self._emit(mido.Message("program_change", channel=self.channel,
                                     program=program))
        return self.log.append("program", {"program": program})

    def _emit(self, msg: mido.Message) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class LiveBackend(Backend):
    def __init__(self, log: SendLog, port_name: str | None = None, **kw):
        super().__init__(log, **kw)
        name = port_name or find_port(mido.get_output_names())
        if not name or PORT_HINT.lower() not in name.lower():
            raise TransportError(
                f"no port matching {PORT_HINT!r} found; refusing to open any other "
                f"device. Available: {mido.get_output_names()}"
            )
        self.port_name = name
        self._port = mido.open_output(name)

    def _emit(self, msg):
        self._port.send(msg)

    def close(self):
        self._port.close()


class MockBackend(Backend):
    """For tests/dry-run: records messages, optionally keeps a virtual grid."""

    def __init__(self, log: SendLog, **kw):
        super().__init__(log, **kw)
        self.sent: list[mido.Message] = []
        self.ccs: dict[int, int] = {}

    def _emit(self, msg):
        self.sent.append(msg)
        if msg.type == "control_change":
            self.ccs[msg.control] = msg.value
