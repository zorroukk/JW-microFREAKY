# 02 — Setup (Windows-first)

Everything below was proven on Windows 11 + FL Studio 21.2.2 + firmware 5.0.0.
macOS: only the fls-pilot lane ships an installer (`scripts/install_macos.sh`);
the MicroFreak lanes were developed and verified on Windows.

## 0. Prerequisites

| Thing | Why | Get it |
|---|---|---|
| **Python 3.12** | 3.13/3.14 have no prebuilt wheels for `python-rtmidi`/`pyaudio` — the build silently falls back to compiling and dies without MSVC | python.org, or `py -3.12` |
| **loopMIDI** | the virtual cables between FL, daemon and relay | https://www.tobsen.com (free) |
| **FL Studio 21+** | the controller lane; 21.2 is the tested build | Image-Line |
| **Arturia MIDI Control Center (MCC)** | firmware, backups, sample/wavetable transfer; NOT needed for MIDI lanes | Arturia site |
| **MicroFreak FW 5.0** | this kit's CC/preset tables are FW5 | MCC (two-phase update: firmware, then factory samples — never unplug between phases) |
| **Hermes desktop** (optional) | only for the visualizer pane | nousresearch.com |

Hardware: connect the Freak **directly by USB** (no hub) and use its dedicated
power supply for any firmware/sample work.

## 1. Per-app virtualenvs

Do NOT share one venv — the fls-pilot daemon and mf-mcp pin different worlds.

```powershell
# fls-pilot (FL controller lane)
cd <this-repo>\apps\fls-pilot
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -e .

# mf-mcp (synth lane)
cd <this-repo>\apps\mf-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -e .        # pulls fastmcp, mido, python-rtmidi
```

Quick MIDI sanity check before anything else (run in the mf-mcp venv):

```python
import mido
print(mido.get_input_names()); print(mido.get_output_names())
# you should see names containing 'MicroFreak' and 'loopMIDI'
```

No MicroFreak in the list → check Device Manager / reconnect / reboot before
continuing; nothing downstream works without it.

## 2. Install the FL controller (fls-pilot lane)

The repo ships `scripts/install_windows.bat` — it (1) copies
`device_FLStudioMCP.py` into `%USERPROFILE%\Documents\Image-Line\FL Studio\
Settings\Hardware\FLStudioMCP\`, (2) `pip install -e .`, (3) seeds the note-
bridge `MCP_Apply.pyscript`, (4) checks loopMIDI. Read the .bat first: if your
FL user-data folder moved (Options → File settings → User data folder), edit the
`FL_SETTINGS` path at the top.

> **Known Windows trap:** if `Documents` is OneDrive-redirected, FL scans the
> *redirected* path. Verify where your `Settings\Piano roll scripts` actually
> lives: registry `HKCU\...\Explorer\User Shell Folders` value `Personal`.
> Files placed in the "ghost" `%USERPROFILE%\Documents\...` will be ignored.
> (The daemon's path resolver learned this; see docs/08.)

## 3. F10 MIDI settings — the wiring

Open FL → Options → MIDI settings (F10). The **rules**:

- Inputs table: enable **only** `FLStudioMCP RX` (device = controller script
  `FLStudioMCP`). Leave Freak/base-loopMIDI **inputs disabled** while producing
  (enabled FL *inputs* re-broadcast to every output — a known storm engine).
- Each enabled RX row must have an output row with the **same number** for its
  replies to land. Example wiring that works: `FLStudioMCP RX` input #42, TX
  output #42, RX output a DIFFERENT number (e.g. 15). Duplicate output numbers
  silently hijack replies.
- Outputs have no on/off toggle — the NUMBER is the binding. Never tell
  yourself "enable the output", just set its number.
- For the hardware lane: create a **dedicated loopMIDI port** (loopMIDI GUI →
  `+`, name e.g. `MF MIDI`), give it a number (e.g. 20), enable it as an
  *input* row only when FL should receive — for our lane the FL **MIDI Out**
  plugin channel's Port spin must equal that same number (20).

After any F10 number change, FL re-initializes bound devices and the
controller script can die quietly — re-verify with the health probe (docs/03).

## 4. Start the stack

```powershell
# 1. daemon (holds the loopMIDI ports, survives FL restarts)
cd <repo>\apps\fls-pilot
.\.venv\Scripts\fl-studio-mcp-daemon.exe
#    health check (same venv, different shell):
python scripts\stack_health_probe.py ; echo exit=$?   # 0 alive

# 2. the relay ONLY when you want FL piano rolls audible on the Freak:
cd <repo>\apps\mf-mcp
.\.venv\Scripts\python.exe -m mf_mcp.relay --loop-port "loopMIDI Port 2" --mf-port MicroFreak --dry
#    (dry = forwards nothing, only logs — always dry-run before going live)

# 3. register the MCP servers in your client (Hermes/Claude/Cursor):
#    fl-studio-mcp   -> stdio, command: <fls-pilot venv>\Scripts\fl-studio-mcp.exe
#    mf-mcp          -> stdio, command: <mf-mcp venv>\Scripts\mf-mcp.exe
```

In Hermes this means an `mcp_servers` block in the profile's `config.yaml`
with those commands; in Claude Desktop it's the `mcpServers` JSON. The fork's
`docs/USER_GUIDE.md` covers client configs; the tools appear as `fl_*` /
`mf_*`.

## 5. Visualizer (optional, needs Hermes)

Copy `apps/mf-visualizer/` into your Hermes profile's `plugins/` directory,
add `mf-visualizer` to `plugins.enabled` in the profile `config.yaml`,
restart Hermes (backend routes mount at boot), then toggle the MicroFreak
pane in Desktop → Settings → Plugins. Details + every feature: docs/05.

## 6. Safety posture (do this once)

- Back up the Freak with MCC *before* any experiment (device/project → export).
- The Freak has **no readback**: if you change something and regret it, the
  recovery is reloading the saved preset (program change) — the tools say so.
- Keep the sendlog: `%LOCALAPPDATA%\mf-mcp\sendlog.jsonl` is the only history
  of what the machine ever sent the device. When debugging goes messy, archive
  it (`mv` to a dated name) so "zero new events" is a meaningful test signal.

Next: [03-fl-studio-mcp.md](03-fl-studio-mcp.md).
