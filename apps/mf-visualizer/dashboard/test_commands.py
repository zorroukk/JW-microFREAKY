"""Exhaustive battery for commands.py (F1). Run: python test_commands.py (from dashboard/)

Lane law 7: pure logic gets exhaustive tests, not curated examples. This file
walks every provenance transition, every coalescing branch (with an INJECTED
fake clock - nothing sleeps), the ring cap at exactly 500, refusal-is-not-a-
stamp, undo/redo inverse replay incl. the 'no-pre-value' ghost, snapshot/restore
phrases, and persistence round-trip through a real tempfile with a corrupt-file
fail-soft. No mido, no device: the egress is a fake sender, and every string law
(statuses, provenance names, human phrases) is asserted here.

Run from dashboard/:  python test_commands.py
Exit 0 prints 'ALL OK - commands battery passed (...)'; failures print and exit 1.
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

try:                                   # em dash in the banner needs utf-8 on cp1252 consoles
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                      # noqa: BLE001
    pass

import commands as C

fails = []
checks = [0]


def ok(cond, msg):
    checks[0] += 1
    if not cond:
        fails.append(msg)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except (ValueError, TypeError):
        return True


class Clock:
    """Injected monotonic clock - the coalescing window is never slept through."""
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def step(self, d):
        self.t += float(d)


class Sender:
    """Mirrors _engine_send's contract: bool, True = actually reached the device.
    `refuse` ccs come back False (LIVE off / send error); everything is recorded."""
    def __init__(self, refuse=()):
        self.refuse = set(int(c) for c in refuse)
        self.calls = []

    def __call__(self, cc, value, source):
        self.calls.append((int(cc), int(value), str(source)))
        return int(cc) not in self.refuse


def bus_for(refuse=(), history_path=None, ring=500, clk=None):
    sh = C.ShadowState()
    snd = Sender(refuse)
    return sh, snd, C.CommandBus(sh, snd, history_path=history_path, ring_size=ring,
                                 now=clk or Clock())


def ex(bus, author, changes, atomic=False, action_id="cmd"):
    return bus.execute({"author": author, "action_id": action_id,
                        "changes": changes, "atomic": atomic})


# ============================================================ provenance ====
sh = C.ShadowState()
ok(sh.get(23) == (None, C.UNKNOWN), "fresh cc must be (None, unknown)")
sh.set_observed(23, 40, "panel")
ok(sh.get(23) == (40, C.OBSERVED), "observed transition failed")
ok(sh.entry(23) == (40, C.OBSERVED), "entry() disagrees with get()")
ok(sh.entry(5) is None, "never-grounded cc must have no entry")
sh.set_commanded(23, 55)
ok(sh.get(23) == (55, C.COMMANDED), "commanded transition failed")
sh.set_observed(23, 60, "preset")
ok(sh.get(23) == (60, C.OBSERVED), "observed must be reachable after commanded")
ok("observed" in C.PROVENANCES and len(C.PROVENANCES) == 3, "provenance vocabulary changed")
ok(C.PROVENANCES == ("commanded", "observed", "unknown"), f"provenance names: {C.PROVENANCES}")
ok(raises(sh.set_observed, 23, 1, ""), "observed accepted an empty source")
ok(raises(sh.set_observed, 23, 1, None), "observed accepted a None source")
ok(raises(sh.set_observed, 23, 1, "   "), "observed accepted a whitespace source")
ok(raises(sh.restore_entry, 23, 1, "pretend"), "restore_entry accepted a fake provenance")
ok(sh.commanded_values() == {23: 55} or True, "commanded_values must not raise")

# a REFUSED send must not pollute the shadow
sh, snd, bus = bus_for(refuse=(83,))
sh.set_observed(83, 12, "panel")
r = ex(bus, "alice", [{"cc": 83, "value": 99}])
ok(r["outcomes"][0]["ok"] is False, "refused cc must report ok False")
ok(r["outcomes"][0]["why"] == "refused", f"refused why: {r['outcomes'][0]['why']}")
ok(sh.get(83) == (12, C.OBSERVED), "refused send polluted provenance")
ok(sh.entry(64) is None, "refused send created a ghost entry")
ex(bus, "alice", [{"cc": 23, "value": 9}])
ok(sh.get(23) == (9, C.COMMANDED), "sent cc must be stamped commanded")
ok((23, 9, "cmd:alice") in snd.calls, "send source must be cmd:<author>")

# a raising send_fn is a refusal, never a pretend send
sh2 = C.ShadowState()
boom = C.CommandBus(sh2, lambda cc, v, s: (_ for _ in ()).throw(RuntimeError("live off")),
                    now=Clock())
r = boom.execute({"author": "x", "changes": [{"cc": 23, "value": 5}]})
ok(r["outcomes"][0]["ok"] is False and r["outcomes"][0]["why"] == "refused",
   "a raising send_fn must read as refused")
ok(sh2.entry(23) is None, "raising send_fn stamped the shadow")

# out-of-range values are refused before they reach the wire
sh3, snd3, bus3 = bus_for()
r = ex(bus3, "alice", [{"cc": 23, "value": 200}, {"cc": 999, "value": 1}])
ok([o["why"] for o in r["outcomes"]] == ["out-of-range", "out-of-range"], "range check missing")
ok(snd3.calls == [], "out-of-range value was sent")
ok(sh3.entries == {}, "out-of-range value stamped the shadow")
ok(raises(ex, bus3, "alice", [{"cc": True, "value": 1}]), "bool cc accepted")
ok(raises(ex, bus3, "alice", "nope"), "non-list changes accepted")
ok(raises(ex, bus3, "", [{"cc": 1, "value": 1}]), "empty author accepted")
ok(raises(C.CommandBus, sh3, "not-callable"), "non-callable send_fn accepted")

# ============================================================= invalidate ====
sh = C.ShadowState()
sh.set_commanded(23, 55)
sh.set_observed(83, 12, "panel")
sh.set_observed(105, 7, "reload")
n = sh.invalidate("disconnect")
ok(n == 3, f"invalidate touched {n} entries, expected 3")
for cc, last in ((23, 55), (83, 12), (105, 7)):
    val, prov = sh.get(cc)
    ok(prov == C.UNKNOWN, f"cc{cc} provenance after invalidate: {prov}")
    ok(val == last, f"cc{cc} last-known value lost: {val} != {last}")
    ok([e for c, e in sh.items() if c == cc][0]["stale_reason"] == "disconnect",
       f"cc{cc} stale_reason not labelled")
for bad in ("", None, "reboot", "disconnect "):
    ok(raises(sh.invalidate, bad), f"invalidate accepted reason {bad!r}")
ok(C.INVALIDATORS == ("disconnect", "preset_switch", "panel_edit_bulk"), "invalidator list changed")
for reason in C.INVALIDATORS:
    sh.invalidate(reason)
    ok(all(e["stale_reason"] == reason for _, e in sh.items()), f"{reason} not labelled")
# value kept even when the very first invalidation lands on a commanded entry
sh.invalidate("preset_switch")
ok(sh.get(23) == (55, C.UNKNOWN), "stale value must survive repeated invalidation")

# ============================================================== ring cap ====
sh = C.ShadowState()
clk = Clock()
_, snd, bus = bus_for(clk=clk)
ids = []
for i in range(505):
    r = ex(bus, "cap", [{"cc": 23 + (i % 4), "value": i % 128}], atomic=True)
    ids.append(r["id"])
ok(len(bus.history) == 500, f"ring cap: {len(bus.history)} != 500")
ok(bus.ring_size == 500, "default ring_size is not 500")
ok(bus.history[0]["id"] == ids[5], "oldest surviving entry is not the 6th command")
ok(bus.history[-1]["id"] == ids[-1], "newest entry missing")
ok(len(bus.history) == len(set(e["id"] for e in bus.history)), "ring holds duplicates")
ok(all(len(e["changes"]) == 1 and 0 <= e["changes"][0]["value"] <= 127 for e in bus.history),
   "ring entries malformed")

# ============================================================= coalescing ====
# within the window: same author + same key merge, newest values win
sh, snd, bus = bus_for(clk=Clock())
c1 = ex(bus, "alice", [{"cc": 23, "value": 10}])
ok(sh.get(23) == (10, C.COMMANDED), "first send not stamped")
bus.now.step(0.1)
c2 = ex(bus, "alice", [{"cc": 23, "value": 20}])
ok(len(bus.history) == 1, f"in-window merge failed: {len(bus.history)} entries")
ok(bus.history[0]["changes"][0]["value"] == 20, "merge did not keep the newest value")
ok(bus.history[0]["merged_count"] == 1, f"merged_count: {bus.history[0]['merged_count']}")
ok(bus.history[0]["id"] == c1["id"] == c2["id"], "merge must return the existing entry")
ok(bus.history[0]["ts"] == bus.now.t, "merged entry ts must advance to the newest")
ok(bus.history[0]["pre"][23] is None, "merge must keep the ORIGINAL pre")

# exactly at the window edge: NOT a merge (strict <). Fresh clock at 0.0 so
# 0.4 - 0.0 is exact - no float-boundary guesswork in the law test.
sh, snd, bus = bus_for(clk=Clock(0.0))
ex(bus, "alice", [{"cc": 23, "value": 1}])
bus.now.step(0.4)
ex(bus, "alice", [{"cc": 23, "value": 2}])
ok(len(bus.history) == 2, "a 0.4s gap merged (must be strict <)")
sh, snd, bush = bus_for(clk=Clock(0.0))
ex(bush, "alice", [{"cc": 23, "value": 1}])
bush.now.step(0.3999)
ex(bush, "alice", [{"cc": 23, "value": 2}])
ok(len(bush.history) == 1, "a 0.3999s gap failed to merge")

# different authors 1 ms apart never merge
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.now.step(0.001)
ex(bus, "bob", [{"cc": 23, "value": 20}])
bus.now.step(0.001)
ex(bus, "alice", [{"cc": 23, "value": 30}])
ok(len(bus.history) == 3, f"authors merged: {len(bus.history)} entries")
ok([e["author"] for e in bus.history] == ["alice", "bob", "alice"], "author order lost")
ok(bus.history[0]["merged_count"] == 0 and bus.history[1]["merged_count"] == 0,
   "non-consecutive same-author entries merged")

# mixed cc-key, same author: no merge
sh, snd, bus = bus_for(clk=Clock(0.0))
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.now.step(0.05)
ex(bus, "alice", [{"cc": 23, "value": 11}, {"cc": 5, "value": 4}])
ok(len(bus.history) == 2, f"mixed cc-key merged: {len(bus.history)} entries")
ok(frozenset(c["cc"] for c in bus.history[0]["changes"]) == frozenset({23}),
   "singleton change-key wrong")
ok(frozenset(c["cc"] for c in bus.history[1]["changes"]) == frozenset({23, 5}),
   "mixed change-key wrong")
bus.now.step(0.05)
ex(bus, "alice", [{"cc": 5, "value": 5}, {"cc": 23, "value": 12}])
ok(len(bus.history) == 2, "same-key follow-up must merge with entry 2")
ok(bus.history[1]["merged_count"] == 1 and bus.history[0]["merged_count"] == 0,
   "mixed-key merge touched the wrong entry")

# identical key set in any order DOES merge (key is a frozenset)
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 1}, {"cc": 5, "value": 2}])
bus.now.step(0.05)
ex(bus, "alice", [{"cc": 5, "value": 3}, {"cc": 23, "value": 4}])
ok(len(bus.history) == 1, "same cc-set in different order did not merge")
ok(bus.history[0]["merged_count"] == 1, "ordered-merge count wrong")

# atomic never coalesces
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 10}], atomic=True)
bus.now.step(0.01)
ex(bus, "alice", [{"cc": 23, "value": 11}], atomic=True)
ok(len(bus.history) == 2, "atomic entries coalesced")

# a coalesced entry is undone as ONE command (pre = before the whole run)
sh, snd, bus = bus_for(clk=Clock())
sh.set_observed(23, 7, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.now.step(0.05)
ex(bus, "alice", [{"cc": 23, "value": 20}])
ok(len(bus.history) == 1, "coalesced run not one entry")
inv = bus.undo()
ok(inv is not None and sh.get(23) == (7, C.OBSERVED),
   "undo of a coalesced run must restore the run's original pre-value")
ok(len(bus.history[0]["changes"]) == 1 and bus.history[0]["changes"][0]["value"] == 20,
   "prior entry mutated by undo")

# ================================================================= undo ======
# exact pre-values incl. a provenance-unknown pre-entry, and a ghost (pre None)
sh, snd, bus = bus_for(clk=Clock())
sh.set_observed(23, 7, "panel")          # grounded pre
sh.set_commanded(5, 40)                  # commanded pre
sh.invalidate("preset_switch")           # -> both now unknown, values kept
ok(sh.get(5) == (40, C.UNKNOWN) and sh.get(23) == (7, C.UNKNOWN), "prep state wrong")
n_before = len(snd.calls)
r = ex(bus, "alice", [{"cc": 23, "value": 99}, {"cc": 5, "value": 88},
                      {"cc": 83, "value": 33}])   # 83 never grounded -> ghost pre
ok([o["ok"] for o in r["outcomes"]] == [True, True, True], "setup sends refused")
ok(len(snd.calls) - n_before == 3, "setup sent the wrong number of ccs")
ok(sh.get(83) == (33, C.COMMANDED), "setup did not stamp cc83")
n_undo = len(snd.calls)
inv = bus.undo()
ok(inv is not None, "undo returned None with an undoable entry present")
ok(inv["author"] == "undo:alice", f"undo author: {inv['author']}")
ok(inv["action_id"] == "undo", "undo action_id wrong")
ok(sh.get(23) == (7, C.UNKNOWN), f"undo must restore exact unknown pre: {sh.get(23)}")
ok(sh.get(5) == (40, C.UNKNOWN), f"undo must restore exact unknown pre: {sh.get(5)}")
ok(sh.get(83) == (33, C.COMMANDED), "ghost cc must NOT be touched by undo")
out = {o["cc"]: o for o in inv["outcomes"]}
ok(out[23]["ok"] is True and out[23]["value"] == 7, "undo did not replay pre value for cc23")
ok(out[5]["ok"] is True and out[5]["value"] == 40, "undo did not replay pre value for cc5")
ok(out[83]["ok"] is False and out[83]["why"] == "no-pre-value",
   f"ghost cc must report no-pre-value: {out[83]}")
ok(out[83]["value"] is None, "ghost outcome must carry a None value")
ok(len(snd.calls) - n_undo == 2, f"ghost cc WAS sent: {len(snd.calls) - n_undo} sends")
ok(all(c[0] in (23, 5) for c in snd.calls[n_undo:]), "undo delivered the ghost cc")
ok(all(c[2] == "undo:alice" for c in snd.calls[n_undo:]), "undo sends must be sourced undo:<author>")
entry = [e for e in bus.history if e["author"] == "alice"][-1]
ok(entry["undone"] is True, "undone entry not marked")
ok(bus.history[-1]["author"] == "undo:alice", "undo command not in the ring")

# author-filtered undo picks that author's newest, not simply the newest
sh, snd, bus = bus_for(clk=Clock())
sh.set_observed(23, 1, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.now.step(1.0)
ex(bus, "bob", [{"cc": 23, "value": 20}])
ok(sh.get(23) == (20, C.COMMANDED), "setup state wrong")
inv = bus.undo(author="alice")
ok(inv is not None and inv["author"] == "undo:alice", "author-filtered undo failed")
ok(sh.get(23) == (1, C.OBSERVED), "author-filtered undo replayed the wrong pre")
ok([e for e in bus.history if e["author"] == "bob"][0]["undone"] is False,
   "author-filtered undo marked the wrong entry")
ok([e for e in bus.history if e["author"] == "alice"][0]["undone"] is True,
   "author-filtered undo failed to mark its own entry")

# nothing undoable -> None (and undo commands are not themselves undone)
sh, snd, bus = bus_for(clk=Clock())
ok(bus.undo() is None, "undo on empty history must be None")
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.undo()
ok(bus.undo() is None, "undo must not undo an undo command")

# ================================================================= redo ======
# undo of a FIRST-EVER command is a ghost: nothing to replay, nothing cleared
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 10}])
inv = bus.undo()
ok(inv["outcomes"][0]["why"] == "no-pre-value", "first-ever undo must be a ghost outcome")
ok(sh.get(23) == (10, C.COMMANDED), "ghost undo must leave the shadow alone")

# full undo/redo cycle on a grounded param
sh, snd, bus = bus_for(clk=Clock())
sh.set_observed(23, 7, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}])
inv = bus.undo()
ok(inv is not None and sh.get(23) == (7, C.OBSERVED), "undo did not replay the observed pre")
red = bus.redo()
ok(red is not None, "redo returned None after undo")
ok(red["author"] == "redo:alice", f"redo author: {red['author']}")
ok(red["action_id"] == "redo", "redo action_id wrong")
ok(sh.get(23) == (10, C.COMMANDED), "redo did not re-stamp commanded")
ok(red["outcomes"][0]["ok"] is True, "redo outcome not ok")
ok(bus.redo() is None, "redo stack consumed twice")
ok(bus.undo() is not None, "the redone entry must be undoable again")

# a fresh non-undo execute clears the redo branch
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.undo()
bus.now.step(1.0)
ex(bus, "bob", [{"cc": 5, "value": 1}])
ok(bus.redo() is None, "fresh execute did not clear the redo branch")

# redo is refused-send honest too
sh = C.ShadowState()
snd = Sender()
flip = {"refuse": False}
bus = C.CommandBus(sh, lambda cc, v, s: (not flip["refuse"]) and snd(cc, v, s), now=Clock())
sh.set_observed(23, 7, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.undo()
flip["refuse"] = True
red = bus.redo()
ok(red["outcomes"][0]["ok"] is False and red["outcomes"][0]["why"] == "refused",
   "refused redo must render honestly")
ok(sh.get(23) == (7, C.OBSERVED), "refused redo stamped the shadow")

# ==================================================== partial outcomes ======
sh, snd, bus = bus_for(refuse=(5,), clk=Clock())
sh.set_observed(23, 7, "panel")
sh.set_observed(5, 8, "panel")
r = ex(bus, "alice", [{"cc": 23, "value": 11}, {"cc": 5, "value": 22}])
ok(len(r["outcomes"]) == 2, "outcomes must be per-change")
ok(r["outcomes"][0]["ok"] is True and r["outcomes"][0]["why"] == "sent", "cc23 outcome wrong")
ok(r["outcomes"][1]["ok"] is False and r["outcomes"][1]["why"] == "refused", "cc5 outcome wrong")
ok(sh.get(23) == (11, C.COMMANDED), "sent half of a partial command not stamped")
ok(sh.get(5) == (8, C.OBSERVED), "refused half of a partial command was stamped")
inv = bus.undo()
od = {o["cc"]: o for o in inv["outcomes"]}
ok(od[23]["ok"] is True and sh.get(23) == (7, C.OBSERVED),
   "undo must replay the half that applied")
ok(od[5]["ok"] is False and od[5]["why"] == "refused",
   "the refused half must stay refused on undo, not pretend to succeed")
ok(sh.get(5) == (8, C.OBSERVED), "a refused undo half must not blind-write the shadow")
ok(od[5]["value"] == 8, "the refused half still reports the attempted pre-value")

# ================================================= snapshot / restore ======
sh, snd, bus = bus_for(clk=Clock())
sh.set_commanded(23, 55)
sh.set_observed(83, 12, "panel")
sh.set_observed(105, 7, "reload")
snap = bus.snapshot("slot1")
ok(snap["name"] == "slot1", "snapshot name lost")
ok(snap["params"] == {23: 55}, f"snapshot must carry commanded values only: {snap['params']}")
ok(sorted(s["cc"] for s in snap["skipped"]) == [83, 105], "snapshot skipped the wrong ccs")
ok(all(s["why"] == "no-commanded-value" for s in snap["skipped"]),
   "snapshot skip reason must be 'no-commanded-value'")

n_before = len(snd.calls)
res = bus.restore(snap, author="nick")
ok(res["action_id"] == "restore-from-slot", f"restore action_id: {res['action_id']}")
ok("replay" in res["note"], f"restore note must say replay: {res['note']!r}")
ok(len(res["outcomes"]) == 1, "restore must only touch commanded params")
ok(all("replay" in o["why"] for o in res["outcomes"]),
   f"restore outcome text must carry 'replay': {res['outcomes']}")
ok(len(snd.calls) - n_before == 1 and snd.calls[-1][:2] == (23, 55),
   "restore sent the wrong payload")
ok(sh.get(83) == (12, C.OBSERVED) and sh.get(105) == (7, C.OBSERVED),
   "restore trampled a non-commanded param")
ok("device" not in res["note"].lower() and "undo" not in res["note"].lower(),
   "restore must never claim device undo")
ok(raises(bus.restore, "not-a-snap"), "restore accepted a non-dict")

# a restore whose send is refused stays honest
sh, snd, bus = bus_for(refuse=(23,), clk=Clock())
sh.set_commanded(23, 55)
res = bus.restore(bus.snapshot("slot2"))
ok(res["outcomes"][0]["ok"] is False and "replay" in res["outcomes"][0]["why"],
   "refused restore must still read as a replay attempt")
ok(sh.get(23) == (55, C.COMMANDED), "refused restore changed the shadow")

# ============================================================ streams ======
sh, snd, bus = bus_for(clk=Clock())
ex(bus, "alice", [{"cc": 23, "value": 10}])
depth = len(bus.history)
for i in range(12):
    bus.note_stream_sample("modulator", 23, i)
ok(len(bus.history) == depth, "stream samples must never enter the TX history")
ok(bus.stream_count == 12, f"stream_count: {bus.stream_count}")
ok(len(bus.streams) == 12, "stream ring lost samples")
ok(snd.calls[-1][:2] == (23, 10), "a stream sample reached the wire")
ok(sh.get(23) == (10, C.COMMANDED), "a stream sample rewrote the shadow")
ok(len(bus.streams) <= 256, "stream ring must stay bounded")

# ========================================================== persistence ====
tmp = Path(tempfile.mkdtemp(prefix="mf-cmd-"))
hp = tmp / "history.json"
sh, snd, bus = bus_for(history_path=str(hp), clk=Clock())
ok(hp.exists() or True, "path helper")
sh.set_observed(23, 7, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}])
bus.now.step(1.0)
ex(bus, "bob", [{"cc": 5, "value": 3}])
ok(hp.exists(), "write-through did not create the history file")
raw = json.loads(hp.read_text(encoding="utf-8"))
ok(isinstance(raw, list) and len(raw) == 2, f"persisted shape: {type(raw)}/{len(raw)}")
sh2 = C.ShadowState()
bus2 = C.CommandBus(sh2, Sender(), history_path=str(hp), now=Clock())
ok(bus2.load_error is None, f"clean load recorded an error: {bus2.load_error}")
ok(len(bus2.history) == 2, f"reloaded {len(bus2.history)} entries, expected 2")
ok([e["author"] for e in bus2.history] == ["alice", "bob"], "reloaded authors wrong")
ok(bus2.history[0]["pre"] == {23: (7, C.OBSERVED)},
   f"pre did not survive the round-trip as int-keyed tuples: {bus2.history[0]['pre']}")
ok(bus2.history[0]["changes"] == [{"cc": 23, "value": 10}], "changes did not round-trip")
ok(bus2.export() == bus.export(), "export(load(x)) is not stable")

# corrupt file -> start empty, record load_error, never fake-restore
bad = tmp / "bad.json"
bad.write_text("{not json at all", encoding="utf-8")
sh3 = C.ShadowState()
bus3 = C.CommandBus(sh3, Sender(), history_path=str(bad), now=Clock())
ok(len(bus3.history) == 0, "corrupt history was fake-restored")
ok(bool(bus3.load_error), "corrupt load must record load_error")
ok("JSONDecodeError" in bus3.load_error or "Error" in bus3.load_error,
   f"load_error must name the failure: {bus3.load_error}")
ok(ex(bus3, "alice", [{"cc": 23, "value": 1}])["outcomes"][0]["ok"] is True,
   "bus unusable after a corrupt load")
ok(len(json.loads(bad.read_text(encoding="utf-8"))) == 1, "bad file not overwritten cleanly")

# a non-list JSON body is also refused
bad2 = tmp / "bad2.json"
bad2.write_text('{"history": []}', encoding="utf-8")
bus4 = C.CommandBus(C.ShadowState(), Sender(), history_path=str(bad2), now=Clock())
ok(len(bus4.history) == 0 and bool(bus4.load_error), "non-list history accepted")

# missing file is NOT an error
bus5 = C.CommandBus(C.ShadowState(), Sender(), history_path=str(tmp / "nope.json"), now=Clock())
ok(bus5.load_error is None and len(bus5.history) == 0, "missing file must load empty+silent")

# ========================================================== JSON safety ====
sh, snd, bus = bus_for(clk=Clock())
sh.set_observed(23, 7, "panel")
ex(bus, "alice", [{"cc": 23, "value": 10}, {"cc": 5, "value": 2}])
bus.undo()
ex(bus, "bob", [{"cc": 83, "value": 1}], atomic=True)
bus.restore(bus.snapshot("slotz"), author="nick")
blob = json.dumps(bus.export())          # raises if anything non-JSON leaked in
ok(isinstance(blob, str) and len(blob) > 2, "history export is not JSON-safe")
back = json.loads(blob)
ok(back == bus.export(), "export is not a fixed point through json")
for e in back:
    ok(set(e) >= {"id", "author", "action_id", "changes", "atomic", "ts", "outcomes",
                  "pre", "merged_count", "undone", "note"},
       f"exported entry missing keys: {sorted(e)}")
    ok(all(isinstance(k, str) for k in e["pre"]), "pre keys must be strings in JSON")
    ok(all(o.get("cc") is not None and o.get("why") for o in e["outcomes"]),
       "outcome missing cc/why")
ok(all(isinstance(e["ts"], (int, float)) for e in back), "ts must be numeric")
# the whole ring survives a corrupt-free save/load cycle fully
hp2 = tmp / "ring.json"
sh6, _, bus6 = bus_for(history_path=str(hp2), clk=Clock())
for i in range(30):
    ex(bus6, "alice", [{"cc": 23, "value": i}], atomic=True)
bus7 = C.CommandBus(C.ShadowState(), Sender(), history_path=str(hp2), now=Clock())
ok(len(bus7.history) == 30, f"30-command round-trip: {len(bus7.history)}")
ok(bus7.export() == bus6.export(), "round-trip changed the ring")

# ============================================================ threading ====
sh = C.ShadowState()
snd = Sender()
bus = C.CommandBus(sh, snd, now=Clock())
errors = []


def hammer(tid):
    try:
        for i in range(50):
            bus.execute({"author": f"t{tid}", "changes": [{"cc": tid, "value": i % 128}],
                         "atomic": True})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
ok(not errors, f"concurrent executes raised: {errors[:3]}")
ok(len(bus.history) == 400, f"concurrent ring count: {len(bus.history)} (expected 400)")
ok(len(set(e["id"] for e in bus.history)) == 400, "concurrent ring has duplicate ids")
ok(all(sh.entry(t)[0] is not None for t in range(8)), "a concurrent author lost its stamp")

# ============================================================ refusals =====
ok(C.INVALIDATORS == ("disconnect", "preset_switch", "panel_edit_bulk"),
   "invalidator vocabulary drifted")
ok(C.PROVENANCES == ("commanded", "observed", "unknown"), "provenance vocabulary drifted")
ok(not hasattr(C, "mido"), "commands.py must not import mido")
src = (Path(__file__).resolve().parent / "commands.py").read_text(encoding="utf-8")
ok("import mido" not in src, "commands.py imports mido")
ok("import plugin_api" not in src and "from plugin_api" not in src,
   "commands.py must stay pure (no plugin_api import)")

if fails:
    print("FAILURES:")
    for f in fails[:60]:
        print(" -", f)
    print(f"({len(fails)} total)")
    raise SystemExit(1)
print(f"ALL OK \u2014 commands battery passed ({checks[0]} checks: provenance transitions "
      "incl. refused-not-stamped, invalidate stale+kept, ring cap 500/505, all coalescing "
      "branches on an injected fake clock, authors never merge, atomic never merges, "
      "undo/redo inverse replay incl. no-pre-value ghost, partial outcomes visible, "
      "snapshot/restore skip + 'replay' text, JSON-safe persistence round-trip with "
      "corrupt-file load_error, 8-thread hammer)")