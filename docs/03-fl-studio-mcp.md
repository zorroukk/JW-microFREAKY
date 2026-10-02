# 03 — fls-pilot: driving FL Studio from chat

`apps/fls-pilot` is a maintained fork of flstudio-mcp (v2.0.x). The vendored
docs are authoritative — read these first, they're better than anything here:

- `apps/fls-pilot/README.md` — tool overview + per-client setup
- `apps/fls-pilot/docs/USER_GUIDE.md` — prompt patterns + full tool reference
- `apps/fls-pilot/docs/AGENT_ORIENTATION.md` + resources `fl://agent-briefing`
  and `fl://status` (agents should load these before live FL work)
- `apps/fls-pilot/knowledgebase/` — measured API facts and known pitfalls

## What it can do (the fork's feature surface)

**Composition** — chord progressions and melodies in any scale/mode written
straight into the piano roll, pattern creation, arrangement into the playlist.

**Mixing & diagnosis** — Mix Review (clipping/masking/balance with gated,
reversible one-at-a-time fixes), plugin/preset recommendations read from your
*actually installed* plugins, audio clip safe defaults, routing review with
proposed bus layouts, batch channel/track rename+color.

**Project ops** — transport, mixer, channels, effects, browser, export
readiness / preflight health reports. Audio analysis: tempo, key,
audio→MIDI melody conversion (CREPE).

**Safety model** — every persistent mutation goes through a rollback-first
path: scoped snapshot → smallest write → readback where supported → changelog
→ named rollback unit. Tools self-classify into safety classes; live audio
tests stay gated on explicit approval.

## The local commits (what this fork adds beyond upstream)

The 10 unpushed commits + `knowledgebase/` contain the field evidence from
this rig (FL 21.2.2 b3914). The important deltas:

1. **v44 live-battery confirmed** raw ops on FL 21.2.2: `preview_note`
   (release via velocity-0 — FL 21.2 has NO `channels.midiNoteOff`),
   `set_arm` / `isTrackArmed`, `set_loop_mode` (0/1 ↔ pattern/song),
   `arrange_new_pattern`, `get_tempo`, `song_position`. `api_unavailable` on a
   different FL build means the API genuinely isn't there — don't brute-force.
2. **apply_notes raw op** — see docs/07 (the piano-roll writing path).
3. **User-data path resolution** via the `User Shell Folders` registry — fixes
   OneDrive-redirected / localized `Documents` (see docs/02 trap + knowledgebase
   item `windows_localized_documents_breaks_script_scan`).
4. **MIDI rebroadcast storm law** (`knowledgebase/known_pitfalls/fl21_midi_input_
   rebroadcast_storm.md`): enabling an FL *input* row re-broadcasts to ALL
   numbered outputs. Keep only FLStudioMCP RX enabled while producing.
5. **Port-number collision law** (`midi_port_number_routing_collision.md`):
   replies route by OUTPUT-row number == bound INPUT-row number; a second
   output row with the same number hijacks them into silence.

## Health & liveness

```powershell
cd apps\fls-pilot
.\.venv\Scripts\python.exe scripts\stack_health_probe.py
echo $?     # 0 = alive with fresh FL heartbeat
           # 1 = daemon up, FL controller silent (FL closed / script dead / F10 churn)
           # 2 = daemon unreachable
```

A **listening port is not a working bridge** — exit 1 with a growing
`heartbeat_age` while FL64 runs means the FL-side device script stopped
answering. The fix ladder lives in docs/08 (short version: restart the DAEMON
before blaming FL — the daemon's reader thread can die while its TCP side
keeps answering).

## Running without MCP tools visible

If your client registered the server but no `fl_*` tools show up this session,
drive the daemon directly over TCP (newline-delimited JSON, port 9787). Command
names are the `CMD_*` constants in `fl_studio_mcp.protocol`:

```json
{"op":"call","cmd":"get_tempo","params":{},"timeout":15}
{"op":"call","cmd":"channel_preview_note","params":{"channel":3,"note":60,"velocity":100,"hold_ms":300}}
```

Next: [04](04-microfreak-mcp-and-relay.md).
