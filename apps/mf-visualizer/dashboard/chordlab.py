"""chordlab — the Chord Lab engine for mf-visualizer F2.5.

PURE-SOFTWARE lane: it builds chord voicings from theory.py and plays them
through the SAME note sink the keybed and the loop transport use, so the whole
trust contract holds unchanged (LIVE gate, sendlog provenance, refuse-closed
validation). No new CC surface, no device reads.

LAWS
- ONE held chord at a time: ``play`` lifts the previous chord first, so a
  half-lit device is impossible by construction.
- TEETER LAW: every note this engine takes down, it lifts again on EVERY exit
  path — explicit release, a moment-chord's expiry, a panic generation bump,
  or a LIVE drop mid-send. A panic bumps the caller's ``panic_gen``, which
  voids any pending expiry timer, and ``panic`` calls ``release_all`` itself.
- Honest provenance: sends are tagged ``chord:<id>`` in the shared sendlog, so
  an audition is distinguishable from a knob ride or a macro throw.
- PARAPHONY: the Freak has 4 voices; theory.py's warning text rides along in
  every response. The engine will still send a 5+ note chord (the note-offs are
  all tracked) but it never pretends the device has the voices.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

MAX_VOICES = 8          # refuse-closed: a chord bigger than this is a typo, not music


class ChordLab:
    def __init__(self, note_send: Callable[[int, int, str], bool],
                 gen_fn: Callable[[], int], live_fn: Callable[[], bool],
                 log: Callable[..., None] = lambda *a: None) -> None:
        self._send = note_send
        self._gen = gen_fn
        self._live = live_fn
        self._log = log
        self.lock = threading.RLock()
        self.held: List[int] = []            # notes we owe a note-off
        self.info: Dict = {}                 # last chord's description
        self.count = 0                       # auditions played this session

    # ------------------------------------------------------------------ play --
    def play(self, notes: Sequence[int], label: str = "", action: str = "hold",
             duration_ms: int = 1400, source: Optional[str] = None) -> Dict:
        if action not in ("hold", "moment"):
            return {"ok": False, "error": "action must be hold|moment"}
        try:
            ns = sorted({int(n) for n in notes})
        except (TypeError, ValueError):
            return {"ok": False, "error": "notes must be ints"}
        if not ns:
            return {"ok": False, "error": "empty chord refused"}
        if len(ns) > MAX_VOICES:
            return {"ok": False, "error": f"{len(ns)} notes refused (max {MAX_VOICES})"}
        bad = [n for n in ns if not 0 <= n <= 127]
        if bad:
            return {"ok": False, "error": f"note out of range 0..127: {bad}"}
        duration_ms = max(80, min(20000, int(duration_ms)))
        self.release()                        # one chord at a time
        src = source or "chord:lab"
        gen = self._gen()
        sent, virtual = [], []
        for n in ns:
            (sent if self._send(n, 96, src) else virtual).append(n)
        with self.lock:
            self.held = list(ns)
            self.count += 1
            self.info = {"label": label, "action": action, "notes": list(ns),
                         "sent": sent, "virtual": virtual, "source": src,
                         "gen": gen, "at": time.time()}
        if action == "moment":
            threading.Timer(duration_ms / 1000.0, self._expire, args=(gen, ns)).start()
        warn = self.warn(ns)
        return {"ok": True, "notes": ns, "label": label, "action": action,
                "sent": sent, "virtual": virtual, "live": bool(self._live()),
                "gen": gen, "paraphony": warn,
                "note": ("chord is HELD on the device - RELEASE (or PANIC) lifts it"
                         if action == "hold" else
                         f"chord plays for {duration_ms} ms, then lifts itself")}

    @staticmethod
    def warn(notes: Sequence[int]) -> Optional[str]:
        from theory import paraphony_warning
        return paraphony_warning(notes)

    # --------------------------------------------------------------- release --
    def _expire(self, gen: int, notes: Sequence[int]) -> None:
        """Expiry timer: void after a panic generation bump (a stale timer must
        never lift a chord the user re-pressed after PANIC)."""
        if gen != self._gen():
            return
        with self.lock:
            mine = [n for n in notes if n in self.held]
            self.held = [n for n in self.held if n not in mine]
            if not self.held:
                self.info = {}
        self._lift(mine)

    def release(self) -> Dict:
        with self.lock:
            notes = list(self.held)
        lifted = self._lift(notes)
        with self.lock:
            self.held = []
            self.info = {}
        return {"ok": True, "released": lifted}

    def release_all(self) -> int:
        with self.lock:
            notes = list(self.held)
            self.held = []
            self.info = {}
        return self._lift(notes)

    def _lift(self, notes: Sequence[int]) -> int:
        n = 0
        for note in notes:
            self._send(int(note), 0, "chord:release")
            n += 1
        return n

    # ----------------------------------------------------------------- state --
    def state(self) -> Dict:
        with self.lock:
            return {"held": list(self.held), "count": self.count,
                    **({"last": dict(self.info)} if self.info else {})}
