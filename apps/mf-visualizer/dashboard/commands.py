"""F1 - commanded shadow state, TX command layer and history (mf-visualizer).

Pure logic, stdlib only, offline/mock-safe:

* no mido, no device, no plugin_api import. Every outbound value leaves through
  the INJECTED ``send_fn(cc, value, source) -> bool``, which mirrors
  ``plugin_api._engine_send``'s contract: True ONLY when the value actually
  reached the device (or the mock claimed it).
* the clock is injected as well (``now=time.monotonic`` by default), so
  coalescing windows are testable without ever sleeping.
* the CommandBus is TX granularity. Continuous / modulator streams stay BELOW
  it: call :meth:`CommandBus.note_stream_sample` for those, never ``execute``.

Lane laws encoded here:

* provenance is EXACTLY one of 'commanded' / 'observed' / 'unknown'; observed is
  never claimed without a grounding source.
* a REFUSED send must not pollute the shadow (no 'commanded' stamp, provenance
  left exactly as it was).
* partial apply stays VISIBLE per-change - there is no all-or-nothing fiction.
* coalescing merges only consecutive, same-author, same-cc-key, sub-window,
  non-atomic entries; authors NEVER merge.
* undo/redo are INVERSE COMMANDS replayed through the same sink. They are
  commands, not device magic: the language is 'replay', never 'device undo'.
"""

import json
import threading
import time
import uuid
from collections import deque
from pathlib import Path

__all__ = [
    "ShadowState", "CommandBus",
    "COMMANDED", "OBSERVED", "UNKNOWN", "PROVENANCES", "INVALIDATORS",
]

# provenance vocabulary - exactly three values, no synonyms grow here
COMMANDED = "commanded"     # we last set it
OBSERVED = "observed"       # panel echo / preset / reload grounded it
UNKNOWN = "unknown"         # stale or never grounded
PROVENANCES = (COMMANDED, OBSERVED, UNKNOWN)

# the only reasons allowed to blow away all provenance
INVALIDATORS = ("disconnect", "preset_switch", "panel_edit_bulk")

CC_MIN, CC_MAX = 0, 127


def _new_id():
    return "cmd-" + uuid.uuid4().hex[:12]


def _cc_key(changes):
    """Change-key: the identical frozenset of ccs a command touches."""
    return frozenset(int(c["cc"]) for c in changes)


def _norm_changes(changes):
    if not isinstance(changes, (list, tuple)):
        raise ValueError("changes must be a list")
    out = []
    for ch in changes:
        if isinstance(ch, dict):
            cc, value = ch.get("cc"), ch.get("value")
        elif isinstance(ch, (list, tuple)) and len(ch) == 2:
            cc, value = ch
        else:
            raise ValueError(f"bad change {ch!r}")
        if isinstance(cc, bool) or isinstance(value, bool):
            raise ValueError(f"bool is not a cc/value: {ch!r}")
        out.append({"cc": int(cc), "value": int(value)})
    return out


