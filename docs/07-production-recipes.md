# 07 — Production recipes in FL Studio (+ the Freak as sound source)

Everything here was used for real on FL 21.2.2 b3914. The Freak's MIDI Out
lane (docs/01 lane B) must be running for you to *hear* any of it.

## Recipe 1 — Write notes into the piano roll from chat (`apply_notes`)

Raw op over the daemon TCP (`127.0.0.1:9787`) or via the fork's tooling:

```json
{"op":"apply_notes",
 "notes":[{"pitch":52,"time_bars":0,"length_bars":1.3333,"velocity":0.79}],
 "mode":"replace|append","trigger":true,"channel":<ch>,"pattern":<pat>}
```

- Units are **BARS** (converted via live PPQ → fractional bars fine). Convert
  any duration request to beats/bars at project tempo BEFORE writing and
  surface grid conflicts to the user (8 s @ 160 BPM = 5⅓ bars = off-grid —
  offer the grid-clean variant instead of silently picking). Label written
  pitches as MIDI numbers (C4=60; FL's roll may display ±1 octave).
- **`notes/channel/pattern/mode/trigger` are TOP-LEVEL** on this op (unlike
  `op:"call"` which nests `params`). Nesting them = zero notes land AND
  `trigger:true` is the default → FL runs an empty replace script and **wipes
  the roll**. Gate every apply on the response: `count` must equal the number
  of notes sent. A wiped roll is cured by re-applying with a correct count.
- Mechanics: it generates `MCP_Apply.pyscript` (FL's piano-roll scripting
  format — `flpianoroll` API exists already in FL 21.2), writes it to FL's
  *real* scanned folder, force-focuses FL and fires Ctrl+Alt+Y.
- **One-time arming (per FL session, cannot be automated):** FL scans piano-
  roll scripts only at STARTUP, and "Run last script again" is a no-op until
  the script has been run once from Piano roll → Tool (spanner) menu →
  Scripts. First use: create the folder `Settings\Piano roll scripts`
  (FL only makes it on first manual script save), restart FL, run **MCP
  Apply** once from the menu. After that all writes are agent-only for the
  session. Every FL restart re-requires the one menu run.
- Make the target pattern first (`arrange_new_pattern {name}`) — fresh FL 21
  projects legitimately have zero patterns. New patterns default to a 1-bar
  loop and pattern mode CLIPS notes past bar 1; there is **no
  pattern-length API** on 21.2.2 — set it in the toolbar's pattern-length
  boxes by hand.
- **Velocity through this path is a lie:** floats set in the script do NOT
  persist — saved notes land at FL's default 120 (proved by byte-dumping the
  saved file). For real dynamics, patch the saved `.flp` (Recipe 2) or draw
  the velocity lane.
- Verification is by eyes+ears, not readback: the fork deliberately has no
  note readback API. Confirm blocks on the auto-opened piano roll, then
  audition. A MIDI Out channel makes NO audio inside FL — mixer peaks prove
  nothing; your ears on the Freak are the sensor.

## Recipe 2 — Tick-exact note surgery: byte-patching the `.flp`

`scripts/flp_notes.py` decodes/writes FL 21.2 FLP pattern-note records
directly (96 ticks/beat) — no MIDI export round-trip:

```
python scripts/flp_notes.py dump project.flp           # every note: start/len at tick precision
python scripts/flp_notes.py patch in.flp out.flp edits.json   # rewrites ONLY start/len fields
```

- FL writes the `.flp` only on save: ask for Ctrl+S first; never parse a
  stale file as current.
- Patch only approved fields (velocity/pan/detune stay byte-identical);
  always write a NEW file and re-parse to verify before claiming success.
  FL's own `Backup/` folder + the untouched original are the safety net.
- Present a tick-exact table first (start/held/release in beats at header
  tempo, grid gaps in ms) — numbers make intent questions answerable. An
  onset exactly between two grid points cannot be guessed: ask which side.
- Don't use the `pyflp` package (fails on FL 21.2 numeric event IDs). The
  bundled walker is the proven one; format spec notes are in the source
  header. Cosmetic parser bug: `dump` prints `tempo=None` on 21.2.2 saves —
  trust the live `get_tempo` API.

## Recipe 3 — Repair sloppy live-recorded MIDI (classify first!)

Usual cause: USB + monitoring **latency stamped into the events** — the
playing was fine, the MIDI is late.

