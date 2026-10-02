# 05 — mf-visualizer: the Hermes desktop pane

A Hermes **desktop plugin** (`apps/mf-visualizer/`, MIT, self-contained) that
renders a photo-faithful MicroFreak panel and connects it to the real device.
Backend: FastAPI routes mounted at `/api/plugins/mf-visualizer/`; frontend:
React `plugin.js` (jsx-factory ESM, no JSX syntax).

Current stamped build: **v0.5.x shipped** (loop studio era); **v0.6.1 F1
stage committed but intentionally UNDEPLOYED and unstamped** (registry +
command bus, offline-batteries green 13/13) — running it means deploying
staged code, so the pane's build stamp is the arbiter at your device.

## State provenance — the three honest lanes

The Freak can't report live state, so the pane builds its picture from:

1. **Send log** — everything the agent/FL/pane ever sent (shared JSONL). The
   panel mirror replays CC/program events (never notes — a release-less
   stream can only seed position-like state).
2. **MIDI Control mode echo** — put the Freak in Utility ▸ MIDI ▸ *MIDI
   Control* mode and it transmits a CC whenever you twist a real knob; the
   pane listens and shows `PANEL` provenance (green).
3. **SysEx preset dumps** — the only full-truth snapshot: ⟳ Read bulk-dumps
   the selected saved preset (see below) and lights the entire panel + mod
   matrix read-only.

Every control in the pane is tagged with where its value came from
(`sent` vs `panel` vs `preset` vs `commanded` vs `unknown`).

## Feature list

**Panel mirror** — knobs, keybed (25-key touch surface faithfully modeled:
15 white + 10 black per two-octave window), OLED text, LEDs, octave mini-beds
below the main keybed (tiled windows, never overlapping). Press-to-speak keys
(pointer down/up/leave + blur sweep all release), keyboard-hold latch (CC64
semantics), optimistic lighting with real note-off expiry, stuck-note sweeper.

**⟳ Read (preset bulk dump)** — proper SysEx handshake: `19[b,p,0]` → header
(35 B: name, category, init bit) → `19[b,p,1]` → `15` → 145× `18` → final `17`
= 146×32 B = the 4672-byte FW5 preset body → clean-room decoder
(`preset_codec.py`: 8→7-bit unpack + self-describing tagged fields, groups
VCO/VCF/EG1/EG2/Arp/LFO/Gen/Kbd + Co1..Co7 = the 35-cell mod matrix). Also
imports `.mfp` files directly (names read by CHARACTER count — spaces inside
preset names are real). The panel NEVER announces local preset changes —
⟳ Read is the only re-sync path (honest tooltip says so).

**Mod Lab (drawer under the panel)** —
- **Macros**: named banks of CC values, one-click apply, author-tagged,
  coalescing per-send, undo/redo, hardware-echo reset on panic.
- **Clips** (`mfclip/1`): recorded CC rides — arm, twist knobs (or send),
  stop, play (once/loop), save/load, **export**.
- **Loops** (`mfpat/1`): recorded NOTE sequences with **overdub +
  auto-doubling** (play past the end, the loop grows), length ⏫/⏬/set-bars
  (1–64; shrink truncates, grow adds a silent tail), step grid quantizer,
  BPM-locked playback.
- **SMF export**: any loop + its CC lanes → format-1 `.mid` (TPQ 1920, files
  land in `%LOCALAPPDATA%\mf-visualizer\midis\`, 📁 button reveals the
  folder). This is the proven path FROM hardware performances INTO FL Studio.

**Scale lock (block, not snap)** — 18 scales (OFF + major modes, harmonic/
melodic/hungarian minor, lydian dom, spanish, pentatonics, blues, hiraj,
whole). Out-of-scale keys go dim and do nothing; releases are never gated.

**Chord theory engine** — deep chord labeler (40+ types, all-root search,
inversion names, true-symmetry flag) + tone-cluster/dyad fallbacks, program
history chips.

**Spectrum analyzer** — real WASAPI loopback capture (`pyaudiowpatch` +
numpy) → 64 log bands 24 Hz–20 kHz, fast attack / 0.2 s release, peak-hold,
carrier markers; falls back to a clearly-labelled MIDI model when no capture
device exists (never presents model as measurement). Retarget the device with
`MF_VIS_AUDIO_DEVICE=<substring>`; disable with `MF_VIS_AUDIO_OFF=1`.

**Panic** — one button: bumps a generation counter, voids transports, stops
clips+loops, releases all notes, resets macro hardware echo. Next fader tick
resticks cleanly.

**LIVE gate** — the pane sends NOTHING until you arm LIVE (and refuses clip/
loop playback even in mock unless armed; rehearsal behavior = hardware
behavior). All sends funnel through one choke point: FW5 allowlist → LIVE →
send → sendlog with provenance (`macro:<id>` / `clip:<id>` / `loop:<id>`).

## Install / deploy (Hermes)

1. Copy `apps/mf-visualizer/` → your profile's `plugins/mf-visualizer/`.
2. `plugins.enabled:` must contain `mf-visualizer` in the profile `config.yaml`
   (the agent loader AND the API mount read the same set — backend routes
   mount at process start, so **restart Hermes** after adding a route or
   enabling the plugin).
3. Desktop half is opt-in: toggle the MicroFreak row in Settings → Plugins.
   The app materializes `desktop/plugin.js` into `desktop-plugins/` at boot;
   keep repo source and runtime copy in sync (marker `sourceMtimeMs` decides).
4. Env knobs: `MF_MCP_SENDLOG` (shared log path), `MF_VIS_MOCK=1`,
   `MF_VIS_AUDIO_DEVICE`, `MF_VIS_AUDIO_OFF`.

## Dev/test (all offline, no device)

Batteries are standalone runners (`python dashboard/test_x.py`), NOT pytest
(pytest INTERNALERRORs on their sys.exit). Any interpreter with fastapi
(e.g. Hermes's own venv python) + `MF_VIS_MOCK=1 MF_VIS_AUDIO_OFF=1`:

```
python tools/gate.py          # runs ALL 10 batteries + node --check + smoke
python tools/gate.py --quick  # skip the pane smoke harness
```

Pane ship law: `node --check` the REPO source, deploy, MD5 both copies, run
the smoke harness against the **deployed** copy (a stale runtime copy makes a
green harness meaningless).

Next: [06](06-song-from-request.md).
