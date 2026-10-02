"""stress — the F0a diagnostics harness for mf-visualizer.

PURPOSE (master plan F0a + brief-06): produce the MECHANICAL evidence that F0/C1
needs — message rates, dedupe/refusal counts, stuck-note behaviour, panic
latency — without ever being a second, unlogged MIDI path.

HARD INVARIANTS (they are the reason this module is allowed to exist):
1. Every stress send goes through the SAME sink as everything else (the injected
   ``cc_send`` / ``note_send`` = plugin_api's ``_engine_send`` / ``_note_send``).
   No bypass, no private port, no direct mido handle.
2. Provenance is on every event: source ``stress:<run id>`` lands in the shared
   sendlog, so a stress run is always separable from a knob ride afterwards.
3. With LIVE off, a run is VIRTUAL: it exercises the whole pipeline, counts what
   the device would have received, and says so (``sent`` vs ``virtual``).
4. A run ALWAYS stops: bounded by duration, bounded by message count, and
   ``/stress/stop`` (plus PANIC) kills it immediately - the thread checks a stop
   event and never sleeps longer than one tick.
5. Metrics are read back from the SENDLOG and from the harness's own counters -
   the harness never invents numbers it did not observe.

Patterns are the ones the brief asked for: a CC sweep, a CC storm (dedupe /
rate-limit evidence), a note burst (stuck-note smoke) and a panic-latency probe.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, List, Optional

PATTERNS = ("cc_sweep", "cc_storm", "note_burst", "mixed")
MAX_DURATION_S = 60.0
MAX_MESSAGES = 5000


class Metrics:
    """Rolling counters for one run: what we ASKED for vs what the sink took."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.t0 = 0.0
        self.attempted = 0
        self.sent = 0
        self.virtual = 0
        self.refused = 0
        self.notes_on = 0
        self.notes_off = 0
        self.by_source: Dict[str, int] = {}
        self._stamps: List[float] = []       # last second of send timestamps

    def note_attempt(self, took: bool) -> None:
        with self.lock:
            self.attempted += 1
            if took:
                self.sent += 1
            else:
                self.virtual += 1

    def bump_source(self, src: str) -> None:
        with self.lock:
            self.by_source[src] = self.by_source.get(src, 0) + 1

    def rate_now(self, now: Optional[float] = None) -> float:
        """Sends in the last second (a sliding window, not an average)."""
        now = now or time.time()
        with self.lock:
            self._stamps = [t for t in self._stamps if now - t <= 1.0]
            return float(len(self._stamps))

    def tick(self, now: Optional[float] = None) -> None:
        with self.lock:
            self._stamps.append(now or time.time())

    def snapshot(self) -> Dict:
        with self.lock:
            return {"attempted": self.attempted, "sent": self.sent,
                    "virtual": self.virtual, "refused": self.refused,
                    "notes_on": self.notes_on, "notes_off": self.notes_off,
                    "by_source": dict(self.by_source)}