1. **Off-grid performance** → select all, set Global Snap to the played grid,
   Tools → **Quick quantize start times (Shift+Q)** (keeps lengths), then
   **Articulate (Alt+L) → Staccato/Portato** so notes end just before the
   next — flush/overlapping gates retrigger on the Freak. Alt+Q with Amount
   70–90 % keeps groove while fixing drift. **Snap releases per note** to a
   chosen division; a chord's finger-stagger is usually intended expression.
2. **Constant late start** → if lag > half a snap step, quantize alone snaps
   to the WRONG step — do a fixed shift first (Recipe 2), then quantize.
3. **Sound length ≠ drawn note** → engine class before MIDI: one-shot
   engines (Sample/Percussion/Speech/granular) play the whole sample
   regardless of gate — only start time matters. Long amp release ringing
   past note-off = envelope, not timing.
4. **Progressive drift** → Freak arp/sequencer or clock: check port Sync +
   'send master sync' and the Freak's global sync setting.

Prevention: set Global Snap to a grid BEFORE recording (values other than
none/Line/Cell quantize live input — manual-verified law), play with
interface direct-monitor on, small ASIO buffer.

## Recipe 4 — Hardware knob-twists → FL automation (and back)

- Hardware knob moves ARE recordable: link an FL target to the Freak knob
  (right-click → Link to controller → twist), right-click Record with the
  filter including **Automation**; moves land in the selected pattern (Event
  Editor to trim). Knob CC-out verified on: Wave 10, Timbre 12, Shape 13,
  Spice 2, Cutoff 23, Reso 83, Attack 105, Decay 106, Sustain 29 (verified
  ON, destination Both via MCC).
- MIDI Out's 8×9 controller slots are all automatable → CC rides can be
  replayed back to the Freak in sync with the roll.
- **Status: the individual legs are verified; the full round-trip (record →
  edit → replay → Freak hears it) has NOT been run end-to-end on a live rig.
  Ladder it: prove the mouse-sweep automation rung before promising
  replayed hardware rides.**

## Recipe 5 — Audio → MIDI: transcribe held-chord recordings (no click)

From a mono WAV of hand-held chords + a self-running filter LFO, measure
BPM/timing/lengths FROM the audio:

- **The wobble is the tempo anchor, not `beat_track` alone.** FFT the
  log-RMS envelope AND spectral centroid over the file; if both peak at one
  modulation Hz (e.g. 7.64 Hz), that's the LFO. Convert:
  `mod_hz / wobbles_per_beat × 60 = BPM`; cross-check with beat_track — all
  three readings should cluster ±1 %. Report the measured value (114.65),
  don't round to whatever FL shows unless within noise.
- Chord segmentation: unit-normalized 12-bin pitch-class templates (chroma)
  → cosine sim per frame → median filter → run-segment (min 0.5 bar). Naive
  per-frame energy competition collapses when chords share notes. Confirm
  with a presence matrix (segment-median ÷ 5th-pct floor; spike >1.3 =
  belongs); label shared bass notes as crosstalk. Warn on the closest pair
  (one bass note apart) — ear-check required.
- **Deliver TWO .mid, never silently pick one:** (a) faithful — boundaries
  snapped to nearest 16th, keeps human starts + intro hold; (b) grid-clean
  loop — normalized to bars. Convert every boundary to bars.beats at project
  tempo before showing.
- Library traps we already paid for: librosa 0.11 — `chroma_cqt()` has no
  `n_bins`; no `librosa.signal.smooth` (use `np.convolve`); `beat_track`
  tempo may be an array → `float(np.atleast_1d(t)[0])`; JSON needs
  `default=float`. mido — it's `bpm2tempo`/`tempo2bpm`; build MidiTracks by
  hand (`m.tracks.append(tr)`) because `track_name` + `add_track` both
  touching `.name` crashes. ticks_per_beat 480 → 16th = 120 ticks.

## Recipe 6 — Get a Freak performance INTO FL

Record a loop or CC ride in the visualizer (docs/05), export SMF, drop the
`.mid` onto the playlist (File → Import handles the rest; watch piano-roll
import options for velocity/length mapping). Until v0.5-style export, the
honest alternative was "re-record the part in FL" — the export IS the path.

Next: [08-troubleshooting.md](08-troubleshooting.md).
