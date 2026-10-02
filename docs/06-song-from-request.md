# 06 — "Play me a song": song generation straight onto the Freak

The newest lane (proven live 2026-10-01, five accepted songs): you ask for a
song, the agent **sources the real thing, arranges it to the hardware, and
performs it over direct USB MIDI** — no DAW, no relay, no FL Studio open.

Accepted at the device: Happy Birthday (waltz arrangement), *Blue (Da Ba Dee)*
(chromatic hook), Coldplay *Fix You* first half, *Saria's Song* (Zelda,
verbatim BGM arrangement), *Zelda's Lullaby* v3b — owner verdict: "close to
perfect". The scripts that did it are in `examples/song-playback/` and are
fully runnable: `python play_happy_waltz.py` (mido + a MicroFreak on USB).

This doc is the *method* — what to tell your agent so it reproduces the lane
instead of inventing a bad arrangement.

## The standing pipeline (every new song)

```
1. SOURCE        what the song really is: melody, chords, meter, tempo
2. VERIFY        cross-check ≥2 sources; pitch memory is NOT a source
3. ARRANGE       ≤4-voice arrangement under the laws below
4. TEMPO         read the file's own set_tempo curve — never assume flat BPM
5. DRY SIM       run the whole event math WITHOUT MIDI; assert voice cap
6. PERFORM       open port by substring match, CC123 bracket, send, CC123 again
```

## The four arrangement laws (in priority order)

The MicroFreak FW5 voice budget is **4**; the cap limits *thickness, never
character*:

1. **Melody verbatim, always the top voice.** Never simplify/re-register/
   re-harmonize it to save voices — a wrong melody is instantly audible even
   when harmony is right. Verify against a real score (abc-notation `w:`
   syllable alignment gives exact phrase endings; from-memory arrangements
   demonstrably fail phrase endings while sounding "fine" in notes).
2. **Only diatonic functions the original implies.** Pop/parlor: I, IV, V7
   (+ occasional passing vi). A real **V7** (with the actual seventh) on
   phrase endings is what makes it sound grown-up; bare triads + hammered
   roots = "child plonking". Never INVENT the bass when the real arrangement
   is fetchable — three invented bass guesses died to the ROM's own pedal
   tone before we just read the data.
3. **Keep the meter's idiomatic accompaniment pattern — and verify it by
   LISTENING, not genre assumption.** 3/4 is not automatically oom-pah-pah:
   Happy Birthday wanted a parlor waltz (root + two-note pair), Zelda's
   Lullaby wanted ONE gentle harp sweep per bar (downbeat root + 3-note
   strum ~150 ms spread, all staccato, NO pads — sustained chord voices read
   as "mush"). Dance → arpeggio, rock → eighth chords. The pattern IS the
   personality.
4. **Voice budget = 1 melody + 1 bass + 2 pair-voices**; default texture is
   3 sounding, spend the 4th only where the score does (cadences, final
   chord). Pair-voices sit BELOW the melody register (unison doubling muddies
   digital engines). Velocity ladder: melody 100–120, bass 80–86, accompaniment
   45–70 (~half the lead — confirmed by ear-model listening).

Simplify *simultaneity*, never *selection*: doublings and mid-bar pads go
first; melody, cadence function and the gait are the song's identity.

## Tempo-curve law (the biggest invisible error class)

Before choosing ANY flat BPM, dump the source `.mid`'s `set_tempo` meta
events (accumulate delta ticks to absolute time!). ROM/scene files carry real
rubato maps — the lullaby runs 110 → rit. 94/80/70/60 → reprise 110 → final
55. Build a piecewise `sec(beat)` integrator and apply it to note_on AND
note_off. A flat-tempo render of rubato material sounds "off" forever, no
matter how correct the notes. If meta is absent, derive BPM = total beats ÷
stated seconds — and mind the grid (24 beats per phrase = 8 bars of 3/4, not
6 of 4/4).

## Gate discipline

- Notes the agent INVENTS release at ≤0.92 of their slot (except held finals)
  — flush/overlapping gates retrigger or choke the Freak.