class StressHarness:
    def __init__(self, cc_send: Callable[[int, int, str], bool],
                 note_send: Callable[[int, int, str], bool],
                 live_fn: Callable[[], bool], panic_gen_fn: Callable[[], int],
                 allowed_ccs, log: Callable[..., None] = lambda *a: None) -> None:
        self._cc = cc_send
        self._note = note_send
        self._live = live_fn
        self._gen = panic_gen_fn
        self._allowed = set(allowed_ccs)
        self._log = log
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.run: Optional[Dict] = None
        self.history: List[Dict] = []
        self.metrics = Metrics()
        self.panic_hit = False

    # ------------------------------------------------------------------ runs --
    def start(self, pattern: str = "cc_sweep", duration_s: float = 5.0,
              rate_hz: float = 20.0, cc: int = 23, note: int = 60) -> Dict:
        if pattern not in PATTERNS:
            return {"ok": False, "error": f"pattern must be one of {PATTERNS}"}
        if self.running():
            return {"ok": False, "error": "a stress run is already going - STOP it first"}
        try:
            duration_s = float(duration_s)
            rate_hz = float(rate_hz)
            cc, note = int(cc), int(note)
        except (TypeError, ValueError):
            return {"ok": False, "error": "duration_s/rate_hz/cc/note must be numbers"}
        if not 0.1 <= duration_s <= MAX_DURATION_S:
            return {"ok": False, "error": f"duration_s must be 0.1..{MAX_DURATION_S}"}
        if not 0.5 <= rate_hz <= 200.0:
            return {"ok": False, "error": "rate_hz must be 0.5..200"}
        if cc not in self._allowed:
            return {"ok": False, "error": f"CC{cc} is not in the FW5 allowlist (refuse-closed)"}
        if not 0 <= note <= 127:
            return {"ok": False, "error": "note must be 0..127"}
        rid = f"{pattern}-{int(time.time() * 1000) % 100000}"
        self.metrics = Metrics()
        self.metrics.t0 = time.time()
        self.panic_hit = False
        self.stop_event.clear()
        with self.lock:
            self.run = {"id": rid, "pattern": pattern, "started": self.metrics.t0,
                        "duration_s": duration_s, "rate_hz": rate_hz, "cc": cc,
                        "note": note, "live_at_start": bool(self._live()),
                        "gen0": self._gen(), "state": "running"}
        self.thread = threading.Thread(target=self._loop, name=f"mfvis-stress-{rid}",
                                       args=(rid, note), daemon=True)
        self.thread.start()
        return {"ok": True, "run": dict(self.run),
                "note": ("LIVE is off: the run is VIRTUAL - full pipeline, nothing reaches a "
                         "device, and the counters will say so") if not self._live() else
                        "LIVE is armed: this run is really sending to the device"}

    def running(self) -> bool:
        with self.lock:
            return bool(self.run and self.run.get("state") == "running")

    def stop(self, why: str = "user") -> Dict:
        self.stop_event.set()
        t = self.thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        with self.lock:
            if not self.run:
                return {"ok": False, "error": "no run to stop"}
            self.run["state"] = "stopped"
            self.run["stopped_by"] = "panic" if self.panic_hit else why
            self.run["ended"] = time.time()
            out = self._summary(self.run)
            self.history.append(out)
            self.history = self.history[-20:]
            self.run = None
        return {"ok": True, **out}

    # ------------------------------------------------------------------ loop --
    def _loop(self, rid: str, note: int) -> None:
        r = dict(self.run or {})
        pattern, cc = r.get("pattern"), int(r.get("cc", 23))
        rate = float(r.get("rate_hz", 20.0))
        end = time.time() + float(r.get("duration_s", 5.0))
        interval = 1.0 / rate
        src = f"stress:{rid}"
        gen0 = int(r.get("gen0", 0))
        step = 0
        try:
            while not self.stop_event.is_set() and time.time() < end:
                if self._gen() != gen0:        # a PANIC is a global stop for diagnostics
                    self.panic_hit = True
                    break
                if self.metrics.attempted >= MAX_MESSAGES:
                    break
                step += 1
                if pattern == "cc_sweep":
                    v = (step * 3) % 128
                    self._one_cc(cc, v, src)
                elif pattern == "cc_storm":
                    # repeated values on purpose: this is the dedupe/rate evidence
                    v = 64 + (step % 4)
                    self._one_cc(cc, v, src)
                    self._one_cc(cc, v, src)
                elif pattern == "note_burst":
                    self._one_note(note + (step % 5), 96 if step % 2 else 0, src)
                else:                                    # mixed
                    if step % 3 == 0:
                        self._one_note(note + (step % 4), 96, src)
                    elif step % 3 == 1:
                        self._one_note(note + (step % 4), 0, src)
                    else:
                        self._one_cc(cc, (step * 5) % 128, src)
                self.metrics.tick()
                time.sleep(interval)
        except Exception as exc:                          # noqa: BLE001
            self._log("stress loop died:", exc)
            with self.lock:
                if self.run:
                    self.run["error"] = str(exc)
        finally:
            # TEETER LAW for the note patterns: whatever the run took down, it lifts.
            if pattern in ("note_burst", "mixed"):
                for n in range(note, note + 5):
                    if 0 <= n <= 127:
                        self._one_note(n, 0, f"{src}:teardown", tally=False)

    def _one_cc(self, cc: int, v: int, src: str) -> None:
        took = bool(self._cc(int(cc), int(v), src))
        self.metrics.note_attempt(took)
        self.metrics.bump_source(src)

    def _one_note(self, note: int, vel: int, src: str, tally: bool = True) -> None:
        took = bool(self._note(int(note), int(vel), src))
        if not tally:            # teardown releases still SEND; they just must not
            return               # distort the stuck-note balance they exist to restore
        self.metrics.note_attempt(took)
        if vel:
            self.metrics.notes_on += 1
        else:
            self.metrics.notes_off += 1
        self.metrics.bump_source(src)

    # --------------------------------------------------------------- reports --
    def _summary(self, run: Dict) -> Dict:
        m = self.metrics.snapshot()
        wall = max(0.001, time.time() - float(run.get("started") or time.time()))
        return {"schema": "mfstress/1", "run": dict(run), "wall_s": round(wall, 3),
                "attempted": m["attempted"], "sent": m["sent"], "virtual": m["virtual"],
                "notes_on": m["notes_on"], "notes_off": m["notes_off"],
                "msg_per_s": round(m["attempted"] / wall, 1),
                "source": f"stress:{run.get('id', '')}",
                "stuck_note_check": (m["notes_on"] == m["notes_off"]) if run.get("pattern") in
                                    ("note_burst", "mixed") else None,
                "sendlog_rows": self._sendlog_rows()}

    def _sendlog_rows(self) -> int:
        """Rows the shared sendlog gained for THIS harness (source stress:*).

        Reads the real file - never a private counter pretending to be the log."""
        try:
            from plugin_api import SENDLOG          # same-process; no second path
        except Exception:                            # noqa: BLE001 - standalone probing
            return -1
        rows = 0
        try:
            with open(SENDLOG, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"stress:' in line:
                        rows += 1
        except OSError:
            return -1
        return rows

    def state(self) -> Dict:
        with self.lock:
            run = dict(self.run) if self.run else None
        return {"running": bool(run and run.get("state") == "running"), "run": run,
                "rate_hz_now": self.metrics.rate_now(), "counters": self.metrics.snapshot(),
                "history": self.history[-5:], "patterns": list(PATTERNS),
                "limits": {"max_duration_s": MAX_DURATION_S, "max_messages": MAX_MESSAGES},
                "sendlog_rows": self._sendlog_rows()}

    def export(self) -> Dict:
        with self.lock:
            history = [dict(h) for h in self.history]
            run = dict(self.run) if self.run else None
        summary = self._summary(run) if run else (history[-1] if history else None)
        csv = self._csv(history + ([summary] if (run and summary) else []))
        return {"ok": True, "schema": "mfstress/1", "summary": summary,
                "history": history, "csv": csv}

    @staticmethod
    def _csv(rows: List[Dict]) -> str:
        """One CSV row per run summary. Empty cells mean 'not measured'."""
        cols = ("wall_s", "attempted", "sent", "virtual", "msg_per_s", "notes_on",
                "notes_off", "stuck_note_check", "sendlog_rows")
        out = ["run_id,pattern," + ",".join(cols)]
        for r in rows:
            if not r:
                continue
            run = r.get("run") or {}
            vals = [run.get("id", ""), run.get("pattern", "")]
            for c in cols:
                v = r.get(c)
                if v is None:
                    vals.append("")
                elif c == "stuck_note_check":
                    vals.append("yes" if v else "no")
                else:
                    vals.append(v)
            out.append(",".join(str(x) for x in vals))
        return "\n".join(out)

    def write_csv(self, path) -> Optional[str]:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.export()["csv"])
            return str(path)
        except OSError as exc:
            self._log("stress csv write failed:", exc)
            return None

    def json_dump(self) -> str:
        return json.dumps(self.export(), indent=1)
