# 10 — MicroFreak MIDI reference (FW5)

Sources: Arturia MicroFreak manual 5.0/5.0.1 (Appendix D = the live-CC
surface), MIT-licensed freakout SysEx docs, community CC dictionaries
(cross-checked: openmidi's 20-CC dictionary agrees with our profile on all 20
shared CCs; ours adds CC26). Engine table = capture mirrored in
`mf-visualizer/desktop/plugin.js::ENGINE_ROWS` (source of record, keep them
equal).

## 1. The 21 live-CC parameters (documented, real-time)

| Parameter | CC | Panel domain |
|---|---|---|
| Engine / oscillator type | 9 | 22 engines — DUAL-DOMAIN table below |
| Wave | 10 | 0–100 % |
| Timbre | 12 | 0–100 % |
| Shape | 13 | 0–100 % |
| Filter Cutoff | 23 | 16 Hz → 26.9 kHz, exponential |
| Filter Resonance | 83 | 0–100 % |
| Filter env amount | 26 | 0–100 % (superset CC) |
| Amp Attack | 105 | 0 ms → 10 s display |
| Amp Decay/Release | 106 | 0 ms → 13 s display |
| Amp Sustain | 29 | 0–100 % |
| Cycling env Rise | 102 | 0 ms → 10 s; curve VERIFIED ≈ 10·(t/127)³ |
| Cycling env Fall | 103 | 0 ms → 10 s; NO clean power law (≈30 ms floor t≤6, +8–11 % mid bulge — use measured anchors) |
| Cycling env Hold/Sustain | 28 | 0–100 % |
| Cycling env Amount | 24 | ±bipolar |
| LFO rate (free) | 93 | 0.06 → 100 Hz log: ≈ 0.06·(100/0.06)^(x^1.02) |
| LFO rate (synced) | 94 | 12 divisions (8/1…1/16t) — division table, not Hz |
| Arp/Seq rate (free) | 91 | 30–240 bpm: ≈ 30+210·x^0.86, quantized 0.5 steps |
| Arp/Seq rate (synced) | 92 | 11 divisions (1/1…1/32T) — division table |
| Spice | 2 | 0–100 % (note: panel "Spice" is a BUTTON too — the knob is CC2) |
| Glide | 5 | 0–10 s |
| Keyboard Hold | 64 | latching on/off |

Arp **Swing** (Shift+turn, 50–75 %) is device-side: **no CC**. Percent↔wire:
panel shows 0–100, wire is 0–127 — `pct=round(cc·100/127)`,
`cc=round(pct·127/100)`.

**Knob CC-OUT (MIDI Control mode) verified transmitting:** 23, 83, 10, 12, 13,
105, 106, 29, 2 (destination Both via MCC). Others may need MCC setup —
device globals are op 42/43 (31 settings, incl. knob behavior + MIDI mode).

## 2. Engine CC9 — the dual-domain table (the one that fooled three builds)

The panel **transmits** one non-uniform value per engine, but **receives**
contiguous ranges that tile 0–127 — and its own transmits sometimes fall
OUTSIDE its receive ranges (SuperWave: transmits 11, receives 12–16). Rules:

- Decoding a value FROM THE PANEL → nearest transmit value.
- Decoding a value we or FL SENT → receive range.
- SENDING engine k → **centre of its receive range**, `round((min+max)/2)` —
  never the band start, never its own transmit value (10 of 22 would be
  out-of-range for FL-style uniform maps).
- Preset SysEx TYPE index (1–22) is a DIFFERENT domain — don't touch CC9
  tables when debugging naming bugs, the decode was the bug, not the list.

| Engine | panel TX | recv range | send here (= `round((min+max)/2)`, JS semantics) |
|---|---|---|---|
| BasicWaves | 5 | 0–11 | 6 |
| SuperWave | 11 | 12–16 | 14 |
| Wavetable | 17 | 16–22 | 19 |
| Harmo | 23 | 23–27 | 25 |
| KarplusStr | 29 | 28–33 | 31 |
| V.Analog | 34 | 34–38 | 36 |
| Waveshaper | 40 | 39–44 | 42 |
| Two Op. FM | 46 | 45–49 | 47 |
| Formant | 52 | 50–55 | 53 |
| Chords | 58 | 56–60 | 58 |
| Speech | 64 | 61–66 | 64 |
| Modal | 69 | 67–71 | 69 |
| Noise | 75 | 72–77 | 75 |
| Bass | 87 | 78–82 | 80 |
| SawX | 93 | 83–88 | 86 |
| Harm | 98 | 89–93 | 91 |
| WaveUser | 104 | 94–99 | 97 |
| Sample | 110 | 100–104 | 102 |
| Scan Grains | 116 | 105–110 | 108 |
| Cloud Grains | 122 | 111–115 | 113 |
| Hit Grains | 127 | 116–121 | 119 |
| **Vocoder** | 81 | **122–127** (sits LAST on receive!) | 125 |

