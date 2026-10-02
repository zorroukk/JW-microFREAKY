# 01 — Architecture: how the pieces talk

Two independent MIDI lanes share one audit log. Understanding this split
prevents 90% of the routing bugs this lane has ever produced.

```
                        ┌────────────────────────────────────────────┐
                        │            LLM agent (Hermes / any         │
                        │            MCP-capable client)             │
                        └───────┬─────────────────────────┬──────────┘
                        stdio MCP│                         │stdio MCP
                                ▼                         ▼
                    ┌───────────────────┐       ┌───────────────────┐
                    │  flstudio-mcp     │       │     mf-mcp        │
                    │  (apps/fls-pilot) │       │  server.py        │
                    │  fl_* tools       │       │  cc/note/program/ │
                    │                   │       │  rollback tools   │
                    └─────────┬─────────┘       └───────┬───────────┘
                              │ TCP 127.0.0.1:9787      │ direct MIDI
                              │ (daemon holds ports)    │ (name-gated)
                              ▼                         │
              loopMIDI "FLStudioMCP RX/TX"              │
                              ▲                         │
                     FL Studio device script            │
                   (Settings/Hardware/FLStudioMCP/)     │
                                                        │
   ═══════ HARDWARE LANE (FL → Freak, optional) ═══════  │
                                                        │
   FL "MIDI Out" channel ──► dedicated loopMIDI port ──► │
   (e.g. port 20)              (e.g. "Port 2" #20)  ────►│
                                    ▲                   │
                                    │  mf_mcp.relay ────┤
                                    │  (forwarder,      │
                                    │   dry flag,       ▼
                                    │   audit)   ┌──────────────┐
                                                 │ Arturia       │
   ═══════ DIRECT LANE (no DAW) ═══════════════► │ MicroFreak    │
   song-playback scripts / visualizer  ─────────►│ USB MIDI      │
                                                 └──────────────┘

   Everything that SENDS appends to one shared audit log:
   %LOCALAPPDATA%\mf-mcp\sendlog.jsonl   (source: "relay"|"server"|"visualizer"|...)
```

## The lanes

**A. Controller lane (FL itself).** `flstudio-mcp` installs a *device script*
into FL's Settings/Hardware folder. A small **daemon** holds the loopMIDI
ports and exposes a newline-delimited JSON API on `127.0.0.1:9787`; the MCP
server (and you, via raw TCP) send commands through it. This lane can do
everything FL's scripting API allows: channels, mixer, piano rolls, plugins,
transport, project.

**B. Hardware lane (FL audio-out on the Freak).** A FL **MIDI Out** plugin
channel sends its piano-roll notes to a *dedicated* loopMIDI port; `mf_mcp.relay`
forwards that port verbatim to the MicroFreak and writes every message to the
sendlog. FL's MIDI Out makes **no sound inside FL** — the Freak is the sound.

**C. Direct lane (no DAW at all).** Anything that can open the
`Arturia MicroFreak` MIDI output — the `mf-mcp` server tools, the visualizer
pane, or a plain `mido` script (see `examples/song-playback`) — plays the
synth directly over USB.

**D. Visualizer lane.** The Hermes desktop plugin mirrors lanes A–C through
the sendlog, listens to the Freak's own CC echoes (panel knob twists) and
reads saved presets over SysEx bulk dump. It can also send (macros, clips,
loops) through the same gated backend.

## Non-negotiable laws (learned the expensive way)

1. **ONE Freak path live at a time.** Never have both the direct port open AND
   the relay forwarding to the Freak with the Freak *input* row enabled —
   you build an echo ring that self-excites (~1,700 events/s observed) and
   hammers the hardware with notes nobody played.
2. **The relay needs a DEDICATED loopMIDI port** (its own port created with
   loopMIDI `+`). Reusing a port anything else listens on is how echo rings
   get built.
3. **The MicroFreak cannot report state over MIDI** (no MIDI 2.0). The only
   truth is (a) the sendlog of what we sent, (b) panel CC echoes while the
   Freak is in MIDI-Control mode, (c) SysEx saved-preset dumps. "Unknown" is
   a valid state; never invent it.
4. **Windows MIDI port names drift.** Enumerated names carry dedupe suffixes
   that change after every reboot/restart (`Arturia MicroFreak 3` → `... 5`).
   Always resolve ports by **substring match on the base name**, never by
   index or first-port fallback.
5. **FL port NUMBER is the binding, not the name.** A MIDI Out channel's
   Port spin must equal the number of the loopMIDI device row in FL's
   Options→MIDI settings (F10). The classic silent failure: port exists with
   the right *name*, wrong *number*.
6. **A listening TCP port is not a working bridge.** Health = daemon answers
   AND the FL device script heartbeat is fresh. Use `scripts/stack_health_probe.py`
   (exit 0 alive, 1 daemon up but FL-side dead, 2 unreachable).
7. **SysEx / pitch bend / aftertouch are dropped by contract** in the relay —
   only CC / note / program are forwarded. This is deliberate safety.

Next: [02-setup.md](02-setup.md).
