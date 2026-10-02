"""Exhaustive battery for theory.py (F2). Run: python test_theory.py  (from dashboard/)

Lane law 7: pure math gets exhaustive tests, not curated examples. This file
walks every scale x root x note, every quality x root x octave x voicing x
inversion, every degree of every scale, every named progression in every key,
and pins the pane's own SCALES/CHORD_TBL tables (parsed out of desktop/plugin.js)
against this module - drift between pane and backend FAILS here, not on stage.
"""
import itertools
import re
import sys
from pathlib import Path

import theory as T

fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------- tables ----
ok(len(T.SCALES) == 18, f"scale count: {len(T.SCALES)}")
ok(T.SCALE_NAMES[0] == "OFF" and T.SCALE_NAMES[-1] == "WHOLE", "scale order changed")
for name, steps in T.SCALES:
    ok(all(isinstance(s, int) and 0 <= s <= 11 for s in steps), f"{name}: steps out of 0..11")
    ok(list(steps) == sorted(set(steps)), f"{name}: steps not sorted/unique")
    ok(bool(steps) != (name == "OFF"), f"{name}: OFF must be the only empty scale")
ok(len(T.QUALITIES) == len(T.QUALITY_TBL) and set(T.QUALITIES) == set(T.QUALITY_TBL),
   "QUALITIES/QUALITY_TBL mismatch")
for q, ivs in T.QUALITY_TBL.items():
    ok(ivs[0] == 0 and list(ivs) == sorted(set(ivs)) and len(ivs) >= 2, f"quality {q}: {ivs}")
    ok(len(ivs) <= T.PARAPHONY_VOICES + 2, f"quality {q} is unplayable ({len(ivs)} tones)")
ok(len(T.CHORD_TBL) >= 40, f"chord table shrank: {len(T.CHORD_TBL)}")

# ------------------------------------------------- pane <-> backend sync ----
js = (Path(__file__).resolve().parent.parent / "desktop" / "plugin.js").read_text(encoding="utf-8")


def _unesc(s):
    return s.replace("\\u266d", "\u266d").replace("\\u266f", "\u266f").replace("\\u00b7", "\u00b7")


m = re.search(r"const SCALES = \[(.*?)\n\]", js, re.S)
ok(m is not None, "pane SCALES block not found")
if m:
    pane_scales = [(n, tuple(int(x) for x in re.findall(r"-?\d+", s) if x != ""))
                   for n, s in re.findall(r"\['([^']+)',\s*\[([^\]]*)\]\]", m.group(1))]
    # the OFF/empty rows have no bracket pair of numbers -> patch them in explicitly
    pane_scales = [(n, ()) if n == "OFF" else (n, t) for n, t in pane_scales]
    ok(pane_scales == list(T.SCALES),
       f"pane SCALES != theory.SCALES:\n  pane={pane_scales}\n  back={list(T.SCALES)}")

m2 = re.search(r"const CHORD_TBL = \{(.*?)\n\}", js, re.S)
ok(m2 is not None, "pane CHORD_TBL block not found")
if m2:
    pane_tbl = {k: _unesc(v) for k, v in re.findall(r"'([0-9,]+)':\s*'([^']*)'", m2.group(1))}
    ok(pane_tbl == T.CHORD_TBL,
       "pane CHORD_TBL != theory.CHORD_TBL: "
       f"only-pane={set(pane_tbl) - set(T.CHORD_TBL)} only-backend={set(T.CHORD_TBL) - set(pane_tbl)} "
       f"diff={ {k: (pane_tbl.get(k), T.CHORD_TBL.get(k)) for k in set(pane_tbl) & set(T.CHORD_TBL) if pane_tbl[k] != T.CHORD_TBL[k]} }")
ok(_unesc(re.search(r"const INTERVAL2 = \[(.*?)\]", js, re.S).group(1)).count("'") // 2 == len(T.INTERVAL2),
   "pane INTERVAL2 length drifted from theory.INTERVAL2")

# -------------------------------------------------------- in_scale/snap -----
for name, steps in T.SCALES:
    for root in range(12):
        for n in range(128):
            want = (not steps) or (((n - root) % 12) in steps)
            ok(T.in_scale(n, name, root) == want, f"in_scale({n},{name},{root})")
            snapped = T.snap_note(n, name, root)
            ok(T.in_scale(snapped, name, root), f"snap left the scale: {n}->{snapped} {name}/{root}")
            if want:
                ok(snapped == n, f"in-scale note moved: {n} -> {snapped}")
            else:
                d = T.snap_note(n, name, root, "down")
                u = T.snap_note(n, name, root, "up")
                if u <= 127 and d >= 0:
                    ok(min(abs(n - d), abs(n - u)) == min(abs(n - x) for x in T.scale_ladder(name, root, 0, 127)),
                       f"snap not nearest: {n} {name}/{root} d={d} u={u}")
                ok(snapped == d if abs(n - d) <= abs(n - u) else snapped == u,
                   f"nearest tie-break wrong: {n} {name}/{root} -> {snapped} (d={d},u={u})")
            gate_ok, gate_note, why = T.note_gate(n, name, root)
            ok(gate_ok == want, f"note_gate disagrees with in_scale: {n},{name},{root}")
            ok(gate_note == n, "scale lock must send the note the user touched, never a neighbour")
            ok(bool(why) == (not want), f"note_gate reason/flag mismatch: {why!r}")