(Uniform `floor(t·22/128)+1` decode maps Speech's own 64 into Modal's band →
the "Modal twice / Speech covered" false report. The engine CC↔index
projection for uniform backends: `t = round((idx−1)·127/21)`, verified 22/22
round-trip.)

## 3. SysEx protocol (Arturia ID: F0 00 20 6B 07 01 … F7)

Frame: `F0 00 20 6B 07 01 SS LL OP payload F7` (mido's `sysex` data excludes
F0/F7 — it re-adds them).

| Op | Function |
|---|---|
| 15 | body dump request/answer (parts) |
| 16 / 17 | body part (145× 32 B chunks + final) |
| 18 | next-part request |
| 19 [bank, program, selector] | request header (selector 0) / body (1) |
| 40 / 41 | LIVE 16-bit param query of the edit buffer (experimental — the unsaved-state door) |
| 42 / 43 | device globals (31 settings, audited allowed values) |
| 52 | header dump (35 B: [10]=category, [12:26]=name, [3]&0x08 = init) |

Preset body = **4672 B = 146×32**, stored 8→7-bit (bitmap byte + 7 data
bytes per block — unpack before parsing). Then self-describing tagged fields:
group = `@#+3` ASCII; field = `(0x40+len)` name chars + `'c'` + meta uint16le.
Groups: VCO / VCF / EG1 (cycling) / EG2 / Arp / LFO / Gen / Kbd + **Co1..Co7**
= 7 mod-matrix columns × 5 cells (rows EG1, EG2, LFO, Xpr, Key = 35 cells),
bipolar ±1; `Mat.Assign1..3` values are op-41 destination addresses (reverse
table in `preset_codec.py DEST_BY_ID`).
512 slots: `bank=(slot−1)>>7`, `prog=(slot−1)&0x7F`; bulk ops 15–19; FW5
boost strings are `<len><EXACT chars>` — names may contain double/trailing
spaces, read by characters, not tokens.
Saved-slot reads are hardware-verified (Elektroid/freakout + our corpus
512/512). **Writes: backup + explicit confirmation + isolated test, always.**

## 4. Panel layout facts (for mirrors/hacks)

- Keybed: 25 touch keys = 15 white + 10 black, C..C two octaves. (Any
  generator that "skips E and A#" is wrong.) An octave = 7 white + 5 black.
- Two control rows under headers: OCTAVE(Glide, Oct±) · DIGITAL OSC(Type,
  Wave, Timbre, Shape) · ARP/SEQ(Shift, Arp|Seq, Oct|Mod, Rate/Swing enc) ·
  ANALOG FILTER(Type, Cutoff, Reso, Mode) · LFO(Shape, Rate) · CYCLING ENV
  (Rise, Fall, Hold, Amount) · ENVELOPE(Amp Mod, Attack, Dec/Rel, Sustain,
  Filter Amt).
- Function band above keys: Hold/Tie · Up|A · Order|B · Random · Pattern ·
  **Spice (button on hardware)** · Dice · Bend + pressure strip (18 pressure
  keys on 2023+ units; label reads PRESS on the panel).
- Top strip: matrix grid, Matrix enc, Paraphonic, Panel, OLED, Preset enc,
  Save, Utility, Master.
- NO MIDI echo exists for: Save/Utility/Arp|Seq/Oct|Mod/Up|A/ORDER|B/
  Random/Dice/Bend/Shift/Paraphonic/Panel/Matrix-encoder, static Preset+
  Master knobs — mirrors of these are cosmetic by law, never live.
- Mod matrix: 5 sources (CycEnv/Env/LFO/Press/Key-Arp) × 7 dests + 3 custom
  columns. Cycling env "Rise/Fall" are TIME knobs, CE hold renamed
  Hold/Sustain on panel.

## 5. Behavior laws (measured on-device)

- **No state readback** — sendlog + panel echoes + saved dumps only.
- **Stair-step quantization** — the internal parameter grid quantizes CC
  streams; smooth sweeps at ~50 msg/s (20 ms) look stepped. Streams that
  need smoothness (vibrato rides) are rate-limited by the device, not the bus.
- **Steep velocity curve** — quiet-but-present layers disappear; mix by
  actual panel response, dry-sim expectations.
- Gate flush/overlap → retrigger/choke; leave gaps between repeated notes.
- One-shot-ish engines (Sample/Percussion/Speech/granular) ignore gate
  length; start time is the only lever.
- Long amp release rings past note-off on sustained engines — tail is the
  envelope, not the MIDI.
- CC123 (all notes off) supported — bracket performances with it.
- Aftertouch/pitch bend: documented on the wire per Arturia FAQ; this kit's
  automated paths deliberately drop both (contract), panel usage unaffected.
