# 09 — Capability matrix: what's possible, proven, and genuinely impossible

Ranked by evidence. **Status vocabulary:**
`LIVE-PROVEN` = verified against real hardware/FL, logged ·
`OFFLINE-OK` = code + batteries green, never round-tripped on this rig ·
`HUMAN-STEP` = works but needs a person at the panel/GUI ·
`IMPOSSIBLE` = hardware/software boundary, not a missing feature.

## MicroFreak control surface

| Capability | Status | Notes |
|---|---|---|
| 21 documented live CCs (manual App. D, FW5) | LIVE-PROVEN | full table + domains in docs/10 |
| Notes / velocity / program (bank+PC) | LIVE-PROVEN | direct + relay + pane + FL lanes |
| Panel knob CC echoes (MIDI Control mode) | LIVE-PROVEN | the pane's `panel` provenance lane |
| Saved-preset bulk READ over SysEx (4672 B, name, category, 35-cell matrix) | LIVE-PROVEN | clean-room decoder; 512/512 factory corpus parses |
| `.mfp` preset file import (Boost + FW5 formats, names-with-spaces) | LIVE-PROVEN | |
| Preset WRITE / sample / wavetable transfer | HUMAN-STEP (MCC) | SysEx writes forbidden by default here: backup + explicit confirm + isolated test only |
| Live unsaved-state READ (ops 40/41 edit-buffer query) | OFFLINE-OK | door exists, addresses mapped only partially; label experimental |
| Mod-matrix programming over MIDI | **IMPOSSIBLE by CC** | not exposed; SysEx body write is Class D (forbidden-by-default) |
| Set *every* front-panel control | **IMPOSSIBLE** | Save/Utility/Arp|Seq/Dice/Random/Bend etc. have NO MIDI echo at all |
| Onboard FX control | **IMPOSSIBLE** | the Freak has no FX section |
| Arp mode/sequence content, LFO shape, filter type, CE mode, paraphonic/unison | HUMAN-STEP | panel recipes only — never present as CC ops |
| Per-note velocity from piano-roll script path | IMPOSSIBLE (this path) | pyscript velocity does not persist (120 always); patch saved .flp instead |
| Aftertouch → mod matrix source | UNVERIFIED | the one keystone test still open |

## Timing/rate laws (measured, this device class)

| Fact | Value |
|---|---|
| Voice budget FW5 | 4 — enforce by pre-assert on the event stream |
| MIDI port message budget | ≈20 ms cadence / ~50 msg/s keeps the stair-step quantizer happy |
| No state readback | sendlog/echo/dump are the only truths |
| Engine CC9 | dual domain: panel transmits non-uniform values, RECEIVES contiguous ranges — send receive-range CENTRES, never band starts |
| Pitch-bend / aftertouch | supported by device per Arturia FAQ; deliberately not in relay/server paths (contract: dropped) |

## FL Studio side (fls-pilot)

| Capability | Status |
|---|---|
| Transport/mixer/channels/patterns/playlist/plugins/effects | LIVE-PROVEN (fork's v44 battery, FL 21.2.2) |
| Rollback-first writes + changelog + named rollback units | LIVE-PROVEN (upstream design) |
| `preview_note` (velocity-0 auto-release), `set_arm`, loop-mode pattern/song | LIVE-PROVEN |
| Piano-roll writes via `apply_notes` + one-time script arming | LIVE-PROVEN (docs/07 R1) |
| Note READBACK | IMPOSSIBLE by design — the fork ships no version-stable return channel; verify by screenshot/ears |
| Pattern-length write | IMPOSSIBLE on 21.2.2 (API unavailable; toolbar by hand) |
| Channel MIDI-port number read/write | IMPOSSIBLE (no API) — owner GUI |
| Hardware knob moves → automation record | LIVE-PROVEN legs, round-trip replay OFFLINE-OK (docs/07 R4) |
| .flp byte patch (start/len at 96 tpb) | LIVE-PROVEN (scripts/flp_notes.py) |
| audio→MIDI transcription (CREPE + chord pipeline) | LIVE-PROVEN (docs/07 R5) |

## Song generation (docs/06)

| Capability | Status |
|---|---|
| Direct-USB full-song performance ≤4 voices | LIVE-PROVEN — 5 accepted songs, scripts shipped |
| Source verification ladder L0→L2 | LIVE-PROVEN |
| Rubato/tempo-curve playback | LIVE-PROVEN (lullaby) |
| Flat-assumption-free pipeline | the method IS the feature; every failure class is documented |

## Safety classes (the mod taxonomy this lane runs on)

| Class | Meaning | Where enforced |
|---|---|---|
| A | read-only observation (sendlog, echoes, dumps) | everywhere |
| B | human-driven writes (you twist, agent watches) | pane PANEL lane |
| C | agent-driven writes (allowlisted CC/note/PC, gated, logged) | mf-mcp, relay, pane LIVE gate |
| D | SysEx writes — forbidden by default (backup+confirm+isolate) | pane reads-only by design; MCC for humans |
| E | no MIDI path at all — cosmetic-only surfaces | panel elements WITHOUT MIDI echo are local mirrors with honest tooltips, never presented as live |

The recurring bug class this table prevents: a control that LOOKS live but
writes nothing (a fake write path survived one audit here before being
excised — audit by tracing the click handler to actual device egress:
mido send / sendlog append; an endpoint returning `ok:true` proves nothing).
