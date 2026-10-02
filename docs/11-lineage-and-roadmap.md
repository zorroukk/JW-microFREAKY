# 11 — Lineage, external tools, roadmap

## Where each piece comes from

| Piece | Origin | License |
|---|---|---|
| `apps/fls-pilot` | fork of [thunderdew-dawn/flstudio-mcp](https://github.com/thunderdew-dawn/flstudio-mcp) (itself fork of rosasynthesiz/flstudio-mcp), + 10 local FL-21.2 hardening commits (unpushed) + a measured knowledge base | MIT (upstream LICENSE + NOTICE preserved in the subtree) |
| `apps/mf-mcp` | original (this kit's author) — MCP server + relay + safety contract | MIT |
| `apps/mf-visualizer` | original Hermes plugin | MIT |
| `scripts/flp_notes.py` | original FLP 21.2 binary walker (proven; `pyflp` does NOT work on FL 21.2 numeric event IDs) | MIT |
| `scripts/midi_pairing_map.py` | original OS-level MIDI graph probe | MIT |
| `examples/song-playback/*` | original arrangement scripts + transcribed source data (see usage note below) | MIT (scripts) |

## Protocol/format facts we stand on (attribution of knowledge)

- Arturia MicroFreak User Manual 5.0/5.0.1, **Appendix D** — the 21 live CCs.
  https://www.arturia.com/support/downloads-manuals/product/microfreak
- Firmware V5 transfer rules (direct USB, two-phase update):
  https://support.arturia.com (MicroFreak Firmware V5 article)
- **freakout** (MIT, kmorrill) — `docs/microfreak-sysex.md`: the SysEx frame
  format, live_map param addresses, 4672-byte body structure. Our
  `preset_codec.py` is clean-room Python built from these MIT docs.
- **Elektroid** (GPL-3.0, dagargo) — actively maintained connector with
  preset/sample/wavetable read+write; strong implementation evidence. Its
  GPL code is deliberately NOT copied into this kit — treat Elektroid as a
  separate tool if you need sample/wavetable transfers.
- **ManageFreak** — Electron preset librarian (GPL-3.0); audited clean, used
  as format evidence only (its multi-token name rebuild loses spaces; ours
  reads by character). Keep as a companion tool, don't merge.
- **MicroFreak Reader** (archived) — proved saved-preset reads; cannot edit,
  no unsaved state, no sequences.
- **openmidi** (Apache-2.0) community dictionary —
  `data/brands/arturia/microfreak.yaml` (20 CCs) verified to match our
  control profile on all 20 shared entries.
- Community engine/CC9 captures (Arturia forum thread on the CC/OSC type
  mismatch) — mirrored into the ENGINE_ROWS table; the receive/transmit
  split is ours to enforce.
- FL Studio truths cited in docs/07–08 were verified against the official
  FL 21.2 online manual (menu switches, Piano roll, recording/automation,
  MIDI import, script folders) — fetch pages yourself; we don't mirror it.
- Game/tune source archives (archive.org items, ROM-midi collections) are
  third-party; the transcribed `.mid`/`.json` files under
  `examples/song-playback/` are for **private study of the pipeline** — don't
  republish the bundle of them, and swap in your own sources for songs you
  actually own.

## Built deliberately DIFFERENT from upstream flstudio-mcp

The fork's direction (per its NOTICE): a rollback-first production assistant,
not a broad API wrapper. Local commits add: FL 21.2.2 v44 live battery
results (preview_note/arm/loop-mode confirmed shapes), the piano-roll
notes-bridge (`apply_notes`) with folder resolution through the Windows
`User Shell Folders` registry (OneDrive-redirected Documents fix), the MIDI
input re-broadcast storm law, port-number collision law, and the daemon
supervision/probe discipline (`stack_health_probe.py`).

## Roadmap / open items (things you might expect that DON'T exist yet)

- **Visualizer v0.6 F1 (staged, deployed=NO):** registry v2 (21 params with
  display laws mirrored from the pane), commanded-shadow state
  (`commanded|observed|unknown`), CommandBus with per-send outcomes,
  author-never-merge coalescing, undo/redo inverse replay, named snapshots,
  500-entry persisted history. Batteries green offline (13/13 gate), device
  batch never run because the deploy gate is exactly that batch.
- **v0.5 live loopMIDI mirror** (loops → FL-visible bus) — last item in the
  loop-studio spec, unstarted.
- **Rig control (Strymon Cloudburst pedal):** ruled as a separate
  profile-driven plugin, Class B only in v1, vendor-documented CC allowlist
  only (knobs 12–16, ensemble 11, expression 100, engage 102, freeze 97/98,
  CC0+PC banks; firmware ≥1.21 adds CC17; ping = CC102). **No code was ever
  written** (the pedal must first appear in MIDI enumeration; phase-0 list
  was never cleared). Do NOT resurrect half-believed claims about it.
- **Keystone open test:** whether incoming channel aftertouch feeds the Freak's
  mod matrix (→ stream sources, not targets, to dodge the stair-step law).
- **Unproven legs to run first when you can churn ports:** daemon-killed-
  mid-hold auto-release test; `api_probe` for a native `midiNoteOff` on
  future FL builds; full hardware-automation replay round-trip (docs/07 R4).
- **Predecessor lane (archive interest only):** an earlier July-2026
  "natural-language controller" CLI + honesty-gate harness lived in the
  author's vault; it was superseded by mf-mcp/visualizer and is not shipped
  here.

## How to extend this kit safely

1. Read `docs/09` safety classes first; a new pane/agent control must map to
   A/B/C, and Class D (SysEx writes) stays forbidden-by-default.
2. Every new capability ships with its **verification rung** named
   (offline battery / smoke / device batch / owner-accepted) — and never
   promises a rung that hasn't passed.
3. Trace honesty: a control is live only if you can follow its handler to
   actual device egress (mido send / sendlog append). Endpoints returning
   `ok:true` prove nothing.
4. Add a `source` tag to anything new writing the shared sendlog
   (`macro:<id>`, `clip:<id>`, `rig:<…>` pattern) so the audit lane keeps
   attributing traffic.
