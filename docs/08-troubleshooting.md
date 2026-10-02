# 08 — Troubleshooting ladder (dead bridge edition)

Ordered fastest-cure-first. Do NOT loop on a failing rung — escalate after
one retry (~10 s probe wait between rungs).

## First: classify the failure

```
apps/fls-pilot/.venv/Scripts/python.exe apps/fls-pilot/scripts/stack_health_probe.py
0 = daemon alive + FL heartbeat fresh      → problem is downstream (routing/number/audio)
1 = daemon up, FL-side silent              → controller script dead / wrong project / F10 churn
2 = unreachable                            → daemon not running (or a STALE listener, below)
```

Read `daemon.log` first (repo `scratch/logs/` when launched from the repo,
else `%LOCALAPPDATA%\flstudio-mcp\logs\`). A listening port is not a bridge;
and a listening 9787 is not necessarily YOUR daemon — cross-check
`netstat -ano` + `tasklist /FI "PID eq <pid>"` (a stale python.exe can hold
the port while the real daemon idles dark; SO_REUSEADDR even allows double-
bind).

## Controller-script death ladder (exit 1 while FL runs)

1. **Restart the DAEMON before touching FL again.** The daemon's MIDI reader
   thread can die while its TCP side keeps answering (command writes raise no
   exception → the self-heal never fires; heartbeat_age climbs across FL
   restarts while FL's script beats into a port nobody reads). A fresh daemon
   catching up on backlog in <1 s proves the script was alive all along.
   Launch it detached (watchdog bat or `start`-style bat; a bare bash
   `start`/`exec` of the exe often never binds).
2. Toggle the bound `FLStudioMCP RX` **input** row power off/on.
3. A crashed script usually IGNORES the power toggle (row stays green, script
   stays dead) — re-bind via the Controller dropdown: switch away from
   `FLStudioMCP`, back again → forces fresh device init; Script output log
   must print `[FLStudioMCP] Ready`.
4. Still silent → restart FL (close, click **No** at save, reopen the wired
   project, touch nothing in F10). FL restart re-numbers ports → re-verify
   numbers.
5. Numbers correct + FL restarted + still no beat → OS driver state (pairing
   map below).

**Timeline check before assuming mid-session death:** if
`heartbeat_age ≈ (now − FL64 StartTime)` with zero fresh beats, the script
NEVER initialized in this instance — that's a binding/number problem, not a
crash; re-probing is futile. Usual trigger: editing port numbers in F10
re-initializes bound devices and kills controller scripts quietly.

## "Nothing happens but FL says ok" (hardware lane)

`ok:true` only proves the FL API executed — not that MIDI physically left.
Wire evidence comes from the receiving end:

```
tail %LOCALAPPDATA%\mf-mcp\sendlog.jsonl     # fresh timestamped source:"relay" entries?
```

Zero `relay` entries while FL plays = FL bypasses the logged chain (wrong
port NUMBER on the MIDI Out channel — check the plugin's Port spin against
the F10 row; owner-GUI-only fix, no API reads/writes channel port numbers),
or the relay died, or its loop port is not the one FL targets.

## Storm / echo-ring forensics

Symptom: hundreds-to-thousands of events per second, notes nobody played.
Causes, in observed order: relay attached to a SHARED loopMIDI bus (echo
ring: relay → Freak → merge/thru → monitor → back); enabled FL input rows
re-broadcasting to all outputs; double Freak path (direct AND relay with
input enabled). Cure: kill the relay's whole process tree, verify sendlog
delta = 0 within seconds, then re-diagnose. After an incident, `mv` the
polluted sendlog to a dated archive so "zero" is meaningful again.

## Pairing map = last resort before a reboot

`scripts/midi_pairing_map.py` (with relay/daemon readers STOPPED first —
stale queued backlog masquerades as live traffic): opens every input, sends
one distinct note per output, prints real out→in pairs + dead outputs.
If pure loopMIDI out→in self-tests fail to loop at OS level while ports
exist — Windows MIDI driver-graph corruption (heavy port churn: renames/
creations/storms/FL restarts). **The only cure is a full Windows reboot**;
F10/loopMIDI/daemon restarts cannot heal it.

## MicroFreak-specific gotchas

- Port names drift with Windows dedupe suffixes every session
  (`Arturia MicroFreak 3` → `... 5`) — substring match `MicroFreak`, never
  index, never first-port.
- Bare program change only reaches bank 0 (512 slots): send CC0 bank THEN PC.
- The panel never announces local preset browsing; a mirrored pane can only
  re-sync by bulk-reading (⟳ Read).
- Non-admin process inspection returns blank command lines — identify routes
  by sendlog traffic, not `Get-CimInstance`.
- FL's OUTPUTS table has no enable toggle; only inputs do. Never hunt for an
  "enable" on an output row.
- After renumbering, demand all three numbers read back in prose (input RX,
  output TX, output RX) and check uniqueness of the paired number among
  outputs — vision passes over the F10 tables reliably mix the columns.
- FL 21.2 has no `channels.midiNoteOff`: previews release via velocity-0
  note-on scheduled from OnIdle (preview notes can't latch even if the
  server dies mid-hold).
- `transport.play` respects whatever PAT/SONG toolbar mode FL is in; in PAT
  mode `get_song_position` stays 0 while playing — not a silence proof.
- A hidden zombie FL from an old session can hold the controller port while
  looking identical: prove attribution BEFORE writing (set_tempo to a
  distinctive value via API, screenshot the VISIBLE window, read its tempo
  box back).
- "Arp starts FL playback and teeters" = FL accepting the Freak's USB MIDI
  clock → uncheck Sync on the MicroFreak port in Options → MIDI preferences.
  The MCP side never sends clock/start/stop.

## New-project "I can't hear my MIDI" triage (in order)

1. `pattern_count: 0` → the notes don't exist (unsaved/other project/deleted
   pattern) — say that before blaming audio. Beware look-alike project names;
   read FL's title bar + check the channel list for your `MF MIDI` channel.
2. Fresh MIDI Out channel's port number almost never matches the F10 device
   row (plugin Port MUST equal the device's port number).
3. Transport in song mode over an empty playlist.
4. Mute/solo states.
5. Remember: MIDI Out makes no sound inside FL — the Freak's own output is
   the monitor (headphones on the Freak work even when your DAW routing is broken).

## Verification-before-promising discipline

When advising hardware behavior, ladder claims: prove the simple rung first
(mouse automation) before the dependent rung (replayed CC rides), run a
positive control before believing any null result, and separate "route not
set" from "device ignores it" with a three-arm test. (Full method:
`scripts/midi_pairing_map.py` + the FL-side probe outputs.)