ok(T.note_gate(66, "MAJOR", 0)[2].startswith("F#4 - out of C MAJOR"), "gate reason text")
ok(T.note_gate(65, "MAJOR", 0)[0], "F4 is IN C major - the gate must not fire")

# ------------------------------------------------------------ chord_label ---
C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B = range(12)
exact = [
    ((C, E, G), "C"), ((C, Ds, G), "Cminor"), ((C, Ds, Fs), "Cdim"),
    ((C, E, Gs), "Caug (symmetric)"), ((C, D, G), "Csus2"), ((C, F, G), "Csus4"),
    ((C, F, As), "Cquartal stack"), ((C, E, A), "C6"), ((C, Ds, A), "Cm6"),
    ((C, E, G, D), "Cadd9"), ((C, E, G, B), "Cmaj7"), ((C, E, G, As), "C7"),
    ((C, Ds, G, As), "Cm7"), ((C, Ds, Fs, As), "Cm7\u266d5"),
    ((C, Ds, G, B), "Cm(maj7)"), ((C, E, G, A), "C6"),
    ((C, E, G, As, D), "C9"), ((C, E, G, B, D), "Cmaj9"),
    ((C, E, G, A, As), "C7add6"), ((C, D, E, F, G, As), "C11"),
    ((C, E, G, As, D, A), "C13"),
]
for pcs, want in exact:
    got = T.chord_label(pcs, C)
    ok(got == want, f"chord_label({pcs}, bass=C) = {got!r}, want {want!r}")
ok(T.chord_label([0, 3, 6, 9]) == "Cdim7 (symmetric)", f"symmetry flag: {T.chord_label([0,3,6,9])}")
ok(T.chord_label([0, 4]) == "C+E maj3", f"dyad: {T.chord_label([0,4])}")
ok(T.chord_label([0, 1, 2]) == "tone cluster C C# D", f"cluster: {T.chord_label([0,1,2])}")
ok(T.chord_label([0, 2, 5]) == "tone set C+D+F", f"tone set: {T.chord_label([0,2,5])}")
ok(T.chord_label([]) is None and T.chord_label([7]) == "G", "degenerate chord labels")
ok(T.chord_label([4, 7, 0], 4) == "C / E\u00b71st inv",
   f"inversion naming: {T.chord_label([4, 7, 0], 4)}")
# every table entry names itself from its own root, in every key
for rel, qname in T.CHORD_TBL.items():
    ivs = [int(x) for x in rel.split(",")]
    for root in range(12):
        pcs = [(root + i) % 12 for i in ivs]
        lab = T.chord_label(pcs, root)
        ok(lab is not None, f"table entry unnameable: {rel} root {root}")
        if lab:
            ok(lab.startswith(T.KEY_NAMES[root]), f"wrong root in label: {rel}/{root} -> {lab}")
            ok(qname == "major" or qname in lab,
               f"quality lost from label: {rel} ({qname}) root {root} -> {lab}")
        lab2 = T.chord_label([p + 12 for p in pcs], root + 60)
        ok(lab2 == lab, f"label is not octave/bass-form invariant: {lab} vs {lab2}")

# ------------------------------------------------------------------ chords --
for q in T.QUALITIES:
    for root in range(12):
        for octv in (2, 3, 4, 5, 6, 7):
            try:
                notes = T.chord_pitches(root, q, octv)
            except ValueError:
                continue                       # would leave 0..127: must refuse (checked below)
            ok(notes == sorted(notes) and len(set(notes)) == len(notes), f"chord shape {q}/{root}/{octv}")
            ok(notes[0] == root % 12 + 12 * (octv + 1), f"chord root placement {q}/{root}/{octv}")
            ok(all(0 <= n <= 127 for n in notes), f"chord out of range {q}/{root}/{octv}")
            ok({n % 12 for n in notes} == {(root + i) % 12 for i in T.QUALITY_TBL[q]},
               f"chord pc set {q}/{root}/{octv}")
# high top edge must refuse, never wrap
ok(raises(T.chord_pitches, 11, "13", 8), "out-of-range chord did not refuse")
ok(raises(T.chord_pitches, 0, "nope"), "unknown quality did not refuse")
ok(raises(T.chord_pitches, 0, "major", 9), "octave 9 did not refuse")
ok(raises(T.chord_label, [0, "x"]), "non-int pc did not refuse")
ok(raises(T.transpose_degrees, [60], "MAJOR", 0, "x"), "non-int degree shift did not refuse")