class ShadowState:
    """Commanded authority per CC: value + provenance + staleness.

    One RLock mirrors plugin_api.State.lock (reentrant, single-reducer
    discipline) so the bus, the reducer and the UI can share it safely.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.entries = {}   # cc -> {value, provenance, source, stale_reason}

    # --- reads ----------------------------------------------------------
    def get(self, cc):
        """(value, provenance). Never-seen cc -> (None, 'unknown')."""
        with self.lock:
            e = self.entries.get(int(cc))
            if e is None:
                return (None, UNKNOWN)
            return (e["value"], e["provenance"])

    def entry(self, cc):
        """(value, provenance) or None when this cc has never been grounded."""
        with self.lock:
            e = self.entries.get(int(cc))
            return None if e is None else (e["value"], e["provenance"])

    def items(self):
        with self.lock:
            return [(cc, dict(e)) for cc, e in self.entries.items()]

    def commanded_values(self):
        with self.lock:
            return {cc: e["value"] for cc, e in self.entries.items()
                    if e["provenance"] == COMMANDED}

    # --- writes ---------------------------------------------------------
    def set_commanded(self, cc, v):
        with self.lock:
            self.entries[int(cc)] = {"value": int(v), "provenance": COMMANDED,
                                     "source": "command", "stale_reason": None}

    def set_observed(self, cc, v, source):
        """Grounding is mandatory: no source, no 'observed' claim."""
        if not isinstance(source, str) or not source.strip():
            raise ValueError("observed state requires a grounding source")
        with self.lock:
            self.entries[int(cc)] = {"value": int(v), "provenance": OBSERVED,
                                     "source": source.strip(), "stale_reason": None}

    def restore_entry(self, cc, value, provenance):
        """Put one entry back EXACTLY as it was (undo replay)."""
        if provenance not in PROVENANCES:
            raise ValueError(f"bad provenance {provenance!r}")
        with self.lock:
            if value is None:
                self.entries.pop(int(cc), None)
                return
            self.entries[int(cc)] = {"value": int(value), "provenance": provenance,
                                     "source": "undo", "stale_reason": None}

    def invalidate(self, reason):
        """Every entry -> unknown, LAST-KNOWN value kept and labelled stale.
        Refuses unknown reasons (refuse-closed)."""
        if reason not in INVALIDATORS:
            raise ValueError(f"unknown invalidator {reason!r}; expected {INVALIDATORS}")
        with self.lock:
            for e in self.entries.values():
                e["provenance"] = UNKNOWN
                e["stale_reason"] = reason
            return len(self.entries)


class CommandBus:
    """TX command layer: one send per change, per-send outcomes, honest history.

    ``send_fn(cc:int, value:int, source:str) -> bool`` is the ONLY egress.
    True means the value reached the device (or the mock claimed it) - exactly
    the ``_engine_send`` contract. Anything else, including a raised exception,
    is a refusal.
    """

    def __init__(self, shadow, send_fn, history_path=None, ring_size=500,
                 coalesce_s=0.4, now=time.monotonic):
        if not callable(send_fn):
            raise ValueError("send_fn must be callable")
        ring_size = int(ring_size)
        if ring_size < 1:
            raise ValueError("ring_size must be >= 1")
        self.shadow = shadow
        self.send_fn = send_fn
        self.history_path = str(history_path) if history_path else None
        self.ring_size = ring_size
        self.coalesce_s = float(coalesce_s)
        self.now = now
        self.lock = threading.RLock()
        self.history = deque(maxlen=ring_size)
        self.redo_stack = []
        self.streams = deque(maxlen=256)   # stream samples: below TX granularity
        self.stream_count = 0
        self.load_error = None             # truthful: never a fake restore
        self.save_error = None
        if self.history_path:
            self._load()

    # --- egress ---------------------------------------------------------
    def _send(self, cc, value, source):
        """Refuse-closed: an exception is a refusal, never a pretend send."""
        try:
            return bool(self.send_fn(int(cc), int(value), source))
        except Exception:  # noqa: BLE001
            return False

    # --- command execution ---------------------------------------------
    def execute(self, cmd):
        """Run one TX command. Nothing is stamped as commanded unless it SENT."""
        c = self._normalize(cmd)
        src = "cmd:" + c["author"]
        pre = {}
        outcomes = []
        with self.lock:
            for ch in c["changes"]:
                cc, val = ch["cc"], ch["value"]
                pre[cc] = self.shadow.entry(cc)   # captured BEFORE we touch anything
                if not (CC_MIN <= cc <= CC_MAX) or not (CC_MIN <= val <= CC_MAX):
                    outcomes.append({"cc": cc, "value": val, "ok": False,
                                     "why": "out-of-range"})
                    continue
                if self._send(cc, val, src):
                    self.shadow.set_commanded(cc, val)
                    outcomes.append({"cc": cc, "value": val, "ok": True, "why": "sent"})
                else:
                    # a refused send must NOT pollute the shadow
                    outcomes.append({"cc": cc, "value": val, "ok": False, "why": "refused"})
            c["pre"] = pre
            c["outcomes"] = outcomes
            c["merged_count"] = 0
            c["undone"] = False
            self.redo_stack.clear()   # standard law: a fresh execute forks history
            stored = self._append(c)
            self._save()
            return stored

    def _normalize(self, cmd):
        if not isinstance(cmd, dict):
            raise ValueError("command must be a dict")
        author = cmd.get("author", "anon")
        if author is None:
            author = "anon"
        if not isinstance(author, str) or not author.strip():
            raise ValueError("author must be a non-empty string")
        return {
            "id": str(cmd.get("id") or _new_id()),
            "author": author.strip(),
            "action_id": str(cmd.get("action_id") or "command"),
            "changes": _norm_changes(cmd.get("changes") or []),
            "atomic": bool(cmd.get("atomic", False)),
            "ts": float(cmd.get("ts", self.now())),
            "note": str(cmd.get("note") or ""),
            "outcomes": [],
        }

    def _append(self, c):
        """Ring append with the COALESCING LAW."""
        with self.lock:
            if self.history:
                last = self.history[-1]
                if (not c["atomic"] and not last.get("atomic")
                        and not last.get("undone")
                        and last["author"] == c["author"]
                        and _cc_key(last["changes"]) == _cc_key(c["changes"])
                        and (c["ts"] - last["ts"]) < self.coalesce_s):
                    last["changes"] = c["changes"]      # newest values win
                    last["outcomes"] = c["outcomes"]
                    last["ts"] = c["ts"]
                    last["merged_count"] = int(last.get("merged_count", 0)) + 1
                    return last                          # original pre/id/author kept
            self.history.append(c)
            return c

    # --- streams (below TX granularity) ---------------------------------
    def note_stream_sample(self, author, cc, value):
        """Continuous/modulator sample: recorded, NEVER sent, never in history."""
        with self.lock:
            self.streams.append({"author": str(author), "cc": int(cc),
                                 "value": int(value), "ts": self.now()})
            self.stream_count += 1

    # --- undo / redo (inverse commands) ---------------------------------
    def undo(self, author=None):
        """Build the INVERSE of the newest undoable entry and replay it."""
        with self.lock:
            entry = None
            for e in reversed(self.history):
                if e.get("undone"):
                    continue
                if e["author"].startswith(("undo:", "redo:")):
                    continue
                if author is not None and e["author"] != author:
                    continue
                entry = e
                break
            if entry is None:
                return None

            src = "undo:" + entry["author"]
            changes, ghosts, outcomes = [], [], []
            for ch in entry["changes"]:
                cc = int(ch["cc"])
                pre = entry.get("pre", {}).get(cc)
                if pre is None:
                    ghosts.append(cc)          # never grounded: nothing to send
                    continue
                val = int(pre[0])
                if self._send(cc, val, src):
                    self.shadow.restore_entry(cc, val, pre[1])   # exact provenance back
                    changes.append({"cc": cc, "value": val})
                    outcomes.append({"cc": cc, "value": val, "ok": True,
                                     "why": "restored-pre-value"})
                else:
                    changes.append({"cc": cc, "value": val})
                    outcomes.append({"cc": cc, "value": val, "ok": False, "why": "refused"})
            for cc in ghosts:
                outcomes.append({"cc": cc, "value": None, "ok": False,
                                 "why": "no-pre-value"})

            inv = {"id": _new_id(), "author": src, "action_id": "undo",
                   "changes": changes, "atomic": False, "ts": float(self.now()),
                   "outcomes": outcomes,
                   "pre": {int(ch["cc"]): None for ch in entry["changes"]},
                   "merged_count": 0, "undone": False, "note": ""}
            entry["undone"] = True
            self.history.append(inv)
            self.redo_stack.append(entry)
            self._save()
            return inv

    def redo(self):
        """Symmetric replay of the newest undone entry. None when the branch is
        empty (a fresh non-undo execute clears it, standard law)."""
        with self.lock:
            if not self.redo_stack:
                return None
            entry = self.redo_stack.pop()
            src = "redo:" + entry["author"]
            outcomes = []
            changes = []
            for ch in entry["changes"]:
                cc, val = int(ch["cc"]), int(ch["value"])
                changes.append({"cc": cc, "value": val})
                if self._send(cc, val, src):
                    self.shadow.set_commanded(cc, val)
                    outcomes.append({"cc": cc, "value": val, "ok": True, "why": "resent"})
                else:
                    outcomes.append({"cc": cc, "value": val, "ok": False, "why": "refused"})
            entry["undone"] = False
            cmd = {"id": _new_id(), "author": src, "action_id": "redo",
                   "changes": changes, "atomic": True, "ts": float(self.now()),
                   "outcomes": outcomes,
                   "pre": {int(ch["cc"]): None for ch in entry["changes"]},
                   "merged_count": 0, "undone": False, "note": ""}
            self.history.append(cmd)
            self._save()
            return cmd

    # --- snapshot / restore ---------------------------------------------
    def snapshot(self, name):
        """Capture current COMMANDED values only; anything else is refused
        per cc with reason 'no-commanded-value'."""
        params, skipped = {}, []
        with self.lock:
            for cc, e in self.shadow.items():
                if e["provenance"] == COMMANDED:
                    params[int(cc)] = e["value"]
                else:
                    skipped.append({"cc": int(cc), "why": "no-commanded-value"})
        return {"name": str(name), "params": params, "skipped": skipped,
                "ts": float(self.now())}

    def restore(self, snap, author="restore"):
        """Replay a slot as ONE command. The text says 'replay' - this is not a
        device undo/restore and must never claim to be one."""
        if not isinstance(snap, dict):
            raise ValueError("snapshot must be a dict")
        name = snap.get("name")
        params = snap.get("params") or {}
        changes = [{"cc": int(cc), "value": int(v)} for cc, v in sorted(params.items())]
        res = self.execute({"author": author, "action_id": "restore-from-slot",
                            "changes": changes, "atomic": True,
                            "note": f"replay slot {name!r}"})
        res["note"] = f"replay slot {name!r}"
        for o in res["outcomes"]:
            o["why"] = f"{o['why']} (replay slot {name!r})"
        self._save()
        return res

    # --- persistence -----------------------------------------------------
    def _ser(self, c):
        d = dict(c)
        d["pre"] = {str(cc): (list(v) if v is not None else None)
                    for cc, v in (c.get("pre") or {}).items()}
        d["changes"] = [{"cc": int(x["cc"]), "value": int(x["value"])}
                        for x in c.get("changes", [])]
        return d

    def _deser(self, d):
        c = dict(d)
        pre = {}
        for k, v in (c.get("pre") or {}).items():
            pre[int(k)] = (tuple(v) if v else None)
        c["pre"] = pre
        c.setdefault("outcomes", [])
        c.setdefault("merged_count", 0)
        c.setdefault("undone", False)
        c.setdefault("atomic", False)
        c.setdefault("note", "")
        return c

    def export(self):
        """JSON-safe plain-dict view of the whole ring."""
        with self.lock:
            return [self._ser(c) for c in self.history]

    def _save(self):
        if not self.history_path:
            return
        try:
            p = Path(self.history_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.export(), indent=1), encoding="utf-8")
            self.save_error = None
        except OSError as exc:
            self.save_error = f"{type(exc).__name__}: {exc}"

    def _load(self):
        """Corrupt file -> start empty AND record load_error. Never fake-restore."""
        try:
            p = Path(self.history_path)
            if not p.exists():
                return
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("history file is not a list")
            loaded = []
            for d in raw:
                if not isinstance(d, dict):
                    raise ValueError("history entry is not an object")
                loaded.append(self._deser(d))
            self.history.extend(loaded)   # maxlen trims if the file outgrew the ring
        except Exception as exc:  # noqa: BLE001
            self.history.clear()
            self.load_error = f"{type(exc).__name__}: {exc}"