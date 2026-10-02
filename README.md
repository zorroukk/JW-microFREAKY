# JW microFREAKY

> **Hermes ⇄ MicroFreak ⇄ FL Studio — full production kit**

Everything needed to wire an **Arturia MicroFreak** hardware synth into an
**LLM agent environment** (Hermes desktop or any MCP-capable client) together
with **FL Studio**, plus a set of hard-won, live-verified MIDI engineering
laws learned from months of real hardware work.

This is a private working kit, not a polished product. Parts of it are
proven live on real hardware, parts are staged and offline-verified only —
every doc says exactly which is which. Don't trust a claim you can find
marked "untested".

## What's in this repo

```
apps/
  fls-pilot/        FL Studio MCP server (fork of flstudio-mcp v2.x) — drives FL itself:
                    mixer, channels, piano roll, plugins, routing, mix review, rollback.
  mf-mcp/           MicroFreak MCP server + loopMIDI→Freak relay — drives the synth:
                    21-parameter CC surface, notes, programs, send-log state model.
  mf-visualizer/    Hermes desktop plugin — live mirror of the Freak front panel:
                    knobs/keybed/preset reads via SysEx, macro bank, clip + loop
                    studio, SMF export, spectrum analyzer, scale lock, panic.
scripts/
  flp_notes.py      Read/byte-patch .flp piano-roll notes (no export round-trip).
  midi_pairing_map.py  Prove which MIDI output actually lands on which input.
examples/
  song-playback/    Proven "play me a song" scripts: melody extraction, voice-
                    capped arrangements (≤4 voices), tempo-curve playback,
                    with the reference data files used to build them.
docs/               How it all fits together, setup, usage, recipes,
                    troubleshooting, capability matrix. START WITH 01.
```

## What you can do with it (the short menu)

| # | Ability | Where documented |
|---|---------|------------------|
| 1 | Chat-driven FL Studio: mixing, routing, organizing, plugin presets, composition into piano rolls, export-readiness checks — all rollback-first | [docs/03](docs/03-fl-studio-mcp.md) |
| 2 | Chat-driven MicroFreak: set any of the 21 documented live-CC parameters, play notes, change presets, with a full audit log and per-change-unit rollback | [docs/04](docs/04-microfreak-mcp-and-relay.md) |
| 3 | FL → Freak integration: write a piano roll from chat, hear it on the hardware through MIDI Out → loopMIDI → relay, audit every message in the shared sendlog | [docs/01](docs/01-architecture.md) + [docs/07](docs/07-production-recipes.md) |
| 4 | **"Play <song>" on the Freak directly over USB — no DAW.** The agent sources the real melody, arranges it within the synth's 4-voice budget, respects the original tempo curve, and performs it | [docs/06](docs/06-song-from-request.md) |
| 5 | Live panel mirror pane in Hermes: knob positions (incl. physical twists), keybed, OLED, preset names + full preset decode over SysEx | [docs/05](docs/05-visualizer-plugin.md) |
| 6 | Mod Lab: saved CC macros, recorded CC "rides" (clips), note **loops with overdub/auto-doubling**, and **export to standard MIDI files** to drag into FL | [docs/05](docs/05-visualizer-plugin.md) |
| 7 | Scale lock + deep chord naming on every note the pane plays (18 scales, 40+ chord types, inversions) | [docs/05](docs/05-visualizer-plugin.md) |
| 8 | Live spectrum analyzer fed by WASAPI loopback audio, 64 log bands | [docs/05](docs/05-visualizer-plugin.md) |
| 9 | Hardware knob-moves captured into FL automation clips (and replayed back to the Freak) | [docs/07](docs/07-production-recipes.md) |
| 10 | Recorded-MIDI timing repair: quantize recipes, tick-exact `.flp` byte patching, audio→MIDI chord transcription | [docs/07](docs/07-production-recipes.md) |
| 11 | A complete MicroFreak MIDI reference: the 21-CC map, the engine CC9 dual-domain table, SysEx preset protocol, value curves, port-numbering laws | [docs/10](docs/10-microfreak-midi-reference.md) |
| 12 | A battle-tested troubleshooting ladder for dead bridges, MIDI storms, Windows driver-graph corruption | [docs/08](docs/08-troubleshooting.md) |

## Honest state of things

Proven live on hardware (owner ears + logs): lanes 1–6, 10–12 and songs
played end-to-end (5 accepted arrangements shipped in `examples/`).
Documented but NOT fully round-trip-verified on a live rig: knob-capture
replay (9). Staged, offline-tested, never deployed: the visualizer's v0.6
registry/command bus. Intentionally impossible (hardware limits, not bugs):
mod-matrix programming, unsaved-patch reads, per-note velocity through the
piano-roll script path — see [docs/09](docs/09-capability-matrix.md) for the
full capability matrix and why each boundary exists.

## Lineage

- `apps/fls-pilot` is a fork of [flstudio-mcp](https://github.com/thunderdew-dawn/flstudio-mcp)
  (MIT, © rosasynthesiz / thunderdew-dawn + contributors) with 10 local commits:
  FL 21.2 hardening, the notes-bridge trigger path, MIDI-port laws, and a
  knowledge base of measured pitfalls.
- `apps/mf-mcp` and `apps/mf-visualizer` are original work (MIT), built for
  Hermes; the visualizer is a Hermes **desktop plugin** and needs Hermes
  running on your machine. The other two are plain Python MCP servers — any
  MCP-capable client works (Claude Desktop, Cursor, ChatGPT, Hermes).
- Protocol facts were cross-checked against Arturia's official manual
  Appendix D, the MIT-licensed [freakout](https://github.com/elektroid/microfreak-patchbook)
  SysEx docs and community CC dictionaries; the full trail is in
  [docs/11](docs/11-lineage-and-roadmap.md).

## Five-minute start

1. Read [docs/01-architecture.md](docs/01-architecture.md) — 5 minutes, saves hours.
2. Install per [docs/02-setup.md](docs/02-setup.md) (Python 3.12 venvs, loopMIDI, controller scripts).
3. Pick a lane:
   - just FL control → [docs/03](docs/03-fl-studio-mcp.md)
   - just the synth from chat → [docs/04](docs/04-microfreak-mcp-and-relay.md)
   - just "play me a song" tonight → [docs/06](docs/06-song-from-request.md)
4. When it dies at 2 a.m.: [docs/08](docs/08-troubleshooting.md).