# ---------------------------------------------------------------- voicings --
checks = 0
for q, style, root, octv in itertools.product(T.QUALITIES, T.VOICING_STYLES, range(12), (3, 4, 5)):
    base_len = len(T.QUALITY_TBL[q])
    if octv >= 5 and base_len > 5:
        continue                                # top edge: covered by the guard test below
    for inv in range(0, min(base_len, 6)):
        try:
            notes = T.voicing(root, q, style, inv, octv)
        except ValueError:
            continue
        checks += 1
        ok(notes == sorted(notes) and len(set(notes)) == len(notes),
           f"voicing shape {q}/{style}/inv{inv}/{root}/{octv}: {notes}")
        ok(all(0 <= n <= 127 for n in notes), f"voicing range {q}/{style}/{root}/{octv}: {notes}")
        ok({n % 12 for n in notes} == {(root + i) % 12 for i in T.QUALITY_TBL[q]},
           f"voicing invented a pitch class {q}/{style}/inv{inv}/{root}/{octv}: {notes}")
        ok(notes[0] == min(notes) and notes[-1] == max(notes), "voicing not ascending")
ok(checks > 1500, f"voicing sweep too small ({checks}) - test degraded")
ok(raises(T.voicing, 0, "major", "jazz"), "unknown style did not refuse")
ok(raises(T.voicing, 0, "major", "close", 9), "inversion 9 did not refuse")
ok(T.voicing(0, "major", "bass")[0] == 48, f"bass-lock: {T.voicing(0,'major','bass')}")
ok(T.voicing(0, "major", "close", 1)[0] == 64, f"1st inversion: {T.voicing(0,'major','close',1)}")
ok(T.voicing(0, "power") == [60, 67], f"power chord: {T.voicing(0,'power')}")
ok(T.voicing(0, "major", "octave") == [60, 64, 67, 72], f"octave double: {T.voicing(0,'major','octave')}")
ok(T.voicing(0, "custom", intervals=(0, 7, 12)) == [60, 67, 72] or raises(T.voicing, 0, "custom"),
   "custom intervals path")

# ------------------------------------------------------------- paraphony ----
ok(T.paraphony_warning([60, 64, 67, 72]) is None, "4 notes warned")
w = T.paraphony_warning([60, 64, 67, 72, 76])
ok(w and "5 notes" in w and str(T.PARAPHONY_VOICES) in w, f"paraphony text: {w}")
for q in T.QUALITIES:
    n = len(T.QUALITY_TBL[q])
    ok(bool(T.paraphony_warning([60 + i for i in range(n)])) == (n > T.PARAPHONY_VOICES),
       f"paraphony gate for {q}")

# -------------------------------------------------- degrees / progressions --
MAJOR_TRIADS = ["major", "minor", "minor", "major", "major", "minor", "dim"]
for root in range(12):
    for d, want in enumerate(MAJOR_TRIADS, start=1):
        ch = T.degree_chord("MAJOR", root, d)
        rel = ",".join(str(v) for v in sorted({(n - ch["notes"][0]) % 12 for n in ch["notes"]}))
        ok(T.CHORD_TBL.get(rel) == want, f"MAJOR degree {d} root {root}: {rel} != {want}")
        ok(ch["notes"][0] % 12 == (root + T.scale_steps("MAJOR")[d - 1]) % 12,
           f"degree chord root wrong: deg{d} root{root}")
    for d, want in enumerate(["Imaj7", "ii7", "iii7", "IVmaj7", "V7", "vi7", "vii7"], start=1):
        ok(T.degree_chord("MAJOR", root, d, seventh=True)["roman"] == want,
           f"MAJOR 7th roman deg{d} root{root}: {T.degree_chord('MAJOR', root, d, seventh=True)['roman']}")
for name, steps in T.SCALES:
    if not steps:
        ok(T.degrees("OFF") == [], "OFF must expose no degrees")
        ok(raises(T.degree_chord, "OFF", 0, 1), "OFF degree chord did not refuse")
        continue
    top = min(7, len(steps))
    for root in range(12):
        for d in range(1, top + 1):
            for sev in (False, True):
                ch = T.degree_chord(name, root, d, sev)
                ok(len(ch["notes"]) == (4 if sev else 3), f"{name} deg {d} size")
                ok(ch["notes"] == sorted(set(ch["notes"])), f"{name} deg {d} shape")
                ok(all(0 <= n <= 127 for n in ch["notes"]), f"{name} deg {d} range")
                ok(all(T.in_scale(n, name, root) for n in ch["notes"]),
                   f"{name} deg {d} left the scale: {ch['notes']}")
                ok(T.pitch_class(ch["notes"][0]) == (root + steps[d - 1]) % 12,
                   f"{name} deg {d} root tone")
                ok(ch["label"], f"{name} deg {d} unnameable: {ch['notes']}")
                ok(all(nm for nm in ch["names"]), f"{name} deg {d} note names")
        ok(raises(T.degree_chord, name, root, top + 1, False), f"{name}: degree {top+1} accepted")
        ok(raises(T.degree_chord, name, root, 0, False), f"{name}: degree 0 accepted")
