"""theory — pure music theory for mf-visualizer F2 (backend theory module).

Stdlib-only, deterministic, no I/O, no MIDI. Every function here is property-
tested (test_theory.py) — lane law 7: new pure math gets exhaustive tests, not
curated examples.

LAWS THIS MODULE CARRIES
- ONE home for theory. The pane keeps its SCALES/CHORD_TBL tables for local
  rendering (dimming, on-the-fly naming), but any NEW consumer (chord lab,
  sequencer, arp, AI lane, visuals) reads THIS module. Re-implementation is
  banned by the owner's preflight law; test_theory.py pins the pane tables
  byte-for-byte against these ones so drift is caught mechanically.
- SCALE LOCK IS A BLOCK, NOT A SNAP (owner redirect 2026-09-19). ``in_scale``
  is the gate; ``snap_note``/``quantize_note`` exist for OTHER consumers and
  must never be smuggled into the note path.
- PARAPHONY: the MicroFreak has 4 voices and is paraphonic — a 5+ note chord
  does not get 5 independent envelopes/voices. ``paraphony_warning`` is the
  single text every chord-shipping surface prints.
- NO READBACK: nothing here knows what the device currently holds. Theory is
  the map, never the state.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------- names ------

KEY_NAMES: Tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")
ROMAN_LOWER = ("i", "ii", "iii", "iv", "v", "vi", "vii")


def note_name(note: int) -> str:
    """MIDI note -> 'C4' style name (C-1 = 0, so 60 = C4)."""
    n = _note(note)
    return f"{KEY_NAMES[n % 12]}{n // 12 - 1}"


def pitch_class(note: int) -> int:
    return _note(note) % 12


def _note(note) -> int:
    if isinstance(note, bool) or not isinstance(note, int):
        raise ValueError(f"note must be an int 0..127, got {note!r}")
    if not 0 <= note <= 127:
        raise ValueError(f"note out of range 0..127: {note!r}")
    return note


# -------------------------------------------------------------- scales ------

# The 18 pane scales (OFF first) — EXACTLY the pane's table, order included.
SCALES: Tuple[Tuple[str, Tuple[int, ...]], ...] = (
    ("OFF", ()),
    # modes of the major scale
    ("MAJOR", (0, 2, 4, 5, 7, 9, 11)), ("MINOR", (0, 2, 3, 5, 7, 8, 10)),
    ("DORIAN", (0, 2, 3, 5, 7, 9, 10)), ("PHRYGIAN", (0, 1, 3, 5, 7, 8, 10)),
    ("LYDIAN", (0, 2, 4, 6, 7, 9, 11)), ("MIXOLYD", (0, 2, 4, 5, 7, 9, 10)),
    ("LOCRIAN", (0, 1, 3, 5, 6, 8, 10)),
    # minor families + dominants
    ("HARM MIN", (0, 2, 3, 5, 7, 8, 11)), ("MEL MIN", (0, 2, 3, 5, 7, 9, 11)),
    ("HUNG MIN", (0, 2, 3, 6, 7, 8, 11)), ("LYD DOM", (0, 2, 4, 6, 7, 9, 10)),
    ("SPANISH", (0, 1, 4, 5, 7, 8, 10)),
    # five-note lanes
    ("MAJ PENT", (0, 2, 4, 7, 9)), ("MIN PENT", (0, 3, 5, 7, 10)),
    ("BLUES", (0, 3, 5, 6, 7, 10)), ("HIRAJ", (0, 2, 3, 7, 8)), ("WHOLE", (0, 2, 4, 6, 8, 10)),
)

SCALE_NAMES: Tuple[str, ...] = tuple(name for name, _ in SCALES)


def scale_steps(name: str) -> Tuple[int, ...]:
    """Pitch-class offsets of a named scale. Unknown name refuses loud."""
    for sname, steps in SCALES:
        if sname == name:
            return steps
    raise ValueError(f"unknown scale: {name!r}")


def in_scale(note: int, scale: str = "OFF", root: int = 0) -> bool:
    """SCALE LOCK gate. OFF (or an empty scale) locks nothing."""
    steps = scale_steps(scale)
    if not steps:
        return True
    return (_note(note) - _root12(root)) % 12 in steps


def scale_ladder(scale: str, root: int = 0, lo: int = 0, hi: int = 127) -> List[int]:
    """Every in-scale note in the window [lo, hi] (inclusive), ascending."""
    steps = scale_steps(scale)
    r = _root12(root)
    if not steps:
        return [n for n in range(max(0, lo), min(127, hi) + 1)]
    out = []
    for n in range(max(0, lo), min(127, hi) + 1):
        if (n - r) % 12 in steps:
            out.append(n)
    return out


def _root12(root) -> int:
    if isinstance(root, bool) or not isinstance(root, int):
        raise ValueError(f"root must be an int 0..11, got {root!r}")
    if not 0 <= root <= 11:
        raise ValueError(f"root out of range 0..11: {root!r}")
    return root


def snap_note(note: int, scale: str = "OFF", root: int = 0,
              direction: str = "nearest") -> int:
    """Nearest (default) / down / up in-scale note. TIES SNAP DOWN.

    This is a NON-note-path helper (quantize-to-scale, degree math). The pane's
    scale lock deliberately blocks instead of snapping — see ``note_gate``."""
    n = _note(note)
    if direction not in ("nearest", "down", "up"):
        raise ValueError(f"direction must be nearest|down|up, got {direction!r}")
    steps = scale_steps(scale)
    if not steps or in_scale(n, scale, root):
        return n
    if direction == "down":
        k = n - 1
        while k >= 0 and not in_scale(k, scale, root):
            k -= 1
        return k if k >= 0 else snap_note(n, scale, root, "up")
    if direction == "up":
        k = n + 1
        while k <= 127 and not in_scale(k, scale, root):
            k += 1
        return k if k <= 127 else snap_note(n, scale, root, "down")
    d = n - 1
    while d >= 0 and not in_scale(d, scale, root):
        d -= 1
    u = n + 1
    while u <= 127 and not in_scale(u, scale, root):
        u += 1
    if d < 0:
        return u
    if u > 127:
        return d
    return d if (n - d) <= (u - n) else u          # tie -> down, by design


def quantize_note(note: int, scale: str = "OFF", root: int = 0) -> int:
    return snap_note(note, scale, root, "nearest")


def note_gate(note: int, scale: str = "OFF", root: int = 0) -> Tuple[bool, int, str]:
    """The SCALE LOCK decision as a pure function: (allowed, note_to_send, why).

    BLOCK, never snap: an out-of-scale press is refused with a named reason and
    the note that WOULD have been sent is the note the user touched."""
    n = _note(note)
    if in_scale(n, scale, root):
        return True, n, ""
    return False, n, f"{note_name(n)} - out of {KEY_NAMES[_root12(root)]} {scale} (scale lock)"


# -------------------------------------------------------------- chords ------

INTERVAL2: Tuple[str, ...] = ("unison", "\u266d2", "maj2", "min3", "maj3", "P4", "tritone",
                              "P5", "min6", "maj6", "min7", "maj7")

# Chord NAMING dictionary (pitch-class sets, sorted, root-relative) — the
# pane's v0.3.0 table, ported verbatim (40+ entries; the old 18-entry table had
# '0,4,7,9' labelled '6' — that is add9).
CHORD_TBL: Dict[str, str] = {
    "0,3,7": "minor", "0,4,7": "major", "0,3,6": "dim", "0,4,8": "aug",
    "0,5,7": "sus4", "0,2,7": "sus2", "0,5,10": "quartal stack",
    "0,4,9": "6", "0,3,9": "m6", "0,4,7,9": "6", "0,3,7,9": "m6",
    "0,2,4,7": "add9", "0,2,3,7": "m(add9)",
    "0,4,5,7": "add11", "0,2,5,7": "sus4add9",
    "0,4,7,11": "maj7", "0,4,7,10": "7", "0,3,7,10": "m7", "0,3,7,11": "m(maj7)",
    "0,3,6,10": "m7\u266d5", "0,3,6,9": "dim7", "0,4,6,10": "7\u266d5", "0,4,8,10": "7\u266f5",
    "0,4,6,11": "maj7\u266d5", "0,4,8,11": "maj7\u266f5", "0,5,7,10": "7sus4", "0,5,7,11": "maj7sus4",
    "0,2,4,9": "6/9", "0,2,3,9": "m6/9", "0,2,4,7,9": "6add9",
    "0,1,4,7,10": "7\u266d9", "0,3,4,7,10": "7\u266f9", "0,1,4,6,10": "7\u266d9\u266d5",
    "0,4,6,7,10": "7\u266f11", "0,4,6,7,11": "maj7\u266f11",
    # 9/11/13 families: the OLD table spelled the 9th as pitch class 9 (= the
    # 6th) and the 11th as class 5 (correct by luck), so C-E-G-A-Bb read as "9"
    # when it is a 7add6. Keys below are the real pc sets; the odd ones keep
    # their own honest names (found by test_theory.py, 2026-09-21).
    "0,2,4,7,10": "9", "0,4,7,9,10": "7add6",
    "0,2,4,7,11": "maj9", "0,4,7,9,11": "maj7add6",
    "0,2,3,7,10": "m9", "0,3,7,9,10": "m7add6",
    "0,2,3,7,11": "m(maj9)", "0,3,7,9,11": "m(maj7)add6",
    "0,2,4,5,7,10": "11", "0,4,5,7,9,10": "11add13",
    "0,2,3,5,7,10": "m11", "0,3,5,7,9,10": "m11add13",
    "0,2,4,7,9,10": "13", "0,2,4,5,7,9,10": "13add11",
    "0,2,3,7,9,10": "m13", "0,2,3,5,7,9,10": "m13add11",
}

# Chord CONSTRUCTION recipes: semitone offsets from the root. Extensions may
# exceed 12 on purpose (9/11/13 keep their voicing height).
QUALITIES: Tuple[str, ...] = (
    "major", "minor", "dim", "aug", "sus2", "sus4", "power",
    "6", "m6", "6/9", "add9", "m(add9)", "quartal",
    "maj7", "7", "m7", "m(maj7)", "dim7", "m7\u266d5", "7\u266d5", "7\u266f5",
    "maj7\u266d5", "maj7\u266f5", "7sus4",
    "9", "maj9", "m9", "7\u266d9", "7\u266f9",
    "11", "m11", "13", "m13",
)

QUALITY_TBL: Dict[str, Tuple[int, ...]] = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "power": (0, 7), "quartal": (0, 5, 10),
    "6": (0, 4, 9), "m6": (0, 3, 9), "6/9": (0, 4, 9, 14),
    "add9": (0, 4, 7, 14), "m(add9)": (0, 3, 7, 14),
    "maj7": (0, 4, 7, 11), "7": (0, 4, 7, 10), "m7": (0, 3, 7, 10),
    "m(maj7)": (0, 3, 7, 11), "dim7": (0, 3, 6, 9), "m7\u266d5": (0, 3, 6, 10),
    "7\u266d5": (0, 4, 6, 10), "7\u266f5": (0, 4, 8, 10),
    "maj7\u266d5": (0, 4, 6, 11), "maj7\u266f5": (0, 4, 8, 11), "7sus4": (0, 5, 7, 10),
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14),
    "7\u266d9": (0, 4, 7, 10, 13), "7\u266f9": (0, 4, 7, 10, 15),
    "11": (0, 4, 7, 10, 14, 17), "m11": (0, 3, 7, 10, 14, 17),
    # 13 chords omit the 11th (standard practice, and the Freak only has 4 voices anyway)
    "13": (0, 4, 7, 10, 14, 21), "m13": (0, 3, 7, 10, 14, 21),
}

VOICING_STYLES: Tuple[str, ...] = ("close", "open", "drop2", "spread", "octave", "bass")

PARAPHONY_VOICES = 4


def chord_label(pcs: Iterable[int], bass: Optional[int] = None) -> Optional[str]:
    """Name a set of notes the way the pane always has (ported verbatim).

    All-root search with bass preference, inversion naming, true-symmetry flags,
    dyad / cluster / tone-set fallbacks."""
    pcs = list(pcs)
    for p in pcs:
        if isinstance(p, bool) or not isinstance(p, int):
            raise ValueError(f"pitch classes must be ints, got {p!r}")
    bass_pc = None if bass is None else pitch_class(bass) if 0 <= bass <= 127 else _root12(bass)
    setpcs = sorted({p % 12 for p in pcs})
    if not setpcs:
        return None
    if len(setpcs) == 1:
        return KEY_NAMES[setpcs[0]]
    if len(setpcs) == 2:
        iv = (setpcs[1] - setpcs[0]) % 12
        return f"{KEY_NAMES[setpcs[0]]}+{KEY_NAMES[setpcs[1]]} {INTERVAL2[iv]}"
    best = None
    for r in setpcs:
        rel = ",".join(str(v) for v in sorted((p - r) % 12 for p in setpcs))
        q = CHORD_TBL.get(rel)
        if not q:
            continue
        score = len(setpcs) * 2 + (3 if r == bass_pc else 0) + (0 if q in ("major", "minor") else 2)
        if best is None or score > best["score"]:
            best = {"r": r, "q": q, "rel": rel, "score": score}
    if best is None:
        run, cur, prev = 1, 1, None
        for p in setpcs:
            cur = cur + 1 if (prev is not None and p - prev == 1) else 1
            run = max(run, cur)
            prev = p
        if run >= 3:
            return "tone cluster " + " ".join(KEY_NAMES[p] for p in setpcs)
        return "tone set " + "+".join(KEY_NAMES[p] for p in setpcs)

    def rel_key(r2: int) -> str:
        return ",".join(str(v) for v in sorted((p - r2) % 12 for p in setpcs))

    rots = sum(1 for r2 in setpcs if rel_key(r2) == best["rel"])   # true symmetry
    inv = ""
    if bass_pc is not None and bass_pc != best["r"]:
        stack = [int(x) for x in best["rel"].split(",")]
        pos = stack.index((bass_pc - best["r"]) % 12)
        posnames = ["root", "1st", "2nd", "3rd", "4th", "5th", "6th"]
        inv = f" / {KEY_NAMES[bass_pc]}\u00b7{posnames[pos] if pos < len(posnames) else 'deep'} inv"
    head = KEY_NAMES[best["r"]] + ("" if best["q"] == "major" else best["q"])
    return f"{head}{inv}{' (symmetric)' if rots > 1 else ''}"


def chord_pitches(root: int, quality: str = "major", octave: int = 4,
                  intervals: Optional[Sequence[int]] = None) -> List[int]:
    """Root-position chord as MIDI notes. ``intervals`` overrides the table."""
    r = _root12(root % 12) + 12 * (_octave(octave) + 1)     # octave 4 -> base 60
    ivs = tuple(intervals) if intervals is not None else _quality(quality)
    if not ivs:
        raise ValueError("empty chord intervals")
    notes = [r + int(i) for i in ivs]
    for n in notes:
        _note(n)
    return sorted(notes)


def _quality(quality: str) -> Tuple[int, ...]:
    if quality in QUALITY_TBL:
        return QUALITY_TBL[quality]
    raise ValueError(f"unknown chord quality: {quality!r}")


def _octave(octave) -> int:
    if isinstance(octave, bool) or not isinstance(octave, int):
        raise ValueError(f"octave must be an int 0..8, got {octave!r}")
    if not 0 <= octave <= 8:
        raise ValueError(f"octave out of range 0..8: {octave!r}")
    return octave


def voicing(root: int, quality: str = "major", style: str = "close", inversion: int = 0,
            octave: int = 4, bass_lock: bool = False, intervals: Optional[Sequence[int]] = None
            ) -> List[int]:
    """A playable voicing: ascending, duplicate-free, inside 0..127.

    Styles: close (root position), open (alternate tones up an octave), drop2
    (second-from-top dropped an octave), spread (root + upper structure at
    +12/+24), octave (root doubled at the octave), bass (bass-lock: root an
    octave down beneath the close voicing). ``inversion`` lifts the lowest N
    tones an octave (close-family styles only)."""
    if isinstance(inversion, bool) or not isinstance(inversion, int) or not 0 <= inversion <= 6:
        raise ValueError(f"inversion must be an int 0..6, got {inversion!r}")
    if style not in VOICING_STYLES:
        raise ValueError(f"unknown voicing style: {style!r} (have {VOICING_STYLES})")
    notes = chord_pitches(root, quality, octave, intervals)
    if inversion:
        if inversion >= len(notes):
            raise ValueError(f"inversion {inversion} exceeds {len(notes)} chord tones")
        if style in ("close", "bass"):
            for i in range(inversion):
                notes[i] += 12
        elif style == "drop2":
            for i in range(inversion):
                notes[i] += 12
        else:                                   # open/spread/octave: rotate the set
            notes = notes[inversion:] + [n + 12 for n in notes[:inversion]]
    if style == "open":
        notes = [n + (12 if i % 2 else 0) for i, n in enumerate(sorted(notes))]
    elif style == "drop2":
        s = sorted(notes)
        if len(s) >= 2:
            s[-2] -= 12
        notes = s
    elif style == "spread":
        s = sorted(notes)
        notes = [s[0]] + [n + 12 for n in s[1:]]
    elif style == "octave":
        s = sorted(notes)
        notes = s + [s[0] + 12]
    elif style == "bass":
        s = sorted(notes)
        notes = [s[0] - 12] + s
    out = sorted(set(notes))
    for n in out:
        _note(n)
    return out


def paraphony_warning(notes: Sequence[int]) -> Optional[str]:
    """The one text every chord-shipping surface prints. None when legal."""
    n = len({int(x) % 12 for x in notes if isinstance(x, int)})
    if len(notes) <= PARAPHONY_VOICES:
        return None
    return (f"{len(notes)} notes = more than the Freak's {PARAPHONY_VOICES} paraphonic voices: "
            "the lowest 4 own the envelopes, the rest ride along (all note-offs still tracked)")


# ------------------------------------------------- degrees / progressions ----

def degree_pitches(scale: str, root: int = 0, degree: int = 1, seventh: bool = False,
                   octave: int = 4) -> List[int]:
    """Stack a triad (or seventh) ON a scale degree, inside the scale itself."""
    steps = scale_steps(scale)
    if not steps:
        raise ValueError("degree chords need a real scale (OFF has no spelling)")
    if isinstance(degree, bool) or not isinstance(degree, int) or not 1 <= degree <= min(7, len(steps)):
        raise ValueError(f"degree must be 1..{min(7, len(steps))} for {scale}, got {degree!r}")
    r = _root12(root) + 12 * (_octave(octave) + 1)
    idxs = [degree - 1 + 2 * k for k in range(4 if seventh else 3)]
    notes = []
    for i in idxs:
        stepsin = len(steps)
        notes.append(r + 12 * (i // stepsin) + steps[i % stepsin])
    for n in notes:
        _note(n)
    return sorted(notes)


def degree_chord(scale: str, root: int = 0, degree: int = 1, seventh: bool = False,
                 octave: int = 4) -> dict:
    """Degree chord + its own name/quality, named from the STACK (no guessing)."""
    notes = degree_pitches(scale, root, degree, seventh, octave)
    label = chord_label(notes, notes[0])
    return {"degree": degree, "roman": _roman(scale, root, degree, seventh),
            "notes": notes, "names": [note_name(n) for n in notes],
            "label": label, "paraphony": paraphony_warning(notes)}


def _roman(scale: str, root: int, degree: int, seventh: bool) -> str:
    notes = degree_pitches(scale, root, degree, seventh)
    ivs = sorted({(n - notes[0]) % 12 for n in notes})
    minorish = 3 in ivs and 4 not in ivs
    dimish = 3 in ivs and 6 in ivs
    r = ROMAN_LOWER if (minorish or dimish) else ROMAN
    base = r[degree - 1]
    if seventh:
        base += "7" if 10 in ivs else "maj7"
    return base


def degrees(scale: str, root: int = 0, seventh: bool = False, octave: int = 4) -> List[dict]:
    steps = scale_steps(scale)
    if not steps:
        return []
    return [degree_chord(scale, root, d, seventh, octave)
            for d in range(1, min(7, len(steps)) + 1)]


# Named progressions: degree numbers (1-based), '7' marker = dominant seventh.
PROGRESSIONS: Dict[str, Tuple[int, ...]] = {
    "I-V-vi-IV (pop)": (1, 5, 6, 4),
    "ii-V-I (jazz)": (2, 5, 1),
    "I-IV-V (rock)": (1, 4, 5),
    "vi-IV-I-V": (6, 4, 1, 5),
    "i-VI-III-VII (andalusian)": (1, 6, 3, 7),
    "12-bar blues": (1, 1, 1, 1, 4, 4, 1, 1, 5, 4, 1, 5),
    "i-iv-v (minor)": (1, 4, 5),
    "I-vi-IV-V (doo-wop)": (1, 6, 4, 5),
}


def progression(scale: str, root: int = 0, name: str = "ii-V-I (jazz)",
                seventh: bool = True, octave: int = 4) -> List[dict]:
    if name not in PROGRESSIONS:
        raise ValueError(f"unknown progression: {name!r} (have {tuple(PROGRESSIONS)})")
    return [degree_chord(scale, root, d, seventh, octave) for d in PROGRESSIONS[name]]


# --------------------------------------------------- degree transposition ----

def transpose_degrees(notes: Sequence[int], scale: str, root: int = 0, amount: int = 0) -> List[int]:
    """Shift notes by ``amount`` SCALE DEGREES (octave-preserving), not semitones.

    Every input note must be in the scale and the result must stay inside
    0..127 - otherwise this refuses loud rather than silently wrapping into
    another octave."""
    steps = scale_steps(scale)
    if not steps:
        raise ValueError("degree transposition needs a real scale (OFF has no degrees)")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError(f"amount must be an int, got {amount!r}")
    ladder = scale_ladder(scale, root, 0, 127)
    pos = {n: i for i, n in enumerate(ladder)}
    out = []
    for n in notes:
        nn = _note(n)
        if nn not in pos:
            raise ValueError(f"{note_name(nn)} is not in {KEY_NAMES[_root12(root)]} {scale}")
        j = pos[nn] + amount
        if j < 0 or j >= len(ladder):
            raise ValueError(f"degree shift {amount:+d} escapes 0..127 from {note_name(nn)}")
        out.append(ladder[j])
    return out


def transpose_preserving_degree(notes: Sequence[int], scale: str, root: int = 0,
                                new_root: int = 0) -> List[int]:
    """Move a chord/line to another KEY inside the same scale, keeping degrees."""
    steps = scale_steps(scale)
    if not steps:
        raise ValueError("degree-preserving key move needs a real scale (OFF has no degrees)")
    old = [_note(n) for n in notes]
    if not old:
        raise ValueError("nothing to move")
    deltas = [n - old[0] for n in old]
    base = scale_ladder(scale, new_root, 0, 127)
    targets = [d for d in base if d % 12 == _root12(new_root)]
    if not targets:
        raise ValueError("no anchor note for that root")
    anchor = min(targets, key=lambda n: abs(n - old[0]))
    out = [anchor + d for d in deltas]
    for n in out:
        _note(n)
        if not in_scale(n, scale, new_root):
            raise ValueError(f"{note_name(n)} escapes {KEY_NAMES[_root12(new_root)]} {scale}")
    return sorted(out)


def registry() -> dict:
    """The pane's read-only theory payload (scales, qualities, voicings, progressions)."""
    return {"schema": "mftheory/1",
            "scales": [{"name": n, "steps": list(s)} for n, s in SCALES],
            "qualities": [{"name": q, "intervals": list(QUALITY_TBL[q])} for q in QUALITIES],
            "voicings": list(VOICING_STYLES),
            "progressions": [{"name": n, "degrees": list(d)} for n, d in PROGRESSIONS.items()],
            "paraphony_voices": PARAPHONY_VOICES}