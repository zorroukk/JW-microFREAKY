# 04 — mf-mcp: the MicroFreak MCP server + FL→Freak relay

`apps/mf-mcp` is the original synth-side package: an MCP server exposing
safe, logged control of the MicroFreak, plus the relay that carries FL
Studio's MIDI Out to the hardware. Python ≥3.10, deps `fastmcp`, `mido`,
`python-rtmidi`. Tests: `tests/test_mf_mcp.py` (standalone runner, 9/9).

## Server tools (8)

| Tool | What it does |
|---|---|
| `ping` | liveness |
| `status` | ports, mock/live mode, sendlog tail, pending change units |
| `list_controls` | the full allowed control surface (from `control-profile-v5.json`) |
| `cc` | set one allowed CC (percent 0–100 UI domain → 0–127 wire) |
| `cc_group` | set several CCs as ONE rollback unit |
| `note` | note on/off (auto-release unless held) |
| `program` | preset change: **CC0 bank first, then PC** (`bank=(slot−1)>>7`, 512 slots — a bare PC only reaches bank 0) |
| `rollback` | undo the last change unit, or restore named units |

**The honest-state model.** The Freak cannot report parameter values back
(no MIDI 2.0, no read path for live state). So "before" values come from
**our own send log**: a CC that was never sent in this log has *unknown*
prior state, and rolling it back is impossible by inverse-CC — the tools say
so explicitly and offer `program` (reload the saved preset) instead. Rollback
continuity survives server restarts because the sendlog persists.

**Enforced in code, not just docs:**
- the output port must *name-match* `MicroFreak` — never a first-port fallback
  (the device_required gate);
- only CC / note / program exist. SysEx, pitch bend, aftertouch are not
  implemented at all in the send path;
- every write appends `{ts, source, kind, ...}` to
  `%LOCALAPPDATA%\mf-mcp\sendlog.jsonl` (override with env `MF_MCP_SENDLOG`).

**Control surface = the 21 documented live CCs** (manual Appendix D, FW5):
engine/type, wave, timbre, shape, filter cutoff/reso/env-amount, attack,
decay/release, sustain, cycling-env rise/fall/hold/amount, LFO free+synced
rate, arp/seq free+synced rate, spice, glide, hold, plus filter amount (26).
Full table with value domains: docs/10.

Mock mode for development: `MF_MCP_MOCK=1` (no device needed, same logs).

## The relay (FL → Freak)

```
python -m mf_mcp.relay --loop-port "<exact mido input name>" --mf-port MicroFreak [--dry] [--max-seconds N]
```

- Opens exactly two ports: IN = a **dedicated** loopMIDI port that FL's MIDI
  Out channel targets, OUT = the Freak. Forwards CC/note/program verbatim,
  everything else dropped by contract, all appended to the shared sendlog with
  `source:"relay"` so `status` and the visualizer see FL traffic too.
- Port resolution = exact match else **unique** partial match. Enumerate
  `mido.get_input_names()` FIRST and pass the FULL name including the Windows
  dedupe suffix (`loopMIDI Port 2 4`): a short string like `loopMIDI Port` is a
  prefix of EVERY loopMIDI port and can attach the wrong one.
- **Always `--dry` first** after any routing change: watch the sendlog for
  expected single-shot growth, then relaunch live, ideally with
  `--max-seconds N` so a runaway self-terminates. (`--dry` still writes the
  sendlog — by design that's your evidence; "zero dry entries" means nothing
  reached the port, it does NOT mean dry suppresses logging.)
- Echo-ring warning (see docs/01 law 1): the loopMIDI port feeding the relay
  must not be a bus anything else monitors. If events-per-second explode,
  kill the relay's whole process tree and verify sendlog delta = 0 before
  diagnosing further.

## Long-running python on Windows

Relays started from an agent/terminal wrapper can get reaped silently minutes
after a clean launch. Start helpers detached, then verify the remembered PID:

```python
subprocess.Popen([...python, "-u", "-m", "mf_mcp.relay", ...],
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.BREAKAWAY_FROM_JOB,
    stdout=open("relay.log", "a"))
```

Next: [05](05-visualizer-plugin.md).