for pname in T.PROGRESSIONS:
    top = max(T.PROGRESSIONS[pname])
    for name, steps in T.SCALES:
        if not steps:
            continue
        for root in range(12):
            if len(steps) < top:
                # a 5-note scale cannot spell a 12-bar blues: refuse loud, never substitute
                ok(raises(T.progression, name, root, pname),
                   f"{name} accepted {pname} (needs degree {top})")
                continue
            prog = T.progression(name, root, pname)
            ok(len(prog) == len(T.PROGRESSIONS[pname]), f"{pname}: length")
            for ch in prog:
                ok(ch["notes"] and all(0 <= n <= 127 for n in ch["notes"]), f"{pname}/{name}/{root}")
                ok(all(T.in_scale(n, name, root) for n in ch["notes"]), f"{pname}/{name}/{root} off-scale")
ok(raises(T.progression, "MAJOR", 0, "nope"), "unknown progression did not refuse")

# ------------------------------------------------------- degree transpose ---
for name, steps in T.SCALES:
    if not steps:
        continue
    for root in range(12):
        lad = T.scale_ladder(name, root, 0, 127)
        ok(len(lad) > 20, "ladder too small")
        for n in lad[::7]:
            for amt in (-len(steps), -1, 1, len(steps) * 2):
                try:
                    out = T.transpose_degrees([n], name, root, amt)
                except ValueError as exc:
                    ok("escapes 0..127" in str(exc), f"unexpected refusal: {exc}")
                    continue
                ok(T.in_scale(out[0], name, root), f"degree shift left the scale: {n} {amt}")
                idx = lad.index(n) + amt
                ok(out[0] == lad[idx], f"degree shift wrong note: {n} {amt} -> {out[0]} (want {lad[idx]})")
        ok(raises(T.transpose_degrees, [61], "MAJOR", 0, 1) or T.in_scale(61, "MAJOR", 0),
           "out-of-scale input accepted")
        break
ok(T.transpose_degrees([60, 64, 67], "MAJOR", 0, 2) == [64, 67, 71], "I -> III degree move")
for root in range(12):
    a = T.degree_chord("MAJOR", root, 1)["notes"]
    b = T.transpose_preserving_degree(a, "MAJOR", root, (root + 5) % 12)
    ok(all(T.in_scale(n, "MAJOR", (root + 5) % 12) for n in b), f"key move off-scale: {root}")
    ok([n - b[0] for n in b] == [n - a[0] for n in a], "key move changed the shape")
ok(raises(T.transpose_preserving_degree, [60], "OFF", 0, 0), "OFF key move did not refuse")

# ---------------------------------------------------------------- registry --
reg = T.registry()
ok(reg["schema"] == "mftheory/1", "registry schema tag")
ok(len(reg["scales"]) == 18 and len(reg["qualities"]) == len(T.QUALITIES), "registry payload")
ok(reg["voicings"] == list(T.VOICING_STYLES) and reg["paraphony_voices"] == 4, "registry voicings")
ok(len(reg["progressions"]) == len(T.PROGRESSIONS), "registry progressions")

# --------------------------------------------------------------- refusals ---
ok(raises(T.in_scale, 0, "NOPE", 0), "unknown scale accepted")
ok(raises(T.in_scale, 0, "MAJOR", 12), "root 12 accepted")
ok(raises(T.in_scale, 0, "MAJOR", -1), "root -1 accepted")
ok(raises(T.in_scale, 0, "MAJOR", True), "bool root accepted")
ok(raises(T.in_scale, "x", "MAJOR", 0), "string note accepted")
ok(raises(T.scale_steps, "MAJOR "), "trailing space accepted as a scale")
ok(raises(T.snap_note, 60, "MAJOR", 0, "sideways"), "bad direction accepted")
for n in (-1, 128, True, "60", 60.0):
    ok(raises(T.note_name, n), f"note_name accepted {n!r}")

if fails:
    print("FAILURES:")
    for f in fails[:40]:
        print(" -", f)
    print(f"({len(fails)} total)")
    raise SystemExit(1)
print(f"ALL OK - theory battery passed ({checks} voicings, every scale x root x note, "
      "every quality x style x inversion, all degrees, all progressions in all keys, "
      "pane SCALES/CHORD_TBL/INTERVAL2 pinned against the backend)")