- Durations extracted from a real source MIDI **are the articulation — play
  them raw.** Cosmetic-gating verbatim notes flattened the staccato hook of
  Saria's Song into mush (v2→v3 lesson).
- **Assert max voice depth ≤ 4 on the event stream before touching
  hardware** (walk the merged on/off stream counting depth). When a verbatim
  multitrack exceeds the cap, feed accompaniment through a **greedy
  allocator**: lock the melody, admit candidates in dynamic order, accept
  only where an overlap-walk proves the cap holds.
- Register separation: pick the lead's singing octave by cross-referencing
  sister sequences of the same tune (a playable skeleton and the BGM scene
  often differ by exactly one octave); accompaniment lives 1.5–2 octaves
  BELOW the lead. Layers that are "quiet but present" vanish on the Freak's
  steep velocity curve — dry-sim the mix, not just the notes.

## Source ladder (L0→L3, how confident can you be?)

| Level | Source | Truth | Trap |
|---|---|---|---|
| L0 | letter notes, abc, chord charts | rhythm + exact endings via abc `w:` | letter notes give pitch SEQUENCE only — arranged rhythm from them "feels child-timed" |
| L1 | human-made `.mid` files (`examples/extract_melody.py` picks the vocal/lead track) | true sung onsets AND lengths, free | "made in the style of" files PARAPHRASE: rhythm gold, absolute pitches NOT authoritative when ≥2 prose sources agree against the file. Check stated BPM vs total length — files may run on a 2× pulse grid |
| — | **game/VGM tunes specifically:** ROM midi collections ship SEPARATE sequences — the *playable* file is a melody-only skeleton; the *background-theme* file is the full multitrack arrangement (grab the BGM sequence for backing). List the whole archive (`archive.org/metadata/<id>` → files[]) before concluding the data doesn't exist | | |
| L2 | the agent LISTENS: fetch reference audio (archive.org OST items; YouTube is bot-walled), base64 → any audio-capable LLM (e.g. `qwen3.8-omni-flash` with `input_audio`) | qualitative calls are reliable: plucked-vs-pads, register gap, dynamics ratio, waltz feel | it confabulates NUMBERS (pluck counts, chord names) — triangulate quantitative claims |
| L3 | owner ears | final say, always | singability test: play it, try to sing over it |

Every "feels off" mystery in this lane traced to a **flat assumption**
(tempo, register, density, harmony) — and every one of those is readable
from data or hearable by L2 before the first note is sent.

## The worked-error table (all five builds of one song)

| build | error class | symptom | law born |
|---|---|---|---|
| v1 | invented harmony | "backing completely off" | read the BGM sequence |
| v2 | invented modal center again | "bass still off" | stop reasoning, start fetching |
| v3 | cosmetic-gated verbatim durations | "timing wrong" | source durations are articulation |
| v4 | right notes, wrong voice map | "no lead / backing missing" | mix & register ≠ pitch errors |
| v5 | rolls inside lead's register + flat tempo | "messy" | lead octave via skeleton cross-ref; accompaniment 1.5–2 octaves under |
| v3b | all fixed + actual listening | "close to perfect" | L2 texture law + ROM tempo curve |

## Practical sending notes

- Use the mf-mcp venv python (mido installed): or `pip install mido` anywhere.
- `mido.open_midiout` the port found by **case-folded substring `microfreak`**
  (names drift). One Freak path live at a time — if the relay holds the port,
  `open_output` fails loudly; that's the collision check working.
- Channel 0 (Omni default is fine unless the Freak's global MIDI channel is
  pinned). CC123 (all notes off) before AND after the performance.
- Schedule one merged stream of `(beat, on/off)`, sort by (time, off-before-on
  at ties), sleep-and-send against `time.perf_counter()`.
- Training set for your agent, ascending difficulty: Ode to Joy → Jingle
  Bells → Hallelujah (6/8 = gait transfer) → Let It Be (pad-chord triage) →
  game themes (exact .mid) → Creep (chromatic guide tones) → Bohemian
  Rhapsody excerpt → Toto Africa (modulations).

Next: [07](07-production-recipes.md).
