# MicroFreak Visualizer (mf-visualizer)

A Hermes desktop pane that mirrors the Arturia MicroFreak front panel and lets
you drive it, in three provenance-clean lanes:

- **send log** — everything Hermes/FL ever sent (shared `mf-mcp` JSONL audit log)
- **MIDI IN listener** — physical knob turns while the MF is in
  Utility ▸ MIDI ▸ *MIDI Control* mode (green `PANEL` values)
- **preset import / SysEx capture** — real FW5 preset bodies decoded from
  `.mfp`/`.mbp` files or a device dump, lighting the panel read-only

## Layout

```
plugins/mf-visualizer/
  plugin.yaml                    agent-package identity
  dashboard/
    manifest.json                backend tab manifest (hidden route, api mount)
    plugin_api.py                FastAPI routes under /api/plugins/mf-visualizer/
    preset_codec.py              FW5 preset body decoder (clean-room, MIT facts)
    control-profile-v5.json      21 CC allowlist (from mf-mcp v5 profile)
    test_preset_codec.py         format unit tests (21 vectors)
  desktop/
    plugin.js                    React pane (SVG panel, knobs, matrix, keybed)
desktop-plugins/mf-visualizer/   the RUNNING copy (materialized from desktop/ by
                                 the desktop app; keep both in sync)
```

## Layout (v0.6 additions)

```
dashboard/theory.py            F2 theory backend: 18 scales, 40+ chord names,
                               qualities, voicings, degree chords, progressions
dashboard/chordlab.py          F2.5 Chord Lab engine: one held chord at a time,
                               teeter-law release, gen-voided expiry timers
dashboard/stress.py            F0a diagnostics harness: mock-safe rate/dedupe/
                               stuck-note/panic-latency runs + mfstress/1 export
dashboard/tutor.json           F2b Matrix Tutor knowledge (cited, labelled)
tools/gate.py                  pre-finish verification gate (all batteries +
                               node --check + pane smoke + debt cap)
tools/bump_check.py            device-verify DEBT CAP: refuses a minor version
                               bump while >=3 ledger rows are non-device
tools/pane_smoke.mjs           fake-react pane harness (runs against THIS source)
tools/test_bump_check.py       battery for the debt-cap mechanism itself
```

Version policy (v0.6): the offline batch is **deliberately unstamped and undeployed**.
`plugin.yaml`/`manifest.json` stay 0.5.0 and the pane footer stays `v0.5.0a` because the
ledger currently carries 7 device-pending rows; `tools/bump_check.py` refuses a minor
bump until the owner clears them at the device. The running deploy keeps the verified
0.5.0a code until that happens.

## Endpoints (auth: dashboard token, like every plugin API)

| route | what |
|---|---|
| `GET /state` | merged panel state: cc values + provenance, feed, ports, matrix, preset |
| `POST /listen` | toggle passive MIDI-IN (needs the device's *input* port free) |
| `POST /live`   | arm/disarm real outgoing CC (never on by default) |
| `POST /cc`     | set a knob — allowlisted FW5 CCs only; virtual unless LIVE |
| `POST /note`   | play a key (virtual or live) |
| `POST /program`| program change — doubles as rollback (reload = reset edit buffer) |
| `POST /matrix` | save the 5×7 mod-matrix view (visualizer-side, display only) |
| `POST /preset/import` | base64 `.mfp`/`.mbp` body → decode → light the panel (read-only) |
| `GET /history`, `GET /ports` | feed tail, MIDI port inventory |
| `GET /theory` | v0.6: scales, chord qualities, voicings, progressions (read-only) |
| `POST /theory/chord` | v0.6: build a voicing without playing it (preview) |
| `POST /theory/progression` | v0.6: spell a named progression in a scale/key |
| `GET /chord`, `POST /chord/play`, `POST /chord/release` | v0.6: Chord Lab audition/hold/release |
| `GET /tutor` | v0.6: Mod-Matrix Tutor knowledge (cited; explains, never programs) |
| `GET /diag`, `POST /stress/start`, `POST /stress/stop`, `GET /stress/export` | v0.6: F0a diagnostics harness (mock-safe) |
| `GET /state` | …now `api: 4` (pane refuses a stale backend instead of firing new routes) |

## Verification (offline, all green 2026-09-21)

`python tools/gate.py` runs everything and fails loud:

- `test_modmath.py` · `test_modengine.py` · `test_loopengine.py` · `test_preset_codec.py` · `test_smf.py`
- `test_theory.py` — 27,216 voicings, every scale × root × note, all degrees/progressions
  in all keys, and the pane's `SCALES`/`CHORD_TBL`/`INTERVAL2` pinned to the backend tables
- `test_routes.py` — the real backend over HTTP in mock: fan-out provenance, recorder,
  panic (incl. chord/stress teardown), LIVE-off honesty, v0.6 theory/chord/tutor/diagnostics
  incl. stuck-note smoke and the `mfstress/1` export
- `node --check desktop/plugin.js` + `tools/pane_smoke.mjs` (fake-react pane harness with
  route pair-pins and the v0.6 render/stale-backend pins)
- `tools/test_bump_check.py` + `tools/bump_check.py` — the debt cap itself

## Safety posture

- **No device writes without `LIVE`**, and `LIVE` never persists across restarts.
- CC sends are restricted to the 21-entry FW5 allowlist (control-profile-v5).
- Port selection matches the name `MicroFreak` — never a first-port fallback.
- Preset import and SysEx capture are **read-only**: they move the picture,
  never the synth.
- The mock env (`MF_VIS_MOCK=1`) proves the full request cycle with zero MIDI;
  used by tests, never ships enabled.

## Hardware verification status (2026-09-15)

- MIDI-IN open/close on `Arturia MicroFreak 3`: PASS (3 s idle probe, clean close).
- Mock end-to-end (dedupe, import, sendlog integrity, SysEx reassembly): PASS.
- Preset decode vs 512-preset Arturia factory corpus: 512/512 parse,
  448/448 bodies fully tagged-decoded (rest are empty init slots).
- **Not yet verified (needs the user at the device):** knob echo in MIDI Control
  mode with a real twist; outgoing CC with LIVE armed; SysEx bulk dump from the
  unit (the passive 0x52/0x16/0x17 reassembly path is unit-tested only).

## Enabling

1. `plugins.enabled` must contain `mf-visualizer` (done 2026-09-15).
2. Desktop ▸ Settings ▸ Plugins ▸ enable the **MicroFreak** row (the desktop half
   of a unified package ships opt-in, matching the agent half).
3. After edits to `desktop/` or `plugin.js`, use *Reload desktop plugins* (or
   restart the app).

## Credits / provenance

Format facts from kmorrill/freakout's MIT protocol docs + frank georgy's reader;
behaviour cross-checked against the GPL ManageFreak implementation (vendored at
`Documents/MicroFreak/vendor/managefreak`) with **no code copied** — all decoder
logic here is written from the documented format. GPL-3.0 stays out of the
Hermes plugin tree; only facts crossed the boundary.
