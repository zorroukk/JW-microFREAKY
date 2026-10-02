import { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

let api = null

// ---------------------------------------------------------------------------
// Static panel knowledge. LAYOUT is transcribed from the owner's reference
// photo (2026-09-15 vision map), minus the v0.2.7a excisions: the matrix
// board (cells faked a write path) and the ARP / SEQ header are gone,
// Paraphonic, Panel, OLED, Preset enc, Save, Utility, Master across the top;
// two control rows under headers OCTAVE | DIGITAL OSCILLATOR | ARP/SEQ |
// ANALOG FILTER | LFO | CYCLING ENVELOPE | ENVELOPE; function-button band +
// pressure bar directly above the 25 keys.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// ENGINE TRUTH (v0.4.1, closes the bug that "appeared multiple times").
// Ground data: owner's device order + the community capture archived in the
// source capture `Raw Sources/MicroFreak/Community Forums/arturia-forum-midi-cc-osc-type-mismatch-2026-05-21.html`
// (Jan 2025, acknowledged by Arturia staff): the panel TRANSMITS one NON-UNIFORM
// value per engine (…Chords 58, Speech 64, Modal 69…Vocoder 81!) while RECEIVING
// contiguous ranges — and Vocoder sits LAST on the panel yet transmits 81.
// The old uniform `floor(t*22/128)` decode sent Speech's own transmit (64.0
// exactly) into the next band => the pane showed MODAL for Speech (owner report
// 2026-09-19: "Modal appears twice, Speech covered"). Rules now:
//   DISPLAY  = nearest TRANSMIT value (what the device actually sends us).
//   SENDING  = centre of the engine's RECEIVE range (its own transmit is out
//              of range for SuperWave/Bass../Vocoder — the thread's exact gripe).
// [name, transmit, recvMin, recvMax] in PANEL order (Vocoder last).
const ENGINE_ROWS = [
  ['BasicWaves', 5, 0, 11], ['SuperWave', 11, 12, 16], ['Wavetable', 17, 16, 22],
  ['Harmo', 23, 23, 27], ['KarplusStr', 29, 28, 33], ['V.Analog', 34, 34, 38],
  ['Waveshaper', 40, 39, 44], ['Two Op. FM', 46, 45, 49], ['Formant', 52, 50, 55],
  ['Chords', 58, 56, 60], ['Speech', 64, 61, 66], ['Modal', 69, 67, 71],
  ['Noise', 75, 72, 77], ['Bass', 87, 78, 82], ['SawX', 93, 83, 88],
  ['Harm', 98, 89, 93], ['WaveUser', 104, 94, 99], ['Sample', 110, 100, 104],
  ['Scan Grains', 116, 105, 110], ['Cloud Grains', 122, 111, 115],
  ['Hit Grains', 127, 116, 121], ['Vocoder', 81, 122, 127],
]
// DECODE SPLIT BY SOURCE (the two wire worlds genuinely differ):
//   source=panel  -> the device's own NON-UNIFORM transmit values (nearest wins;
//                    ties keep the earlier panel row: 78 = Noise over Vocoder).
//   pane/FL/other -> arbitrary CC9 we or FL put on the wire -> the RECEIVE ranges
//                    the device actually recognises (they tile 0..127).
const engineTransmitIdx = (t) => {
  const v = clampT(t)
  let best = 0, bd = Infinity
  for (let i = 0; i < ENGINE_ROWS.length; i++) {
    const d = Math.abs(v - ENGINE_ROWS[i][1])
    if (d < bd) { bd = d; best = i }
  }
  return best + 1
}
const engineRecvIdx = (t) => {
  const v = clampT(t)
  for (let i = 0; i < ENGINE_ROWS.length; i++) if (v >= ENGINE_ROWS[i][2] && v <= ENGINE_ROWS[i][3]) return i + 1
  return engineTransmitIdx(v)   // unreachable in theory (ranges tile 0..127); belt
}
const engineIdxFromCc = (t, src) => (src === 'panel' ? engineTransmitIdx(t) : engineRecvIdx(t))
const engineCcFor = (idx) => {
  const r = ENGINE_ROWS[Math.max(0, Math.min(21, (idx | 0) - 1))]
  return Math.round((r[2] + r[3]) / 2)      // centre of the RECEIVE range the device actually recognises
}
const ENGINES_22 = [null, ...ENGINE_ROWS.map((r) => r[0])]
const engineFromCc = (t, src) => ENGINES_22[engineIdxFromCc(t, src)] || `Engine`

// MF panel shows 0..100 (%); MIDI wire is 0..127 - convert at the boundary.
const toPct = (cc) => Math.round((cc * 100) / 127)
const toCc = (pct) => Math.max(0, Math.min(127, Math.round((pct * 127) / 100)))

// Panels shows real units, not %, on most timing knobs. Curves below are FITTED
// to the owner's 2026-09-16 side-by-side readouts (device value <-> CC):
//   Rise/Fall 10*(t/127)^3  (5 points, all within ~1%)
//   Hold     10*(t/127)^2 ; Attack 10*(t/127)^3.3 ; Dec/Rel 13*(t/127)^(5/3)
//   Arp bpm   30+210*(t/127)^0.86, quarter-step quantized (device shows .5 steps:
//             193.5 landed exactly on its quantization grid)
//   LFO Hz    0.06*(100/0.06)^(t/128) ; Glide unprobed -> still approx.
const ARP_DIVS = ['1/1', '1/2', '1/2T', '1/4', '1/4T', '1/8', '1/8T', '1/16', '1/16T', '1/32', '1/32T']
const LFO_DIVS = ['8/1', '4/1', '2/1', '1/1', '1/2', '1/2T', '1/4', '1/4T', '1/8', '1/8T', '1/16', '1/16T']
const clampT = (t) => Math.max(0, Math.min(127, t))
const fmtTime = (d) => d < 1 ? `${Math.round(d * 1000)}ms` : `${d.toFixed(1)}s`
const powT = (t, maxS, p) => fmtTime(maxS * Math.pow(clampT(t) / 127, p))
const FMT = {
  5: (t) => `≈${powT(t, 10, 3)}`,
  23: (t) => { const hz = 16 * Math.pow(26900 / 16, clampT(t) / 127)
    return hz < 999.5 ? `${Math.round(hz)}Hz` : `${(hz / 1000).toFixed(1)}kHz` },
  91: (t) => `${Math.round((30 + 210 * Math.pow(clampT(t) / 127, 0.86)) * 2) / 2}bpm`,
  92: (t) => ARP_DIVS[Math.min(ARP_DIVS.length - 1, Math.floor(t * ARP_DIVS.length / 128))],
  93: (t) => { const hz = 0.06 * Math.pow(100 / 0.06, Math.pow(clampT(t) / 127, 1.02)); return hz < 10 ? `${hz.toFixed(2)}Hz` : `${hz.toFixed(1)}Hz` },
  94: (t) => LFO_DIVS[Math.min(LFO_DIVS.length - 1, Math.floor(t * LFO_DIVS.length / 128))],
  102: (t) => powT(t, 10, 3),
  103: (t) => powT(t, 10, 3),
  28: (t) => powT(t, 10, 2),
  105: (t) => powT(t, 10, 3.3),
  106: (t) => powT(t, 13, 5 / 3),
}
const fmtVal = (cc, t, src) => (cc != null && FMT[cc]) ? FMT[cc](t) : (cc === 9 ? engineFromCc(t, src) : `${toPct(t)}%`)
// Pane build stamp (v0.2.5 lesson): the app's ESM module cache can keep serving an old
// plugin.js even after "reload" - the footer shows this stamp so a doubled-Modal /
// missing-Speech report is instantly separable from a stale-renderer problem.
const PANE_BUILD = 'v0.5.0a'
// v0.6 offline batch (F2 theory backend / F2.5 Chord Lab / F0a diagnostics) lives
// in this source but is deliberately NOT deployed and NOT stamped: the device-verify
// debt cap (owner visualizer-versions.md ledger, tools/bump_check.py) refuses a minor
// version bump while 3+ versions still sit unverified at the device. PANE_BUILD stays
// the at-device arbiter until the F0 batch clears those rows.
const PANE_STAGE = 'F2+F2.5+F0a offline (undeployed, unstamped)'

// SCALE-TABLES BEGIN (pure, unit-tested via Temp/mfvis_scale_test.py)
const KEY_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
const SCALES = [
  ['OFF', []],
  // modes of the major scale
  ['MAJOR', [0, 2, 4, 5, 7, 9, 11]], ['MINOR', [0, 2, 3, 5, 7, 8, 10]],
  ['DORIAN', [0, 2, 3, 5, 7, 9, 10]], ['PHRYGIAN', [0, 1, 3, 5, 7, 8, 10]],
  ['LYDIAN', [0, 2, 4, 6, 7, 9, 11]], ['MIXOLYD', [0, 2, 4, 5, 7, 9, 10]],
  ['LOCRIAN', [0, 1, 3, 5, 6, 8, 10]],
  // minor families + dominants
  ['HARM MIN', [0, 2, 3, 5, 7, 8, 11]], ['MEL MIN', [0, 2, 3, 5, 7, 9, 11]],
  ['HUNG MIN', [0, 2, 3, 6, 7, 8, 11]], ['LYD DOM', [0, 2, 4, 6, 7, 9, 10]],
  ['SPANISH', [0, 1, 4, 5, 7, 8, 10]],
  // five-note lanes
  ['MAJ PENT', [0, 2, 4, 7, 9]], ['MIN PENT', [0, 3, 5, 7, 10]],
  ['BLUES', [0, 3, 5, 6, 7, 10]], ['HIRAJ', [0, 2, 3, 7, 8]], ['WHOLE', [0, 2, 4, 6, 8, 10]]]
// v0.4.3 OWNER REDIRECT (2026-09-19): out-of-scale keys are BLOCKED, not
// snapped - "it'll light up a completely different one" reads as broken. The
// beds dim the locked-out keys so the law is visible before you touch.
// The old nearest-tone snap helper is GONE from the note path entirely.
const inScale = (n, steps, root) => !steps || !steps.length || steps.includes((((n - root) % 12) + 12) % 12)
// SCALE-TABLES END
const ENGINE_TABLE_OK = ENGINE_ROWS.length === 22 && new Set(ENGINE_ROWS.map((r) => r[1])).size === 22
  && engineFromCc(64, 'panel') === 'Speech' && engineFromCc(69, 'panel') === 'Modal'   // the owner seam, pinned
  && engineFromCc(81, 'panel') === 'Vocoder' && engineFromCc(127, 'panel') === 'Hit Grains'
  && engineCcFor(2) === 14 && engineFromCc(14) === 'SuperWave'   // pane-side: midpoint decodes back by RECEIVE range
export { ENGINES_22, ENGINE_ROWS }  // smoke harness asserts the table contents

const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
const noteName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`
const isBlack = (n) => [1, 3, 6, 8, 10].includes(n % 12)

// v0.3.0 deep chord engine - interval-set dictionary (sorted root-relative
// pitch classes), all-root search with bass preference, inversions,
// symmetric flags, dyad/cluster/tone-set fallbacks. The old table had bugs
// ('0,4,7,9' labelled '6' - that is add9) and 18 entries; this has 40+.
const INTERVAL2 = ['unison', '\u266d2', 'maj2', 'min3', 'maj3', 'P4', 'tritone', 'P5', 'min6', 'maj6', 'min7', 'maj7']
const CHORD_TBL = {
  '0,3,7': 'minor', '0,4,7': 'major', '0,3,6': 'dim', '0,4,8': 'aug',
  '0,5,7': 'sus4', '0,2,7': 'sus2', '0,5,10': 'quartal stack',
  '0,4,9': '6', '0,3,9': 'm6', '0,4,7,9': '6', '0,3,7,9': 'm6',
  '0,2,4,7': 'add9', '0,2,3,7': 'm(add9)',
  '0,4,5,7': 'add11', '0,2,5,7': 'sus4add9',
  '0,4,7,11': 'maj7', '0,4,7,10': '7', '0,3,7,10': 'm7', '0,3,7,11': 'm(maj7)',
  '0,3,6,10': 'm7\u266d5', '0,3,6,9': 'dim7', '0,4,6,10': '7\u266d5', '0,4,8,10': '7\u266f5',
  '0,4,6,11': 'maj7\u266d5', '0,4,8,11': 'maj7\u266f5', '0,5,7,10': '7sus4', '0,5,7,11': 'maj7sus4',
  '0,2,4,9': '6/9', '0,2,3,9': 'm6/9', '0,2,4,7,9': '6add9',
  '0,1,4,7,10': '7\u266d9', '0,3,4,7,10': '7\u266f9', '0,1,4,6,10': '7\u266d9\u266d5',
  '0,4,6,7,10': '7\u266f11', '0,4,6,7,11': 'maj7\u266f11',
  // v0.6.0 theory-parity fix (test_theory.py found it): the 9th was spelled as
  // pitch class 9 (= the 6th), so C-E-G-A-Bb read as '9' when it is a 7add6.
  // Mirrored EXACTLY in dashboard/theory.py CHORD_TBL — the battery pins both.
  '0,2,4,7,10': '9', '0,4,7,9,10': '7add6',
  '0,2,4,7,11': 'maj9', '0,4,7,9,11': 'maj7add6',
  '0,2,3,7,10': 'm9', '0,3,7,9,10': 'm7add6',
  '0,2,3,7,11': 'm(maj9)', '0,3,7,9,11': 'm(maj7)add6',
  '0,2,4,5,7,10': '11', '0,4,5,7,9,10': '11add13',
  '0,2,3,5,7,10': 'm11', '0,3,5,7,9,10': 'm11add13',
  '0,2,4,7,9,10': '13', '0,2,4,5,7,9,10': '13add11',
  '0,2,3,7,9,10': 'm13', '0,2,3,5,7,9,10': 'm13add11',
}
function chordLabel(pcs, bassNote) {
  const set = [...new Set(pcs.map((p) => ((p % 12) + 12) % 12))].sort((a, b) => a - b)
  if (!set.length) return null
  if (set.length === 1) return NAMES[set[0]]
  if (set.length === 2) {
    const iv = (set[1] - set[0] + 12) % 12
    return `${NAMES[set[0]]}+${NAMES[set[1]]} ${INTERVAL2[iv]}`
  }
  const bassPc = bassNote == null ? null : ((bassNote % 12) + 12) % 12
  let best = null
  for (const r of set) {
    const rel = set.map((p) => (p - r + 12) % 12).sort((a, b) => a - b).join(',')
    const q = CHORD_TBL[rel]
    if (!q) continue
    const score = set.length * 2 + (r === bassPc ? 3 : 0) + (q === 'major' || q === 'minor' ? 0 : 2)
    if (!best || score > best.score) best = { r, q, rel, score }
  }
  if (!best) {
    let run = 1, cur = 1
    for (let i = 1; i < set.length; i++) { cur = set[i] - set[i - 1] === 1 ? cur + 1 : 1; run = Math.max(run, cur) }
    if (run >= 3) return `tone cluster ${set.map((p) => NAMES[p]).join(' ')}`
    return `tone set ${set.map((p) => NAMES[p]).join('+')}`
  }
  const relKey = (r2) => set.map((p) => (p - r2 + 12) % 12).sort((a, b) => a - b).join(',')
  const rots = set.filter((r2) => relKey(r2) === best.rel).length  // true symmetry: identical interval set under rotation
  let inv = ''
  if (bassPc != null && bassPc !== best.r) {
    const stack = best.rel.split(',').map(Number)
    const pos = stack.indexOf((bassPc - best.r + 12) % 12)
    inv = ` / ${NAMES[bassPc]}\u00b7${['root', '1st', '2nd', '3rd', '4th', '5th', '6th'][pos] || 'deep'} inv`
  }
  return `${NAMES[best.r]}${best.q === 'major' ? '' : best.q}${inv}${rots > 1 ? ' (symmetric)' : ''}`
}

// v0.2.7b/c OWNER REDRAW (mockup 2026-09-17) - matched LITERALLY: linear rows,
// big preset box, rows at the mockup's own heights (owner note: fill the top),
// SPICE + mini beds + pressure bar all excised; one keybed only.
// v0.2.7d lifted the block 66px; v0.2.7e restored the owner's octave mirrors
// under the keybed (bed back to y440). v0.3.0: row1 hugs the top (38/78) and
// the mods band y409-439 is now the signal analyzer (owner's louder ask).
const R = 21
const KNOBS = [
  // row 1 (y=170): GLIDE | osc quartet | filter pair | cycling-env quartet
  { cc: 5,   label: 'GLIDE',    x: 84,  y: 78, tip: 'time mode 0–10s; Shift selects time/synced/rate glide modes device-side' },
  { cc: 9,   label: 'TYPE',     x: 185, y: 78, clicky: true, tip: '22 detented engines - drag clicks through them like the encoder; shows the engine name, never a %' },
  { cc: 10,  label: 'WAVE',     x: 249, y: 78 },
  { cc: 12,  label: 'TIMBRE',   x: 313, y: 78 },
  { cc: 13,  label: 'SHAPE',    x: 377, y: 78 },
  { cc: 23,  label: 'CUTOFF',   x: 520, y: 78, tip: 'panel shows 16Hz–26.9kHz · exponential law between the endpoints (endpoints owner-confirmed, curve not per-point fitted)' },
  { cc: 83,  label: 'RESO',     x: 584, y: 78 },
  { cc: 102, label: 'RISE',     x: 733, y: 78, tip: 'panel shows 0ms–10.0s · Shift+turn: Shape 0–100 (no separate CC)' },
  { cc: 103, label: 'FALL',     x: 797, y: 78, tip: 'panel shows 0ms–10.0s · Shift+turn: Shape 0–100 (no separate CC)' },
  { cc: 28,  label: 'SUSTAIN',  x: 861, y: 78, tip: 'cycling-env hold: 0ms–10s (panel label Hold/Sustain)' },
  { cc: 24,  label: 'AMOUNT',   x: 925, y: 78 },
  // row 2 (y=270): RATE under GLIDE · LFO under filter · EG quartet under envelopes
  { cc: 91,  alts: [92], label: 'RATE', x: 84, y: 192, tip: 'unsynced 30–240bpm (CC91) · synced shows divisions (CC92) · Shift+turn: Swing 50–75% (device-side, no CC)' },          // ARP/SEQ rate (white encoder)
  { cc: 93,  alts: [94], label: 'RATE', x: 513, y: 192, tip: '0.06–100Hz log (manual §8) · press encoder = Sync, then divisions (CC94)' },          // LFO rate (white encoder)
  { cc: 105, label: 'ATTACK',   x: 733, y: 192 },
  { cc: 106, label: 'DEC/REL',  x: 797, y: 192 },
  { cc: 29,  label: 'SUSTAIN',  x: 861, y: 192 },
  { cc: 26,  label: 'F AMT',    x: 925, y: 192 },
]
const CC_KNOBS = KNOBS

const SRC_COLOR = {
  panel: '#4ade80',
  visualizer: 'var(--ui-accent)',
  hermes: 'var(--ui-accent)',
  sendlog: 'var(--ui-accent)',
  relay: '#f59e0b',
  preset: '#c084fc',
  macro: '#f472b6',
  clip: '#fb923c',
  ui: 'var(--ui-text-quaternary)',
  unknown: 'var(--ui-text-quaternary)',
}
const SRC_LABEL = { panel: 'PANEL', visualizer: 'LIVE', relay: 'FL', ui: 'virt', sendlog: 'log', hermes: 'H', preset: 'PRESET', macro: 'MACRO', clip: 'CLIP' }
// v0.4: mod lanes stamp provenance as macro:<id> / clip:<id> — colors/labels key on the prefix
const srcKey = (s) => (s || '').startsWith('macro:') ? 'macro' : (s || '').startsWith('clip:') ? 'clip' : (s || 'unknown')


// ---------------------------------------------------------------------------
function knobAngle(cc) {
  return -135 + Math.max(0, Math.min(127, cc)) / 127 * 270
}

function Knob({ k, state, onDrag, labelBelow = true }) {
  // rate knobs mirror whichever lane is live: free (CC91/93) or synced (CC92/94).
  // The freshest timestamp wins - so pressing the encoder into Sync flips the
  // display to divisions the moment the device starts sending CC92.
  const lanes = [k.cc, ...(k.alts || [])].map((c) => [c, state.cc[String(c)]]).filter(([, s]) => s)
  lanes.sort((a, b) => (a[1].ts || 0) - (b[1].ts || 0))
  const lane = lanes.length ? lanes[lanes.length - 1] : null
  const st = lane ? lane[1] : null
  const cc = st ? st.value : (k.static === 'master' ? 127 : k.static === 'preset' ? Math.min(127, state.program ?? 0) : 0)
  const angle = knobAngle(cc)
  const color = st ? (SRC_COLOR[srcKey(st.source)] || SRC_COLOR.unknown) : 'var(--ui-text-quaternary)'
  const isMoving = st && Date.now() / 1000 - st.ts < 0.6
  const r = k.small ? 14 : k.static ? 17 : R
  const shown = k.static === 'master' ? '100' : k.static === 'preset' ? `P${(state.preset?.slot ?? (state.program ?? 0) + 1)}` : fmtVal(lane ? lane[0] : k.cc, cc, st && st.source)
  return jsxs('g', {
    style: { cursor: k.cc != null ? 'ns-resize' : 'default' },
    transform: `translate(${k.x} ${k.y})`,
    onPointerDown: k.cc != null ? (e) => onDrag(k, e, cc) : undefined,
    children: [
      k.tip && jsx('title', { children: k.tip }),
      jsx('circle', { r: r + 5, fill: 'transparent' }),
      jsx('circle', { r, fill: 'var(--card)', stroke: isMoving ? color : 'var(--ui-stroke-secondary)', strokeWidth: isMoving ? 2.5 : 1.5 }),
      jsx('line', { x1: 0, y1: 0, x2: 0, y2: -(r - 4), stroke: color, strokeWidth: 3, strokeLinecap: 'round', transform: `rotate(${angle})` }),
      k.cc != null && jsx('path', { d: arcPath(r + 5, angle), stroke: color, strokeWidth: 2, fill: 'none', opacity: 0.7 }),
      labelBelow && jsx('text', { y: r + 12, textAnchor: 'middle', fontSize: k.small ? 6.5 : 8, fill: 'var(--ui-text-tertiary)', style: { letterSpacing: '0.5px' }, children: k.label }),
      labelBelow && k.clicky && jsx('text', { y: r + 21, textAnchor: 'middle', fontSize: 8, fontWeight: 600, fill: color, children: shown }),
      labelBelow && k.clicky && jsx('text', { y: r + 30, textAnchor: 'middle', fontSize: 6, fill: 'var(--ui-text-quaternary)', children: st ? SRC_LABEL[srcKey(st.source)] || st.source : '' }),
      labelBelow && !k.clicky && jsx('text', { y: r + 20, textAnchor: 'middle', fontSize: 7, fill: color, children: st ? `${SRC_LABEL[srcKey(st.source)] || st.source} ${shown}` : shown }),
    ],
  })
}

function arcPath(r, endAngle) {
  const a0 = (-135 - 90) * Math.PI / 180
  const a1 = (endAngle - 90) * Math.PI / 180
  const x0 = r * Math.cos(a0), y0 = r * Math.sin(a0)
  const x1 = r * Math.cos(a1), y1 = r * Math.sin(a1)
  const large = endAngle - -135 > 180 ? 1 : 0
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`
}

function SectionTitle({ x, y, children }) {
  return jsx('text', { x, y, fontSize: 8.5, fill: 'var(--ui-text-quaternary)', style: { letterSpacing: '1.2px' }, children: children })
}

// v0.4.3: onHold = long-press (550 ms) or right-click clears the pill instead
// of cycling (owner: "I have to scroll thru all the options to go back to
// base"). longHoldFiredAt is MODULE-LEVEL on purpose: the pill re-renders at
// poll rate and a per-render flag would reset mid-hold, letting the click that
// follows the release cycle the pill anyway. The 250 ms window eats that click.
let longHoldFiredAt = 0
function LedButton({ x, y, w = 46, label, on = false, color = 'var(--ui-accent)', onClick, onHold }) {
  const holdDown = onHold && ((e) => {
    e.preventDefault?.()
    const t = setTimeout(() => { longHoldFiredAt = Date.now(); onHold() }, 550)
    const cancel = () => { clearTimeout(t); window.removeEventListener('pointerup', cancel); window.removeEventListener('pointerleave', cancel) }
    window.addEventListener('pointerup', cancel)
    window.addEventListener('pointerleave', cancel)
  })
  return jsxs('g', { transform: `translate(${x} ${y})`, style: { cursor: 'pointer' },
    onClick: onClick && ((e) => { if (Date.now() - longHoldFiredAt < 250) return; onClick(e) }),
    onPointerDown: holdDown || undefined,
    onContextMenu: onHold && ((e) => { e.preventDefault?.(); longHoldFiredAt = Date.now(); onHold() }), children: [
    jsx('rect', { x: 0, y: 0, width: w, height: 20, rx: 4, fill: on ? color : 'var(--card)', stroke: 'var(--ui-stroke-secondary)', opacity: on ? 0.85 : 1 }),
    jsx('text', { x: w / 2, y: 13.5, textAnchor: 'middle', fontSize: 7.5, fill: on ? 'var(--card)' : 'var(--ui-text-tertiary)', children: label }),
  ] })
}

function RoundButton({ x, y, r = 13, label, on = false, color = 'var(--ui-accent)', onClick }) {
  return jsxs('g', { transform: `translate(${x} ${y})`, style: { cursor: 'pointer' }, onClick, children: [
    jsx('circle', { r, fill: on ? color : 'var(--card)', opacity: on ? 0.85 : 1, stroke: 'var(--ui-stroke-secondary)' }),
    jsx('circle', { r: 3.5, cy: -(r + 5), fill: 'var(--ui-text-quaternary)' }),
    jsx('text', { y: r + 12, textAnchor: 'middle', fontSize: 7, fill: 'var(--ui-text-tertiary)', children: label }),
  ] })
}

// Octave windows BELOW and ABOVE the main bed (owner photo annotation),
// same proportions scaled down. 24 keys each, C-aligned, TILING the pitch
// space: [anchor-24, anchor-1] and [anchor+25, anchor+48]. They must NOT
// share a note with the main bed (anchor..anchor+24): with keyboard-hold
// latching, one note owned by two keyboards became a fight - latch here,
// release there, the arpeggiator pool winking (owner report v0.2.6c).
function MiniBed({ lo, x: ox, y, w, caption, notes, onDown, onUp, steps = null, root = 0 }) {
  const dead = (n) => !!(steps && steps.length) && !inScale(n, steps, root)
  const H = (w / 15) * (128 / 64.8)                 // identical key proportions, scaled down
  const bh = H * 0.61, bwm = (w / 15) * 0.556
  const whites = []
  for (let off = 0; off <= 23; off++) if (!isBlack(lo + off)) whites.push(lo + off)
  const ww = w / whites.length
  const whitesEls = []
  const blackEls = []
  let wi = 0
  for (let off = 0; off <= 23; off++) {
    const n = lo + off
    if (isBlack(n)) {
      const bx = wi * ww - bwm / 2
      blackEls.push(jsxs('g', { transform: `translate(${ox + bx} ${y})`, onPointerDown: (e) => onDown(n, e), onPointerUp: () => onUp(n), onPointerLeave: () => onUp(n), style: { cursor: 'pointer' }, children: [
        jsx('rect', { width: bwm, height: bh, rx: 1.5, fill: notes.has(n) ? 'var(--ui-accent)' : '#1e293b', stroke: 'var(--ui-stroke-secondary)', strokeWidth: 0.4, opacity: notes.has(n) ? 1 : (dead(n) ? 0.22 : 0.6) }),
      ] }, `mb${n}`))
      continue
    }
    whitesEls.push(jsxs('g', { transform: `translate(${ox + wi * ww} ${y})`, onPointerDown: (e) => onDown(n, e), onPointerUp: () => onUp(n), onPointerLeave: () => onUp(n), style: { cursor: 'pointer' }, children: [
      jsx('rect', { x: 0.5, y: 0, width: ww - 1, height: H, rx: 1.5, fill: notes.has(n) ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)', opacity: notes.has(n) ? 0.9 : (dead(n) ? 0.1 : 0.28) }),
      n % 12 === 0 && jsx('text', { x: ww / 2, y: H - 3, textAnchor: 'middle', fontSize: 5, fill: 'var(--ui-text-quaternary)', children: noteName(n) }),
    ] }, `mw${n}`))
    wi++
  }
  // BLACKS AFTER WHITES: SVG paint/hit order is document order, so an
  // interleaved black key used to sit UNDER its right-hand white neighbour -
  // clicking the visible top key hit the bottom one (owner report v0.2.6).
  // The main bed already draws blacks last; now the minis do too.
  return jsxs('g', { children: [
    caption ? jsx('text', { x: ox, y: y - 2, fontSize: 6, fill: 'var(--ui-text-quaternary)', children: caption }) : null,
    ...whitesEls, ...blackEls,
  ] })
}

function Keybed({ notes, anchor, onDown, onUp, noteStats, steps = null, root = 0 }) {
  const els = []
  const W = 972
  const whiteW = W / 15
  const blacks = []
  let wi = 0
  const dead = (n) => !!(steps && steps.length) && !inScale(n, steps, root)
  const tip = (n) => {
    if (dead(n)) return `${noteName(n)} - out of ${steps.length}-tone scale (locked: presses do nothing)`
    const st = noteStats && noteStats[String(n)]
    if (!st) return `${noteName(n)} - not played yet this session`
    return `${noteName(n)} x${st.count} | avg vel ${Math.round(st.avg_vel)} | max ${st.max_vel} | ${Math.round(st.total_ms / Math.max(1, st.count))}ms avg | with: ${st.last_with.map(noteName).join(' ') || 'alone'}`
  }
  for (let off = 0; off <= 24; off++) {
    const n = anchor + off
    if (isBlack(n)) { blacks.push({ n, x: wi * whiteW }); continue }
    const lit = notes.has(n)
    els.push(jsxs('g', { transform: `translate(${wi * whiteW} 0)`, onPointerDown: (e) => onDown(n, e), onPointerUp: () => onUp(n), onPointerLeave: () => onUp(n), style: { cursor: 'pointer' }, children: [
      jsx('title', { children: tip(n) }),
      jsx('rect', { x: 1, y: 0, width: whiteW - 2, height: 128, rx: 3, fill: lit ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)', opacity: lit ? 0.9 : (dead(n) ? 0.12 : 0.35) }),
      jsx('text', { x: whiteW / 2, y: 120, textAnchor: 'middle', fontSize: 7.5, fill: 'var(--ui-text-quaternary)', children: noteName(n) }),
    ] }, `w${n}`))
    wi++
  }
  for (const { n, x } of blacks) {
    const lit = notes.has(n)
    els.push(jsxs('g', { transform: `translate(${x - 18} 0)`, onPointerDown: (e) => onDown(n, e), onPointerUp: () => onUp(n), onPointerLeave: () => onUp(n), style: { cursor: 'pointer' }, children: [
      jsx('title', { children: tip(n) }),
      jsx('rect', { x: 0, y: 0, width: 36, height: 78, rx: 3, fill: lit ? 'var(--ui-accent)' : '#1e293b', stroke: 'var(--ui-stroke-secondary)', strokeWidth: 0.5, opacity: lit ? 1 : (dead(n) ? 0.25 : 1) }),
      jsx('text', { x: 18, y: 72, textAnchor: 'middle', fontSize: 6.5, fill: '#64748b', children: noteName(n) }),
    ] }, `b${n}`))
  }
  return jsx('g', { children: els })
}

// ---------------------------------------------------------------------------
function Visualizer() {
  const [state, setState] = useState({ cc: {}, events: [], params: {}, listening: false, live: false, program: null, bank: 0, active_notes: [], matrix: null, seq: 0, pressure: 0, note_stats: {}, chords: [], reading: false, spectrum: null, api: 0, macros: [], clips: [], clips_playing: [], recording: false, loops: [], loops_playing: [], loop_rec: null })
  const [status, setStatus] = useState('connecting…')
  const [anchor, setAnchor] = useState(48)
  const [slotInput, setSlotInput] = useState(null)
  const localNotesRef = useRef(new Map())
  const [localNotes, setLocalNotes] = useState(() => new Set())
  // v0.3.0: debounced chord plate, progression history, analyzer smoothing buffers
  const pendRef = useRef({ key: null, at: 0 })
  const plateRef = useRef({ key: null, name: '', tones: '', at: 0 })
  const [prog, setProg] = useState([])
  const smRef = useRef({ vals: new Array(64).fill(0), peaks: new Array(64).fill(0) })
  // v0.4.2 fast analyzer: latest WIRE bands live in a ref (never in React state -
  // the rAF loop paints the bars, so spectrum updates must not re-render the tree).
  const spRef = useRef({ bands: null, model: false })
  const spBarRefs = useRef([])      // the 64 bar rects; rAF owns y/height/fill
  const spPkRefs = useRef([])       // the 64 peak-hold rects; rAF owns y/height
  const spFailRef = useRef(0)       // consecutive /spectrum failures
  const srcRef = useRef(null)       // v0.4.4: last announced analyzer source (status line only on change)
  const spFastRef = useRef(true)    // false once the backend proves it has no /spectrum (pre-restart skew)
  const pendingRef = useRef(new Map())
  const lastPostRef = useRef(0)
  const anchorRef = useRef(48)
  const lastPushRef = useRef(0)
  const inFlightRef = useRef(false)
  const [localErr, setLocalErr] = useState(null)
  const [panicFlash, setPanicFlash] = useState(false)

  // The window follows the HARDWARE octave only - never your own clicks.
  // Two guard rails learned the hard way (screenshot 2026-09-16):
  //  - only source 'panel' notes (caller filters) may move it at all,
  //  - a note must be >7 semitones OUTSIDE the window to shift it, so the
  //    top C# (a+25) no longer drags the whole keyboard an octave up.
  const reAnchor = (notesList) => {
    let a = anchorRef.current
    for (const n of notesList) {
      while (n > a + 31 && a < 76) a += 12
      while (n < a - 7 && a > 24) a -= 12
    }
    if (a !== anchorRef.current) { anchorRef.current = a; setAnchor(a) }
  }

  const refresh = useCallback(async () => {
    try {
      const s = await api.rest('/state')
      if (s.cc) {
        for (const [cc, val] of pendingRef.current) {
          const cur = s.cc[cc]
          if (!cur || cur.value === val.value) { pendingRef.current.delete(cc) }
          else { s.cc[cc] = { ...cur, value: val.value, source: 'ui', ts: val.ts } }
        }
      }
      { const t = Date.now() / 1000
        reAnchor((s.events || []).filter((e) => e.kind === 'note' && e.velocity && e.source === 'panel' && e.ts > t - 20).map((e) => e.note)) }
      setState((prev) => ({ ...prev, ...s }))
      // v0.4.2: when the fast /spectrum poll is dead (pre-restart backend),
      // the 4 Hz /state keeps feeding the rAF painter instead.
      if (s.spectrum && !spFastRef.current) spRef.current = { bands: s.spectrum.bands || null, model: !!s.spectrum.model }
      setStatus(s.mock ? 'mock backend' : 'connected')
    } catch (e) {
      setStatus('backend unreachable — reload desktop plugins / enable plugin?')
    }
  }, [])

  const onDelta = useCallback((msg) => {
    if (!msg || msg.type !== 'delta') return
    lastPushRef.current = Date.now()
    setState((prev) => {
      const merged = { ...prev }
      for (const key of ['cc', 'program', 'bank', 'live', 'listening', 'listen_error', 'preset',
                         'preset_error', 'active_notes', 'pressure', 'reading', 'note_stats', 'chords']) {
        if (key in msg) merged[key] = msg[key]
      }
      merged.seq = msg.seq ?? prev.seq
      if (msg.events && msg.events.length) merged.events = [...prev.events, ...msg.events].slice(-120)
      return merged
    })
    for (const [cc, val] of pendingRef.current) {
      if (Date.now() / 1000 - val.ts < 1.2) {
        setState((prev) => ({ ...prev, cc: { ...prev.cc, [cc]: { value: val.value, source: 'ui', ts: val.ts } } }))
      }
    }
    // anchor follows ONLY physical key presses (see reAnchor guard rails)
    const seen = (msg.events || []).filter((e) => e.kind === 'note' && e.velocity && e.source === 'panel').map((e) => e.note)
    reAnchor(seen)
  }, [])

  useEffect(() => {
    refresh()
    let dispose = null
    try {
      if (api && typeof api.socket === 'function') dispose = api.socket('/events', onDelta)
    } catch { /* older SDK */ }
    // v0.4.2: 4 Hz /state stays (held-chord timer, knob truth, mod lanes) but
    // spectrum no longer depends on it - the FAST ANALYZER effect below owns
    // that lane now. The redundant 5 s full-poll is gone.
    const t = setInterval(() => {
      if (!inFlightRef.current) {
        inFlightRef.current = true
        refresh().finally(() => { inFlightRef.current = false })
      }
    }, 250)
    return () => { if (dispose) dispose(); clearInterval(t) }
  }, [refresh, onDelta])

  // ---- v0.4.2 FAST ANALYZER (owner: "the audio spectrum visualizer is very
  // laggy, make it snappy and direct"). Old path: bands rode the heavy 4 Hz
  // /state poll and were smoothed ONCE PER RENDER (x0.72/poll = ~1 s tails +
  // up to 250 ms arrival delay), every value through the React reconciler.
  // Now: (1) tiny GET /spectrum polled ~16 Hz into a REF (zero re-renders);
  // (2) ONE paint loop with TIME-based smoothing - rise instant, LINEAR fall
  // (v0.5.1 owner fix: full scale -> 0 within the 0.5 s ceiling; the old
  // exp(-dt/0.15) read as fast mid-fall but left a slow dB-space tail on
  // screen), peaks fall faster than the bar - writing y/height/fill straight
  // to the 64 bar + 64 peak rects via refs. Backend older than the pane (no /spectrum,
  // routes mount only at boot): after 4 failed polls spFastRef flips off and
  // refresh() feeds spRef from /state instead - same painter, coarser arrival.
  useEffect(() => {
    let stop = false, flying = false, lastCol = null, raf = 0
    const HAS_RAF = typeof requestAnimationFrame === 'function'
    const nowMs = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now())
    let last = nowMs()
    const loop = (now) => {
      if (stop) return
      const dt = Math.min(0.25, Math.max(0.001, (now - last) / 1000))
      last = now
      const sm = smRef.current, bands = spRef.current.bands, model = spRef.current.model
      const FALL_PER_S = 1 / 0.2    // v0.5.1a: matches backend SPEC_RELEASE_S=0.2 (owner: SNAPPY)
      const PK_FALL_PER_S = 1 / 0.3 // peak ticks lead the bar, clear by 0.3 s — no tick outlives the bar law
      const col = model ? '#3d5a68' : '#16a34a'
      const colChanged = col !== lastCol
      lastCol = col
      for (let i = 0; i < 64; i++) {
        const v = (bands && bands[i]) || 0
        const nv = Math.max(v, sm.vals[i] - FALL_PER_S * dt)
        sm.vals[i] = nv
        sm.peaks[i] = Math.max(nv, sm.peaks[i] - PK_FALL_PER_S * dt)
        const bar = spBarRefs.current[i]
        if (bar) {
          const h = Math.max(0.5, nv * 25)
          bar.setAttribute('y', String(436 - h))
          bar.setAttribute('height', String(h))
          if (colChanged) bar.setAttribute('fill', col)
        }
        const pk = spPkRefs.current[i]
        if (pk) {
          const hp = sm.peaks[i] * 25, bh = Math.max(0.5, nv * 25)
          if (hp > bh + 1.5) { pk.setAttribute('y', String(436 - hp - 1)); pk.setAttribute('height', '1.2') }
          else pk.setAttribute('height', '0')
        }
      }
      if (HAS_RAF) raf = requestAnimationFrame(loop)
    }
    if (HAS_RAF) raf = requestAnimationFrame(loop)
    const tick = () => {
      if (!spFastRef.current || flying) return
      flying = true
      api.rest('/spectrum').then((r) => {
        if (r && Array.isArray(r.bands)) {
          spRef.current = { bands: r.bands, model: !!r.model }; spFailRef.current = 0
          // v0.4.4 honesty: name the analyzer's real source the moment it changes
          const who = r.model ? 'midi-model (audio off)' : (r.src || '?')
          if (who !== srcRef.current) { srcRef.current = who; setStatus(`analyzer: ${who}`) }
        }
        else if (++spFailRef.current >= 4) spFastRef.current = false
      }).catch(() => { if (++spFailRef.current >= 4) spFastRef.current = false }).finally(() => { flying = false })
    }
    const t = setInterval(tick, 60)
    tick()
    return () => {
      stop = true
      if (HAS_RAF && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf)
      clearInterval(t)
    }
  }, [api])

  const postCc = useCallback((cc, value) => {
    pendingRef.current.set(String(cc), { value, ts: Date.now() / 1000 })
    api.rest('/cc', { method: 'POST', body: { cc, value } }).catch(() => {})
  }, [])

  const onDrag = useCallback((k, e, startVal) => {
    e.preventDefault()
    e.target.setPointerCapture?.(e.pointerId)
    const start = { y: e.clientY }
    // v0.3.2: start from the DISPLAYED value (the freshest of a knob's
    // lanes) - dragging a synced RATE no longer teleports to stale CC91.
    const startPct = toPct(startVal ?? state.cc[String(k.cc)]?.value ?? 0)
    const live = state.live
    const snap = (v) => (k.clicky ? engineCcFor(engineIdxFromCc(v)) : v)
    const move = (ev) => {
      const pct = Math.max(0, Math.min(100, startPct + (start.y - ev.clientY) * 0.5))
      const v = snap(toCc(pct))
      pendingRef.current.set(String(k.cc), { value: v, ts: Date.now() / 1000 })
      setState((prev) => ({ ...prev, cc: { ...prev.cc, [String(k.cc)]: { value: v, source: live ? 'visualizer' : 'ui', ts: Date.now() / 1000 } } }))
      if (live && Date.now() - lastPostRef.current > 40) {
        lastPostRef.current = Date.now()
        api.rest('/cc', { method: 'POST', body: { cc: k.cc, value: v } }).catch(() => {})
      }
    }
    const up = (ev) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      const pct = Math.max(0, Math.min(100, startPct + (start.y - ev.clientY) * 0.5))
      postCc(k.cc, snap(toCc(pct)))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [state, postCc])

  // Keyboard-hold (CC64) latch mode: a pressed note sustains until pressed
  // AGAIN (hardware behavior). The pane mirrors it: a latched click never
  // expires visually and sends NO auto note-off; a second click releases.
  const holdOn = (state.cc['64']?.value ?? 0) > 63
  const [scaleIdx, setScaleIdx] = useState(0)
  const [keyIdx, setKeyIdx] = useState(0)
  const playNote = useCallback((n0) => {
    const map = localNotesRef.current
    // v0.4.3 SCALE LOCK gate: while locked, out-of-scale presses are dead (the
    // beds dim them). Releasing an ALREADY-LATCHED note is never gated - the
    // latch can predate a scale switch and a gated note-off would strand it.
    if (!(holdOn && map.get(n0) === Infinity) && !inScale(n0, SCALES[scaleIdx][1], keyIdx)) {
      setStatus(`${noteName(n0)} - out of ${KEY_NAMES[keyIdx]} ${SCALES[scaleIdx][0]} (scale lock)`)
      return
    }
    const n = n0   // SCALE LOCK choke point: latch map, POSTs and releases all see the SENT note
    if (holdOn) {
      if (map.get(n) === Infinity) {
        map.delete(n)
        setLocalNotes(new Set(map.keys()))
        api.rest('/note', { method: 'POST', body: { note: n, velocity: 0 } }).catch(() => {})
      } else {
        map.set(n, Infinity)
        setLocalNotes(new Set(map.keys()))
        api.rest('/note', { method: 'POST', body: { note: n, velocity: 96, hold: true } }).then((r) => {
          // backend only latches while LIVE and the note actually reached the
          // device. No latch => no audio is sustaining, so downgrade the light
          // to the normal flash (keys staying lit silently was owner-reported).
          if (r && r.hold) return
          if (map.get(n) !== Infinity) return
          map.set(n, Date.now() + 260)
          setTimeout(() => {
            const exp = map.get(n)
            if (exp !== Infinity && exp && Date.now() >= exp) {
              map.delete(n)
              setLocalNotes(new Set(map.keys()))
            }
          }, 300)
        }).catch(() => {})
      }
      return
    }
    map.set(n, Date.now() + 260)
    setLocalNotes(new Set(map.keys()))
    const tick = () => {
      const now = Date.now()
      let changed = false
      for (const [k2, exp] of map) { if (exp <= now) { map.delete(k2); changed = true } }
      if (changed) setLocalNotes(new Set(map.keys()))
    }
    setTimeout(tick, 300)
    api.rest('/note', { method: 'POST', body: { note: n, velocity: 96 } }).catch(() => {})
  }, [holdOn, scaleIdx, keyIdx])

  // v0.4.1 press-to-speak (owner: "it should keep the key active as long as I'm
  // pressing"): pointerDOWN fires the note, pointerUP/LEAVE releases it. No
  // more "activates only when you let go". Same SCALE LOCK choke point, so
  // press, release, device light and sendlog all carry the SAME sent note.
  // hold:true = the backend schedules no auto-release while LIVE (v0.2.6b
  // contract); virtual mode sustains the light via pressedRef. Refcounts the
  // same pitch under multiple pointers (touchscreen chords). CC64 holdOn mode
  // keeps the classic click-toggle (latching IS the feature there).
  const pressedRef = useRef(new Map())      // sent note -> pointer count
  const noteDown = useCallback((n0, ev) => {
    ev?.preventDefault?.()
    if (holdOn) { playNote(n0); return }
    if (!inScale(n0, SCALES[scaleIdx][1], keyIdx)) { setStatus(`${noteName(n0)} - out of ${KEY_NAMES[keyIdx]} ${SCALES[scaleIdx][0]} (scale lock)`); return }
    const n = n0
    const cnt = pressedRef.current.get(n) || 0
    pressedRef.current.set(n, cnt + 1)
    if (cnt === 0) {
      localNotesRef.current.set(n, Infinity)
      setLocalNotes(new Set(localNotesRef.current.keys()))
      api.rest('/note', { method: 'POST', body: { note: n, velocity: 96, hold: true } }).catch(() => {})
    }
  }, [holdOn, scaleIdx, keyIdx])
  const noteUp = useCallback((n0) => {
    if (holdOn) return                       // latch mode: clicks own notes, pointers don't
    const n = n0                             // v0.4.3: ungated by law - a scale switched mid-press must not strand the note-off
    const cnt = pressedRef.current.get(n)
    if (!cnt) return
    if (cnt > 1) { pressedRef.current.set(n, cnt - 1); return }
    pressedRef.current.delete(n)
    const map = localNotesRef.current
    if (map.get(n) === Infinity) map.delete(n)
    setLocalNotes(new Set(map.keys()))
    api.rest('/note', { method: 'POST', body: { note: n, velocity: 0 } }).catch(() => {})
  }, [holdOn, scaleIdx, keyIdx])
  useEffect(() => {                          // alt-tab mid-press must never strand a held note
    const drop = () => { for (const n of [...pressedRef.current.keys()]) noteUp(n) }
    window.addEventListener('blur', drop)
    return () => window.removeEventListener('blur', drop)
  }, [noteUp])

  // Leaving hold mode releases the pane's latches too (a stray note-off is a
  // harmless no-op on the device if it already let go).
  useEffect(() => {
    if (holdOn) return
    const map = localNotesRef.current
    let changed = false
    for (const [n2, exp] of map) {
      if (exp === Infinity) {
        map.delete(n2)
        changed = true
        api.rest('/note', { method: 'POST', body: { note: n2, velocity: 0 } }).catch(() => {})
      }
    }
    if (changed) setLocalNotes(new Set(map.keys()))
  }, [holdOn])

  const toggle = useCallback(async (path, body) => {
    try { await api.rest(path, { method: 'POST', body }); refresh() } catch { /* status covers it */ }
  }, [refresh])

  // v0.3.2: a 512-slot device is bank*128 + program + 1. The old guess ignored bank
  // (bank 1 / pgm 5 showed P6 - wrong slot in the P-box AND in the ⟳/⟲ default).
  const currentSlot = state.preset?.slot ?? (state.program != null
    ? (state.bank ?? 0) * 128 + state.program + 1 : 1)
  const readPreset = useCallback(async () => {
    const slot = Number(slotInput) || currentSlot
    setLocalErr(null)
    try {
      const r = await api.rest('/preset/read', { method: 'POST', body: { slot } })
      if (r && r.ok) { setSlotInput(String(slot)); setStatus(`reading slot ${slot}…`) }
      else setLocalErr((r && r.error) || 'read refused')
    } catch (e) {
      setLocalErr(`backend lacks /preset/read (${String(e).slice(0, 40)}) — fully QUIT and reopen the Hermes app; backend code loads at boot, plugin reload alone is not enough`)
    }
  }, [slotInput, currentSlot])

  const rollback = useCallback(() => {
    setLocalErr(null)
    api.rest('/program', { method: 'POST', body: { slot: Number(slotInput) || currentSlot } })
      .then((r) => { if (r && !r.ok) setLocalErr(r.error) })
      .catch((e) => setLocalErr(`rollback failed: ${String(e).slice(0, 40)} — is the backend current (restart the app)?`))
    refresh()
  }, [slotInput, currentSlot, refresh])

  const engine = useMemo(() => {
    const s9 = state.cc['9']
    return !s9 || s9.value == null ? null : engineFromCc(s9.value, s9.source)
  }, [state.cc])

  const nameByCc = useMemo(() => {
    const m = {}
    for (const [name, p] of Object.entries(state.params || {})) m[p.cc] = name
    return m
  }, [state.params])

  const fileRef = useRef(null)
  const importMfp = useCallback((ev) => {
    const f = ev.target.files?.[0]
    if (!f) return
    const rd = new FileReader()
    rd.onload = async () => {
      const bytes = new Uint8Array(rd.result)
      let bin = ''
      for (let i = 0; i < bytes.length; i += 8192) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192))
      }
      const r = await api.rest('/preset/import', { method: 'POST', body: { data_b64: btoa(bin) } }).catch((e) => ({ ok: false, error: String(e) }))
      setStatus(r && r.ok ? `preset loaded: ${r.name}` : `import failed: ${(r && r.error) || 'unknown'}`)
      refresh()
    }
    rd.readAsArrayBuffer(f)
    ev.target.value = ''
  }, [refresh])

  const latest = state.events?.[state.events.length - 1]
  const notes = new Set([...(state.active_notes || []), ...localNotes])
  const noteList = [...notes].sort((a, b) => a - b)
  const chord = noteList.length >= 2 ? chordLabel(noteList, noteList[0]) : null
  // 150ms settle keeps chord runs from flickering the plate through garbage;
  // the setTimeout ping gives the commit a render even on a quiet socket.
  const nowMs = Date.now()
  const cKey = noteList.join(',')
  if (noteList.length >= 2 && chord) {
    if (cKey !== pendRef.current.key) {
      pendRef.current = { key: cKey, at: nowMs }
      setTimeout(() => setProg((x) => [...x]), 170)
    } else if (nowMs - pendRef.current.at >= 150 && cKey !== plateRef.current.key) {
      plateRef.current = { key: cKey, name: chord, tones: noteList.map(noteName).join(' '), at: nowMs }
      setProg((x) => [...x.slice(-7), chord.replace(/\s\/.*$/, '').replace(' (symmetric)', '')])
    }
  }
  const plate = plateRef.current.key && nowMs - plateRef.current.at < 4000 ? plateRef.current : null
  const sp = state.spectrum
  const spTag = !sp ? 'analyzer: backend too old' : sp.model ? 'analyzer: MIDI model - no audio capture' : `analyzer: ${String(sp.src).slice(0, 34)}`
  const hotKeys = Object.entries(state.note_stats || {}).sort((a, b) => b[1].count - a[1].count).slice(0, 6)
  const maxHot = hotKeys.length ? hotKeys[0][1].count : 1
  const chords = (state.chords || []).filter((c) => c.pcs.length >= 2).slice(0, 8)
  const pushLive = Date.now() - lastPushRef.current < 1600
  const staleBackend = (state.api ?? 0) !== 4 && state.seq > 0
  const errLine = localErr || state.preset_error

  return jsxs('div', { className: 'flex flex-col gap-3 p-4 max-w-[1240px]', children: [
    // header / controls
    jsxs('div', { className: 'flex items-center gap-3 flex-wrap', children: [
      jsx('h1', { className: 'text-lg font-semibold', children: 'MicroFreak Visualizer' }),
      jsxs('span', { style: { fontSize: 12, color: pushLive ? '#4ade80' : 'var(--ui-text-quaternary)' }, children: [pushLive ? '● live push' : '○ polling', ' · ', status] }),
      jsx('button', { type: 'button', onClick: () => toggle('/listen', { enabled: !state.listening }), style: btn(state.listening), children: state.listening ? '◉ Listening (MIDI IN)' : '○ Listen: knob turns' }),
      jsx('button', { type: 'button', onClick: () => toggle('/live', { enabled: !state.live }), style: btn(state.live, state.live && '#dc2626'), children: state.live ? '⬤ LIVE — sends hit the device' : '○ Live-out: off (virtual)' }),
      jsxs('span', { className: 'flex items-center gap-1', children: [
        jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-tertiary)' }, children: 'slot' }),
        jsx('input', { type: 'number', min: 1, max: 512, placeholder: String(currentSlot), value: slotInput ?? '', onChange: (e) => setSlotInput(e.target.value), style: { width: 58, fontSize: 12, padding: '5px 6px', borderRadius: 6, border: '1px solid var(--ui-stroke-secondary)', background: 'transparent', color: 'var(--ui-text-secondary)' } }),
        jsx('button', { type: 'button', onClick: readPreset, style: btn(false, '#c084fc'), children: '⟳ Read preset' }),
        state.live && jsx('button', { type: 'button', onClick: rollback, style: btn(false, '#dc2626'), children: '⟲ Reload (bank+PC)' }),
      ] }),
      jsx('button', { type: 'button', onClick: () => fileRef.current?.click(), style: btn(false), children: '⤓ Load .mfp' }),
      jsx('input', { ref: fileRef, type: 'file', accept: '.mfp,.mbp', style: { display: 'none' }, onChange: importMfp }),
      jsxs('span', { style: { fontSize: 12, color: 'var(--ui-text-secondary)' }, children: ['now: ', noteList.length ? noteList.map(noteName).join(' ') : '—', chord ? ` (${chord})` : ''] }),
    ] }),
    staleBackend && jsx('div', { style: { fontSize: 12, color: '#f87171' }, children: '⚠ The pane is newer than the running backend (no /events or /preset/read). Fully QUIT the Hermes app and reopen — backend routes mount only at boot; plugin reload is not enough.' }),
    jsx('div', { style: { fontSize: 11, color: errLine ? '#fbbf24' : 'var(--ui-text-tertiary)' }, children: errLine ? `⚠ ${errLine}` : `slot ${currentSlot}${state.preset?.name ? ` · ${state.preset.name}` : ' (guessed from sendlog — ⟳ Read to confirm)'} · time/bpm/Hz knobs display like the panel · ⟳ Read = saved state (SAVE on device first if you edited) · keys follow the device octave · hover a key for stats` }),

    // ---- the panel (photo-accurate placement) ----
    jsx('svg', { viewBox: '0 0 1000 664', style: { width: '100%', background: 'var(--card)', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 10 }, children: jsxs('g', { children: [
      // top strip (v0.2.7a): OLED | trail readout - top-left quadrant is now
      // FREED, reserved for the owner-directed reorg + skins pass. The old
      // mod-matrix board and the ARP / SEQ header are excised: its cells
      // POSTed /matrix (local JSON, zero MIDI) - a fake write path, not a mirror.
      // v0.2.6d sweep: every panel-side-only control (MATRIX/PARA/PANEL/SAVE/
      // UTILITY buttons, static PRESET+MASTER knobs, TYPE/MODE/SHIFT, the six
      // cycleBtn arp-seq-LFO-env mirrors, RANDOM/PATTERN/DICE/BEND/UP|A/ORDER|B)
      // is GONE from the face - they could never read or write MIDI, only tease.
      // Freed slots are reserved for pane-native controls (owner direction).
      jsxs('g', { children: [
        // v0.2.7b: the mockup's big P-box - bottom-right quadrant, same bindings,
        // now readable at a glance across the room instead of a 9pt strip.
        jsx('rect', { x: 709, y: 246, width: 271, height: 162, rx: 10, fill: '#0a0a0f', stroke: 'var(--ui-stroke-secondary)' }),
        jsx('text', { x: 725, y: 292, fontSize: 24, fontWeight: 600, fill: '#e2e8f0', children: `P${currentSlot}${state.preset?.name ? ' ' + String(state.preset.name).slice(0, 13) : state.reading ? ' reading' : ''}` }),
        jsx('text', { x: 725, y: 328, fontSize: 15, fill: '#93c5fd', children: engine ? engine.slice(0, 17) : '—' }),
        jsx('text', { x: 725, y: 356, fontSize: 12, fill: state.pressure > 0 ? '#facc15' : '#64748b', children: `press ${toPct(state.pressure || 0)}%` }),
        jsx('text', { x: 725, y: 382, fontSize: 12, fill: state.live ? '#4ade80' : '#475569', children: state.live ? 'LIVE ARMED' : 'virtual' }),
      ] }),
      jsxs('g', { transform: 'translate(140 356)', children: [
        plate ? jsx('text', { fontSize: 11, fontWeight: 700, fill: '#4ade80', children: `\u266a ${plate.name}` }, 'plate')
              : jsx('text', { fontSize: 7.5, fill: '#64748b', children: latest ? `${latest.kind} ${latest.cc != null ? 'CC' + latest.cc + ' ' + toPct(latest.value) + '%' : latest.note != null ? noteName(latest.note) : ''} ${latest.source || ''}`.slice(0, 42) : '' }, 'lately'),
        jsx('text', { y: 13, fontSize: 7.5, fill: '#94a3b8', children: plate ? `${plate.tones} \u00b7 held ${Math.round((nowMs - plate.at) / 100) / 10}s` : 'play two or more keys \u2014 deep chord naming is listening' }, 'tones'),
        jsx('text', { y: 23, fontSize: 7, fill: '#64748b', children: `${prog.length ? prog.slice(-6).join(' \u2192 ') : 'progression waits for your first chord'} \u00b7 keys ${noteName(anchor)}\u2013${noteName(anchor + 24)} \u00b7 ${spTag}` }, 'ctx'),
      ] }),
      // v0.4.2 signal analyzer (owner: "very laggy - snappy and direct"): React
      // renders only the STATIC furniture (bg, 64 bars, 64 peak lines, Hz grid,
      // carrier markers). The FAST ANALYZER effect owns bar y/height/fill via
      // refs - no band value passes through the reconciler anymore. 'sp' key
      // prefix is law (b0..b63 collided with black-key tags). Bars are pushed
      // in a FIRST pass, peaks in a SECOND: SVG paint/hit order is document
      // order (v0.2.6 black-key lesson).
      (() => {
        const BW = 972 / 64
        const els = [jsx('rect', { x: 14, y: 409, width: 972, height: 30, rx: 6, fill: '#0a0a0f', stroke: 'var(--ui-stroke-secondary)' }, 'spsbg')]
        for (let i = 0; i < 64; i++) {
          els.push(jsx('rect', { x: 14 + i * BW, y: 435.5, width: BW - 2, height: 0.5, fill: '#16a34a', opacity: 0.9, ref: (el) => { spBarRefs.current[i] = el } }, 'spb' + i))
        }
        for (let i = 0; i < 64; i++) {
          els.push(jsx('rect', { x: 14 + i * BW, y: 434, width: BW - 2, height: 0, fill: '#cbd5e1', opacity: 0.6, ref: (el) => { spPkRefs.current[i] = el } }, 'spp' + i))
        }
        const fx = (f) => 14 + ((Math.log10(f) - Math.log10(24)) / (Math.log10(20000) - Math.log10(24))) * 972
        for (const [f, lbl] of [[100, '100'], [500, '500'], [1000, '1k'], [5000, '5k'], [10000, '10k']]) {
          els.push(jsx('line', { x1: fx(f), y1: 410, x2: fx(f), y2: 413, stroke: '#334155' }, 'spt' + f))
          els.push(jsx('text', { x: fx(f), y: 418, fontSize: 5, fill: '#475569', textAnchor: 'middle', children: lbl + 'Hz' }, 'sptl' + f))
        }
        let lastX = -99
        for (const n of noteList) {
          const f = 440 * Math.pow(2, (n - 69) / 12)
          if (f < 24 || f > 20000) continue
          const x = fx(f)
          if (x - lastX < 17) continue
          lastX = x
          els.push(jsx('path', { d: `M${x} 422 l-2.4 -3.6 h4.8 z`, fill: '#4ade80' }, 'spnm' + n))
          els.push(jsx('text', { x, y: 431, fontSize: 5.8, fill: '#e2e8f0', textAnchor: 'middle', fontWeight: 700, children: noteName(n) }, 'spnn' + n))
        }
        return jsxs('g', { children: els }, 'spectrum')
      })(),

      // headers row 1 + control row 1
      SectionTitle({ x: 165, y: 38, children: 'DIGITAL OSCILLATOR' }),
      SectionTitle({ x: 470, y: 38, children: 'ANALOG FILTER' }),
      SectionTitle({ x: 690, y: 38, children: 'CYCLING ENVELOPE' }),
      ...KNOBS.filter((k) => k.y === 78).map((k) => Knob({ k, state, onDrag })),

      // headers row 2 + control row 2
      SectionTitle({ x: 45, y: 279, children: 'OCTAVE' }),
      SectionTitle({ x: 497, y: 162, children: 'LFO' }),
      SectionTitle({ x: 720, y: 162, children: 'ENVELOPE' }),
      LedButton({ x: 43, y: 318, label: 'OCT −', w: 40, onClick: () => setAnchor((a) => { const b = Math.max(24, a - 12); anchorRef.current = b; return b }) }),
      LedButton({ x: 88, y: 318, label: 'OCT +', w: 40, onClick: () => setAnchor((a) => { const b = Math.min(76, a + 12); anchorRef.current = b; return b }) }),
      ...KNOBS.filter((k) => k.y === 192).map((k) => Knob({ k, state, onDrag })),

      // function band: HOLD (CC64) is the only real-MIDI button left in the band
      // (v0.2.7b owner mockup: SPICE off the face - CC2 stays drivable via register/chat).
      (() => { const on = (state.cc['64']?.value ?? 0) > 63
        return LedButton({ x: 48, y: 389, label: 'HOLD', w: 40, on, onClick: () => { api.rest('/cc', { method: 'POST', body: { cc: 64, value: on ? 0 : 127 } }).catch(() => {}); setStatus(on ? 'keyboard-hold off (CC64)' : 'keyboard-hold ON (CC64) - works in LIVE; panel echoes it back') } }) })(),

      // PANIC (v0.3.1, Class B): documented-message-only kill switch behind the
      // backend /panic contract. Offline it still cleans pane state (labeled).
      // Honest scope: the device's own arp/seq keeps running - status says so.
      LedButton({ x: 100, y: 389, w: 52, label: 'PANIC', on: panicFlash, color: '#e5484d', onClick: () => {
        setPanicFlash(true); setTimeout(() => setPanicFlash(false), 400)
        api.rest('/panic', { method: 'POST', body: {} }).then((r) => {
          localNotesRef.current.clear(); setLocalNotes(new Set()); pendingRef.current.delete(64)
          setStatus(r && r.ok ? `PANIC: ${r.released} released + hold off${r.sent ? '' : ' (offline - state only)'} · device arp/seq unaffected` : `panic send FAILED: ${(r && r.error) || 'no response'}${r && r.cleared ? ` - pane state cleaned anyway (${r.cleared} notes + hold released); device may hold a PARTIAL release` : ''}`)
        }).catch(() => setStatus('panic failed: backend unreachable'))
      } }),

      // SCALE LOCK (v0.3.3, Class B): pane-side note transform - zero new CC
      // surface: out-of-scale keys go DIM and DEAD while locked (v0.4.3 - was
      // snap-to-nearest; owner: a different key lighting up reads as broken).
      // Honest scope: the device's own panel quantize and the FL relay are
      // untouched by this lock - the pill says "pane" in the status line.
      // HOLD a pill (or right-click it) to clear instead of cycling back.
      LedButton({ x: 158, y: 389, w: 64, on: scaleIdx > 0, label: scaleIdx ? `${KEY_NAMES[keyIdx]} ${SCALES[scaleIdx][0]}` : 'SCALE OFF', onHold: () => { setScaleIdx(0); setStatus('scale lock cleared (hold) - every key plays as touched') }, onClick: () => { const nx = (scaleIdx + 1) % SCALES.length; setScaleIdx(nx); setStatus(nx ? `pane scale-lock: ${KEY_NAMES[keyIdx]} ${SCALES[nx][0]} - dimmed keys are locked out; hold the pill to clear (panel quantize + FL relay untouched)` : 'scale lock off - pane sends clicked notes as-is') } }),
      LedButton({ x: 228, y: 389, w: 40, on: scaleIdx > 0 && keyIdx !== 0, label: KEY_NAMES[keyIdx], onHold: () => { setKeyIdx(0); setStatus('root cleared to C (hold)') }, onClick: () => { const nk = (keyIdx + 1) % 12; setKeyIdx(nk); if (scaleIdx) setStatus(`pane scale-lock root: ${KEY_NAMES[nk]} ${SCALES[scaleIdx][0]}`) } }),

      jsxs('g', { transform: 'translate(14 440)', children: [jsx(Keybed, { notes, anchor, onDown: noteDown, onUp: noteUp, noteStats: state.note_stats, steps: SCALES[scaleIdx][1], root: keyIdx })] }),
      // owner's octave mirrors - back at their proven v0.2.6c geometry: 24-key
      // TILING windows under the main bed, no shared note, blacks after whites.
      MiniBed({ lo: anchor - 24, x: 0, y: 592, w: 462, notes, onDown: noteDown, onUp: noteUp, steps: SCALES[scaleIdx][1], root: keyIdx }),
      MiniBed({ lo: anchor + 25, x: 510, y: 592, w: 462, notes, onDown: noteDown, onUp: noteUp, steps: SCALES[scaleIdx][1], root: keyIdx }),
    ] }) }),

    // ---- v0.4 MOD LAB (pane-native lane, below the faithful panel face) ----
    jsx(ModLab, { state, setStatus, refresh, nameByCc, engine }),

    // ---- register + per-key / per-chord stats ----
    jsxs('div', { className: 'flex gap-6 flex-wrap', children: [
      jsxs('div', { className: 'flex flex-col gap-1', style: { minWidth: 360, flex: 1 }, children: [
        jsx('h2', { className: 'font-semibold text-sm', children: 'Input register (one line per action)' }),
        ...(state.events || []).slice(-12).reverse().map((e, i) => jsxs('div', { style: { fontSize: 11, display: 'flex', gap: 8, opacity: i === 0 ? 1 : 0.75 }, children: [
          jsx('span', { style: { width: 8, height: 8, borderRadius: 9, background: SRC_COLOR[srcKey(e.source)] || SRC_COLOR.unknown, marginTop: 3 } }),
          jsx('span', { style: { color: 'var(--ui-text-tertiary)', width: 46 }, children: new Date((e.ts || 0) * 1000).toLocaleTimeString() }),
          jsx('span', { style: { width: 150 }, children: e.kind === 'cc' ? `CC${e.cc} -> ${fmtVal(e.cc, e.value, e.source)}` : e.kind === 'note' ? `note ${noteName(e.note)} v${e.velocity ?? ''}` : e.kind === 'program' ? `pgm ${(e.program ?? 0) + 1}${e.bank != null ? ` bank ${e.bank}` : ''}` : e.kind === 'pressure' ? `pressure ${toPct(e.value)}%` : `${e.kind} ${e.name ?? ''}` }),
          jsx('span', { style: { color: 'var(--ui-text-secondary)' }, children: e.kind === 'cc' ? (nameByCc[e.cc] || '') : '' }),
          jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: e.source }),
        ] }, `${e.seq}-${i}`)),
        (!state.events || !state.events.length) && jsx('div', { style: { fontSize: 12, color: 'var(--ui-text-quaternary)' }, children: 'Nothing yet — flip Listen, turn a panel knob (MIDI Control mode), or send mf_cc from chat.' }),
        jsx('h2', { className: 'font-semibold text-sm', style: { marginTop: 8 }, children: 'Chords this session' }),
        jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4, fontSize: 11 }, children:
          chords.length ? chords.map((c, i) => jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', padding: '2px 6px', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5 }, children: [
            jsx('span', { children: `${chordLabel(c.pcs) || c.pcs.join(',')} [${c.pcs.map((p) => NAMES[p]).join('+')}]` }),
            jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: `x${c.count}` }),
          ] }, `ch${i}`)) : jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: 'play two or more keys together…' }) }),
      ] }),
      jsxs('div', { className: 'flex flex-col gap-1', style: { minWidth: 320 }, children: [
        jsx('h2', { className: 'font-semibold text-sm', children: 'Knob board (last value + provenance)' }),
        jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 4, fontSize: 11 }, children:
          CC_KNOBS.map((k) => {
            const st = state.cc[String(k.cc)]
            return jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', padding: '2px 6px', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5 }, children: [
              jsx('span', { style: { color: 'var(--ui-text-tertiary)' }, children: k.label }),
              jsx('span', { style: { color: st ? (SRC_COLOR[srcKey(st.source)] || 'inherit') : 'var(--ui-text-quaternary)' }, children: st ? fmtVal(k.cc, st.value, st.source) : '—' }),
            ] }, `kb-${k.cc}`)
          }) }),
        jsx('h2', { className: 'font-semibold text-sm', style: { marginTop: 8 }, children: 'Hottest keys (hover a key for its full stats)' }),
        ...hotKeys.map(([n, st]) => jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }, children: [
          jsx('span', { style: { width: 34, color: 'var(--ui-text-tertiary)' }, children: noteName(Number(n)) }),
          jsx('div', { style: { flex: 1, height: 6, background: 'var(--ui-stroke-secondary)', borderRadius: 3, opacity: 0.4 }, children: jsx('div', { style: { height: 6, width: `${(st.count / maxHot) * 100}%`, background: 'var(--ui-accent)', borderRadius: 3 } }) }),
          jsx('span', { style: { width: 30, textAlign: 'right' }, children: `x${st.count}` }),
          jsx('span', { style: { width: 56, textAlign: 'right', color: 'var(--ui-text-quaternary)' }, children: `v${Math.round(st.avg_vel)}` }),
        ] }, `hk${n}`)),
        !hotKeys.length && jsx('div', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: 'no key stats yet' }),
      ] }),
    ] }),
    jsx('div', { style: { fontSize: 10, color: 'var(--ui-text-quaternary)' }, children: `[${PANE_BUILD}${ENGINE_TABLE_OK ? '' : ' ENGINE-TABLE-STALE!'} · ${PANE_STAGE}] sendlog: ${state.sendlog || ''} · in: ${state.in_port || '—'} · out: ${state.out_port || '—'} · the panel never announces preset switches — ⟳ Read is how the mirror re-syncs${state.syx && state.syx.length ? ` · syx: ${state.syx.slice(-5).join(' · ')}` : ''}` }),
  ] })
}

// ---------------------------------------------------------------------------
// v0.4 MOD LAB: macro bank (8 nonlinear fan-out faders) + clip studio
// (record knob rides -> mfclip/1 -> play loop/pingpong/once, stretch, reverse).
// Class B law: every action is a human click -> route -> engine -> _engine_send.
// Faders/clips move the REAL panel knobs because the sends are the truth.
// ---------------------------------------------------------------------------
const CURVE_OPTS = ['lin', 's', 'exp', 'log', 'step4', 'snap12']
const RATE_OPTS = [0.25, 0.5, 1, 2, 4, 8]
const inputS = { fontSize: 11, padding: '2px 5px', borderRadius: 5, border: '1px solid var(--ui-stroke-secondary)', background: 'transparent', color: 'var(--ui-text-secondary)' }

function ModLab({ state, setStatus, refresh, nameByCc, engine }) {
  const [draft, setDraft] = useState(null)
  const [xs, setXs] = useState({})
  const [cfg, setCfg] = useState({})
  const [recName, setRecName] = useState('')
  const [loopName, setLoopName] = useState('')
  const [loopBpm, setLoopBpm] = useState('120')
  const [loopSpb, setLoopSpb] = useState(4)
  // v0.6 Chord Lab + F0a diagnostics. `staleApi` guards every new control: a pane
  // newer than the running backend must SAY so instead of firing routes that
  // do not exist yet (the v0.5.0a silent-404 lesson).
  const staleApi = (state.api || 0) < 4
  const [theory, setTheory] = useState(null)
  const [chordCfg, setChordCfg] = useState({ root: 0, quality: 'major', voicing: 'close', inversion: 0, octave: 4, bass_lock: false })
  const [diagCfg, setDiagCfg] = useState({ pattern: 'cc_sweep', duration_s: 4, rate_hz: 20, cc: 23, note: 60 })
  const chord = state.chord || {}
  const diag = state.diag || {}
  useEffect(() => {
    if (staleApi || theory) return
    api.rest('/chord')
      .then((r) => setTheory((r && r.ok && r.qualities) ? r : { qualities: [], voicings: [] }))
      .catch(() => setTheory({ qualities: [], voicings: [] }))
  }, [state.api])
  const qualities = (theory && theory.qualities) || []
  const voicings = (theory && theory.voicings) || []
  // Show the chord BEFORE sending it: the backend builds the voicing (the pane
  // never invents notes), so what you read is exactly what PLAY will send.
  const [preview, setPreview] = useState(null)
  useEffect(() => {
    if (staleApi) return
    let live = true
    api.rest('/theory/chord', { method: 'POST', body: chordCfg })
      .then((r) => { if (live) setPreview(r && r.ok ? r : { ok: false, error: (r && r.error) || 'refused' }) })
      .catch(() => { if (live) setPreview({ ok: false, error: 'backend unreachable' }) })
    return () => { live = false }
  }, [chordCfg.root, chordCfg.quality, chordCfg.voicing, chordCfg.inversion, chordCfg.octave, chordCfg.bass_lock])
  const macros = state.macros || []
  const clips = state.clips || []
  const loops = state.loops || []
  const lrec = state.loop_rec
  const playing = {}
  for (const p of (state.clips_playing || [])) playing[p.id] = p
  const lplaying = {}
  for (const p of (state.loops_playing || [])) lplaying[p.id] = p
  const ccOpts = Object.entries(state.params || {}).map(([n, p]) => [p.cc, n]).sort((a, b) => a[0] - b[0])
  const post = (path, body) => api.rest(path, { method: 'POST', body })
    .then((r) => {
      if (r && !r.ok) setStatus(`${path}: ${(r && r.errors && r.errors[0]) || (r && r.error) || 'refused'}`)
      refresh()
      return r
    })
    .catch(() => setStatus(`${path}: unreachable — pane newer than backend? fully QUIT + reopen the app`))
  const tset = (i, patch) => setDraft({ ...draft, targets: draft.targets.map((t, j) => (j === i ? { ...t, ...patch } : t)) })
  const editLoad = async (id) => {
    try {
      const r = await api.rest('/macros')
      const m = ((r && r.macros) || []).find((mm) => mm.id === id)
      if (m) setDraft(JSON.parse(JSON.stringify(m)))   // deep copy: edits must not touch live bank
      else setStatus('macro vanished before it could open for editing')
    } catch { setStatus('could not load macro for editing — backend too old?') }
  }

  return jsxs('div', { style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 10, padding: '10px 12px' }, children: [
    jsxs('div', { style: { display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }, children: [
      jsx('h2', { className: 'font-semibold text-sm', children: 'MOD LAB — macros · rides · loops' }),
      jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: `macros fan out over the 21 documented CCs · Clip studio rides = CC moves · Loop studio = NOTES you play, looped & exportable · engine now: ${engine || '—'}` }),
    ] }),
    // ---- macro bank ----
    jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 6, margin: '8px 0' }, children:
      macros.length ? macros.map((m) => jsxs('div', { style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 7, padding: '5px 8px', fontSize: 11 }, children: [
        jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center' }, children: [
          jsx('span', { style: { fontWeight: 600, flex: 1, color: 'var(--ui-text-secondary)' }, children: m.name || m.id }),
          jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: `${m.targets} target${m.targets === 1 ? '' : 's'}` }),
          jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, onClick: () => editLoad(m.id), children: '✎' }),
          jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, onClick: () => post('/macro/delete', { id: m.id }), children: '✕' }),
        ] }),
        jsx('input', { type: 'range', min: 0, max: 127, value: xs[m.id] ?? m.x ?? 0, style: { width: '100%', accentColor: '#f472b6' },
          onInput: (e) => setXs({ ...xs, [m.id]: +e.target.value }),
          onChange: (e) => { post('/macro', { id: m.id, x: +e.target.value }); } }),
      ] }, `mx-${m.id}`))
        : jsx('div', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: 'no macros yet — “+ macro”: one fader, many targets, curves per target' }),
    }),
    jsx('button', { type: 'button', style: btn(false, '#f472b6'), onClick: () => setDraft({ id: 'm' + Date.now().toString(36).slice(-4), name: '', targets: [{ cc: 23, base: 64, depth: 63, curve: 'lin' }] }), children: '+ macro' }),
    draft && jsxs('div', { style: { border: '1px dashed #f472b6', borderRadius: 7, padding: 8, margin: '6px 0', fontSize: 11 }, children: [
      jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }, children: [
        jsx('input', { style: { ...inputS, width: 150 }, placeholder: 'macro name', value: draft.name, onChange: (e) => setDraft({ ...draft, name: e.target.value }) }),
        jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: 'target = where the fader pushes: value = base + curve(x) × depth (± = inverse, s/exp/log shape it, step4/snap12 detent it)' }),
      ] }),
      ...draft.targets.map((t, i) => jsxs('div', { style: { display: 'flex', gap: 5, alignItems: 'center', margin: '3px 0', flexWrap: 'wrap' }, children: [
        jsx('select', { style: inputS, value: t.cc, onChange: (e) => tset(i, { cc: +e.target.value }), children:
          ccOpts.map(([cc, n]) => jsx('option', { value: cc, children: `CC${cc} ${n}` }, `cc-${cc}`)) }),
        jsx('label', { children: ['base ', jsx('input', { type: 'number', min: 0, max: 127, value: t.base, style: { ...inputS, width: 52 }, onChange: (e) => tset(i, { base: +e.target.value }) })] }),
        jsx('label', { children: ['depth ', jsx('input', { type: 'number', min: -127, max: 127, value: t.depth, style: { ...inputS, width: 56 }, onChange: (e) => tset(i, { depth: +e.target.value }) })] }),
        jsx('select', { style: inputS, value: t.curve || 'lin', onChange: (e) => tset(i, { curve: e.target.value }), children:
          CURVE_OPTS.map((c) => jsx('option', { value: c, children: c }, c)) }),
        draft.targets.length > 1 && jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, onClick: () => setDraft({ ...draft, targets: draft.targets.filter((_, j) => j !== i) }), children: '×' }),
      ] }, `tg-${i}`)),
      jsxs('div', { style: { display: 'flex', gap: 8, marginTop: 6 }, children: [
        draft.targets.length < 8 && jsx('button', { type: 'button', style: btn(false), onClick: () => setDraft({ ...draft, targets: [...draft.targets, { cc: 83, base: 64, depth: 40, curve: 'lin' }] }), children: '+ target' }),
        jsx('button', { type: 'button', style: btn(true, '#f472b6'), onClick: () => post('/macro/save', { macro: { ...draft, x: xs[draft.id] ?? draft.x ?? 0 } }).then((r) => r && r.ok && setDraft(null)) , children: 'save macro' }),
        jsx('button', { type: 'button', style: btn(false), onClick: () => setDraft(null), children: 'cancel' }),
      ] }),
    ] }),
    // ---- clip studio ----
    jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'center', margin: '10px 0 6px', flexWrap: 'wrap' }, children: [
      jsx('h2', { className: 'font-semibold text-sm', children: 'Clip studio' }),
      jsx('input', { style: { ...inputS, width: 120 }, placeholder: 'clip name (opt)', value: recName, onChange: (e) => setRecName(e.target.value) }),
      state.recording
        ? jsx('button', { type: 'button', style: btn(true, '#dc2626'), onClick: () => post('/clip/record', { action: 'stop' }).then((r) => { if (r && r.ok) { setRecName(''); setStatus(`clip saved: ${r.clip.name} · CCs ${r.clip.tracks.join(',')}`) } }), children: '⏹ stop & save — recording your moves…' })
        : jsx('button', { type: 'button', style: btn(false, '#dc2626'), onClick: () => post('/clip/record', { action: 'arm', name: recName }), children: '⏺ record a ride' }),
      jsx('span', { style: { fontSize: 10.5, color: 'var(--ui-text-quaternary)' }, children: 'while armed, every CC move counts: panel knobs (Listen + MIDI Control mode), pane knobs, macros, FL rides — nothing moved = discarded' }),
    ] }),
    jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 6 }, children:
      clips.length ? clips.map((c) => {
        const p = playing[c.id]
        const k = cfg[c.id] || {}
        return jsxs('div', { style: { border: `1px solid ${p ? '#fb923c' : 'var(--ui-stroke-secondary)'}`, borderRadius: 7, padding: '5px 8px', fontSize: 11 }, children: [
          jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center' }, children: [
            jsx('span', { style: { fontWeight: 600, flex: 1 }, children: c.name || c.id }),
            jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: `${(c.length_ms / 1000).toFixed(1)}s · ${(c.tracks || []).map((cc) => `CC${cc}`).join(' ')}` }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'export this ride as a .mid of CC automation for FL', onClick: () => post('/clip/export', { id: c.id }).then((r) => { if (r && r.ok) { setStatus(`exported ${r.file} · CC automation as a .mid`); revealLoops(setStatus, 'midis') } }), children: '⬇' }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, onClick: () => post('/clip/delete', { id: c.id }), children: '✕' }),
          ] }),
          p && jsx('div', { style: { height: 3, background: 'var(--ui-stroke-secondary)', borderRadius: 2, margin: '4px 0' }, children:
            jsx('div', { style: { height: 3, width: `${Math.min(100, (p.position_ms / c.length_ms) * 100)}%`, background: '#fb923c', borderRadius: 2 } }) }),
          jsxs('div', { style: { display: 'flex', gap: 5, alignItems: 'center', flexWrap: 'wrap', marginTop: 3 }, children: [
            jsx('select', { style: inputS, value: k.loop || 'loop', onChange: (e) => setCfg({ ...cfg, [c.id]: { ...k, loop: e.target.value } }), children:
              ['loop', 'pingpong', 'once'].map((m) => jsx('option', { value: m, children: m }, m)) }),
            jsx('select', { style: inputS, value: k.stretch || 1, onChange: (e) => setCfg({ ...cfg, [c.id]: { ...k, stretch: +e.target.value } }), children:
              RATE_OPTS.map((r) => jsx('option', { value: r, children: `×${r}` }, r)) }),
            jsx('label', { style: { color: 'var(--ui-text-quaternary)' }, children: ['rev ', jsx('input', { type: 'checkbox', checked: !!k.reverse, onChange: (e) => setCfg({ ...cfg, [c.id]: { ...k, reverse: e.target.checked } }) })] }),
            p
              ? jsx('button', { type: 'button', style: btn(true, '#fb923c'), onClick: () => post('/clip/stop', { id: c.id }), children: '⏹' })
              : jsx('button', { type: 'button', style: btn(false, '#fb923c'), onClick: () => post('/clip/play', { id: c.id, loop: k.loop || 'loop', stretch: k.stretch || 1, reverse: !!k.reverse }), children: '▶ play' }),
          ] }),
        ] }, `cl-${c.id}`)
      }) : jsx('div', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: 'no clips yet — ⏺ record, ride some knobs, ⏹ stop' }) }),
    // ---- LOOP STUDIO (v0.5): the NOTES lane — record what you play on the keys,
    // loop it, lengthen it (double/halve/set bars), overdub, export .mid for FL.
    jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'center', margin: '12px 0 6px', flexWrap: 'wrap' }, children: [
      jsx('h2', { className: 'font-semibold text-sm', children: 'Loop studio' }),
      jsx('input', { style: { ...inputS, width: 110 }, placeholder: 'loop name (opt)', value: loopName, onChange: (e) => setLoopName(e.target.value) }),
      jsx('label', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: ['bpm ', jsx('input', { style: { ...inputS, width: 48 }, value: loopBpm, onChange: (e) => setLoopBpm(e.target.value) })] }),
      jsx('select', { style: inputS, value: loopSpb, onChange: (e) => setLoopSpb(+e.target.value), children:
        [[4, '1 bar / pass'], [2, '2 beats / pass'], [6, '6 beats / pass'], [8, '2 bars / pass']].map(([v, lbl]) => jsx('option', { value: v, children: lbl }, `spb-${v}`)) }),
      lrec && lrec.overdub_into
        ? jsx('button', { type: 'button', style: btn(true, '#22c55e'), onClick: () => post('/loop/overdub', { action: 'stop' }).then((r) => { if (r && r.ok) { setLoopName(''); setStatus(`overdubbed ${r.added} note(s) into ${r.loop.id}`) } }), children: '⏹ merge overdub' })
        : lrec
          ? jsx('button', { type: 'button', style: btn(true, '#dc2626'), onClick: () => post('/loop/record', { action: 'stop' }).then((r) => { if (r && r.ok) { setLoopName(''); setStatus(`loop saved: ${r.loop.name} · ${r.loop.notes} notes · ${r.loop.bars} bar(s)`) } else if (r) setStatus((r.error || 'take discarded') + (state.listening ? '' : ' — and Listen was OFF, so panel keys were never arriving')) }), children: '⏹ stop & save — play your keys…' })
          : jsx('button', { type: 'button', style: btn(false, '#dc2626'), onClick: () => post('/loop/record', { action: 'arm', name: loopName, bpm: parseFloat(loopBpm) || 120, spb: loopSpb }).then((r) => { if (r && r.ok) setStatus(state.listening ? 'LOOP ARMED — play now, then ⏹ (pane keybed works with LIVE off)' : 'LOOP ARMED — but ⚠ LISTEN IS OFF: Freak keys will NOT be heard. Toggle Listen, or play the pane keybed below') }), children: '⏺ record loop' }),
      jsx('span', { style: { fontSize: 10.5, color: 'var(--ui-text-quaternary)' }, children: 'while armed, every note counts: MF keys (MIDI Control + Listen), pane keybed, FL — quantized to the grid, nothing played = discarded' }),
    ] }),
    jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 6 }, children:
      loops.length ? loops.map((l) => {
        const p = lplaying[l.id]
        const k = cfg[l.id] || {}
        return jsxs('div', { style: { border: `1px solid ${p ? '#22c55e' : 'var(--ui-stroke-secondary)'}`, borderRadius: 7, padding: '5px 8px', fontSize: 11 }, children: [
          jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center' }, children: [
            jsx('span', { style: { fontWeight: 600, flex: 1 }, children: l.name || l.id }),
            jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: `${l.notes}n · ${l.bars}bar${l.bars === 1 ? '' : 's'}` }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'export this loop as a .mid for FL Studio', onClick: () => post('/loop/export', { id: l.id }).then((r) => { if (r && r.ok) { setStatus(`exported ${r.file} · drop it onto an FL playlist track`); revealLoops(setStatus, 'midis') } }), children: '⬇' }),
            lrec && lrec.overdub_into === l.id
              ? null
              : jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'overdub: arm a second pass merged into this loop', onClick: () => post('/loop/overdub', { action: 'arm', id: l.id, spb: l.spb }), children: '✎' }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, onClick: () => post('/loop/delete', { id: l.id }), children: '✕' }),
          ].filter(Boolean) }),
          p && jsx('div', { style: { height: 3, background: 'var(--ui-stroke-secondary)', borderRadius: 2, margin: '4px 0' }, children:
            jsx('div', { style: { height: 3, width: `${Math.min(100, (p.position_ms / p.span_ms) * 100)}%`, background: '#22c55e', borderRadius: 2 } }) }),
          jsxs('div', { style: { display: 'flex', gap: 5, alignItems: 'center', flexWrap: 'wrap', marginTop: 3 }, children: [
            jsx('select', { style: inputS, value: k.loop || 'loop', onChange: (e) => setCfg({ ...cfg, [l.id]: { ...k, loop: e.target.value } }), title: 'replay mode', children:
              ['loop', 'once'].map((m) => jsx('option', { value: m, children: m === 'loop' ? '🔁 loop' : '▶ once' }, m)) }),
            p
              ? jsx('button', { type: 'button', style: btn(true, '#22c55e'), onClick: () => post('/loop/stop', { id: l.id }), children: '⏹' })
              : jsx('button', { type: 'button', style: btn(false, '#22c55e'), onClick: () => post('/loop/play', { id: l.id, loop: k.loop || 'loop', stretch: k.stretch || 1 }), children: '▶ play' }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'double the length (old pass plays twice)', onClick: () => post('/loop/double', { id: l.id }), children: '⏫' }),
            jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'halve the length', onClick: () => post('/loop/halve', { id: l.id }), children: '⏬' }),
            k.len !== undefined ? null : jsx('button', { type: 'button', style: { ...btn(false), padding: '0 6px' }, title: 'show exact length control', onClick: () => setCfg({ ...cfg, [l.id]: { ...k, len: l.bars } }), children: '⏱' }),
            k.len !== undefined && jsx('select', { style: inputS, value: k.len, onChange: (e) => { setCfg({ ...cfg, [l.id]: { ...k, len: +e.target.value } }); post('/loop/length', { id: l.id, bars: +e.target.value }) }, title: 'set loop length in bars', children:
              [1, 2, 3, 4, 6, 8].map((v) => jsx('option', { value: v, children: `${v}bar` }, `bars-${v}`)) }),
          ] }),
        ] }, `lp-${l.id}`)
      }) : jsx('div', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: 'no loops yet — ⏺ record loop, play your MicroFreak keys, ⏹ stop & save' }) }),
    // ---- CHORD LAB + F0a DIAGNOSTICS (v0.6): the owner's chord toggler, backed
    // by dashboard/theory.py (the ONE theory home - no JS chord math here).
    // Api 4 = the backend that has /theory /chord /stress; a stale backend says so.
    jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', margin: '14px 0 6px', flexWrap: 'wrap' }, children: [
      jsx('h2', { className: 'font-semibold text-sm', children: 'Chord Lab' }),
      jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: staleApi
        ? 'backend is older than this pane (no /theory /chord routes) — fully QUIT + reopen the app'
        : `theory: ${theory ? 'loaded ' + theory.qualities.length + ' qualities · ' + theory.voicings.length + ' voicings' : 'loading…'} · voicings are built backend-side; the pane only names them` }),
    ] }),
    jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', fontSize: 11 }, children: [
      jsx('select', { style: inputS, value: chordCfg.root, title: 'root', onChange: (e) => setChordCfg({ ...chordCfg, root: +e.target.value }), children:
        KEY_NAMES.map((n, i) => jsx('option', { value: i, children: n }, `cr-${i}`)) }),
      jsx('select', { style: inputS, value: chordCfg.quality, title: 'chord type', onChange: (e) => setChordCfg({ ...chordCfg, quality: e.target.value }), children:
        (qualities.length ? qualities : ['major', 'minor', 'maj7', 'm7', '7', 'sus4', 'dim', 'aug', 'add9', '9'])
          .map((q) => jsx('option', { value: q, children: q }, `cq-${q}`)) }),
      jsx('select', { style: inputS, value: chordCfg.voicing, title: 'voicing style', onChange: (e) => setChordCfg({ ...chordCfg, voicing: e.target.value }), children:
        (voicings.length ? voicings : ['close', 'open', 'drop2', 'spread', 'octave', 'bass'])
          .map((v) => jsx('option', { value: v, children: v }, `cv-${v}`)) }),
      jsx('label', { children: ['inv ', jsx('input', { type: 'number', min: 0, max: 4, value: chordCfg.inversion, style: { ...inputS, width: 46 }, onChange: (e) => setChordCfg({ ...chordCfg, inversion: +e.target.value }) })] }),
      jsx('label', { children: ['oct ', jsx('input', { type: 'number', min: 1, max: 7, value: chordCfg.octave, style: { ...inputS, width: 46 }, onChange: (e) => setChordCfg({ ...chordCfg, octave: +e.target.value }) })] }),
      jsx('label', { style: { color: 'var(--ui-text-quaternary)' }, children: ['bass-lock ', jsx('input', { type: 'checkbox', checked: !!chordCfg.bass_lock, onChange: (e) => setChordCfg({ ...chordCfg, bass_lock: e.target.checked }) })] }),
      jsx('button', { type: 'button', style: btn(false, '#a78bfa'), disabled: staleApi, onClick: () => post('/chord/play', { ...chordCfg, action: 'hold' }).then((r) => {
        if (r && r.ok) setStatus(`chord ${r.label} [${r.notes.join(' ')}] · ${r.sent.length ? 'on the device' : 'virtual (LIVE off)'}${r.paraphony ? ' · ' + r.paraphony : ''}`)
      }), children: 'play & hold' }),
      jsx('button', { type: 'button', style: btn(false, '#a78bfa'), disabled: staleApi, onClick: () => post('/chord/play', { ...chordCfg, action: 'moment', duration_ms: 1200 }).then((r) => {
        if (r && r.ok) setStatus(`chord ${r.label} [${r.notes.join(' ')}] · 1.2 s then lifts itself`)
      }), children: 'strum 1.2s' }),
      jsx('button', { type: 'button', style: btn(false, '#dc2626'), disabled: staleApi, onClick: () => post('/chord/release', {}).then((r) => r && setStatus(`chord released (${r.released} note-off${r.released === 1 ? '' : 's'})`)), children: '⏹ release' }),
      jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: 'the Freak is 4-voice paraphonic: a bigger chord still sends, but the lowest 4 own the envelopes' }),
    ] }),
    jsx('div', { style: { fontSize: 11, margin: '4px 0', color: preview && preview.ok === false ? '#e5484d' : 'var(--ui-text-secondary)' }, children:
      staleApi ? 'preview off (stale backend)'
        : preview && preview.ok === false ? `preview refused: ${preview.error}`
        : preview && preview.notes ? `preview: ${preview.label} \u00b7 ${preview.names.join(' ')} \u00b7 ${preview.notes.length} voice${preview.notes.length === 1 ? '' : 's'}${preview.paraphony ? ' \u26a0 ' + preview.paraphony : ''}`
        : 'preview: …' }),
    chord && chord.held && chord.held.length
      ? jsx('div', { style: { fontSize: 11, color: '#a78bfa', margin: '4px 0' }, children: `HELD: ${chord.last && chord.last.label ? chord.last.label + ' · ' : ''}${chord.held.join(' ')} — RELEASE or PANIC lifts it` })
      : jsx('div', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)', margin: '4px 0' }, children: chord && chord.count ? `nothing held (${chord.count} audition${chord.count === 1 ? '' : 's'} this session)` : 'nothing held — pick a chord and play it' }),
    // ---- F0a diagnostics: evidence, mock-safe by default, always stoppable
    jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', margin: '12px 0 6px', flexWrap: 'wrap' }, children: [
      jsx('h2', { className: 'font-semibold text-sm', children: 'Diagnostics (F0a)' }),
      jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-quaternary)' }, children: staleApi
        ? 'backend older than this pane (no /stress routes) — fully QUIT + reopen'
        : `evidence tool for rate/dedupe/stuck-note/panic-latency · ${state.live ? 'LIVE ARMED: a run really sends' : 'LIVE off: runs are VIRTUAL (nothing reaches the device)'} · sendlog rows from this harness: ${diag.sendlog_rows ?? '—'}` }),
    ] }),
    staleApi ? null : jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', fontSize: 11 }, children: [
      jsx('select', { style: inputS, value: diagCfg.pattern, onChange: (e) => setDiagCfg({ ...diagCfg, pattern: e.target.value }), children:
        ['cc_sweep', 'cc_storm', 'note_burst', 'mixed'].map((p) => jsx('option', { value: p, children: p }, `dp-${p}`)) }),
      jsx('label', { children: ['sec ', jsx('input', { type: 'number', min: 0.1, max: 60, value: diagCfg.duration_s, style: { ...inputS, width: 54 }, onChange: (e) => setDiagCfg({ ...diagCfg, duration_s: +e.target.value }) })] }),
      jsx('label', { children: ['Hz ', jsx('input', { type: 'number', min: 0.5, max: 200, value: diagCfg.rate_hz, style: { ...inputS, width: 54 }, onChange: (e) => setDiagCfg({ ...diagCfg, rate_hz: +e.target.value }) })] }),
      jsx('label', { children: ['CC ', jsx('input', { type: 'number', min: 0, max: 127, value: diagCfg.cc, style: { ...inputS, width: 54 }, onChange: (e) => setDiagCfg({ ...diagCfg, cc: +e.target.value }) })] }),
      diag.running
        ? jsx('button', { type: 'button', style: btn(true, '#dc2626'), onClick: () => post('/stress/stop', { why: 'user' }).then((r) => r && r.ok && setStatus(`stress run stopped · ${r.attempted} msgs · ${r.msg_per_s}/s · sent ${r.sent} / virtual ${r.virtual}${r.stuck_note_check === false ? ' · ⚠ UNBALANCED notes' : ''}`)), children: '⏹ STOP (running…)' })
        : jsx('button', { type: 'button', style: btn(false, '#38bdf8'), onClick: () => post('/stress/start', diagCfg).then((r) => r && r.ok && setStatus(`stress ${r.run.pattern} running — ${r.note || ''}`)), children: '▶ run' }),
      jsx('button', { type: 'button', style: btn(false, '#dc2626'), onClick: () => post('/panic', {}).then((r) => r && setStatus(`PANIC gen ${r.gen} · chords ${r.chords_released} · diagnostic ${r.stress_stopped ? 'stopped' : 'idle'}`)), children: 'PANIC' }),
      jsx('button', { type: 'button', style: btn(false), onClick: () => api.rest('/stress/export').then((r) => r && r.ok && setStatus(`exported ${r.paths.csv}`)), children: '\u2913 export mfstress/1' }),
      jsx('button', { type: 'button', style: btn(false), onClick: () => api.rest('/diag').then((r) => r && setStatus(`harness: ${r.counters.attempted} attempted \u00b7 ${r.history.length} run(s) recorded \u00b7 limits ${r.limits.max_duration_s}s / ${r.limits.max_messages} msgs \u00b7 patterns ${r.patterns.join('/')}`)), children: '\u24d8 readout' }),
      jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: `rate now ${diag.rate_hz_now || 0}/s · attempted ${diag.counters ? diag.counters.attempted : 0} · sent ${diag.counters ? diag.counters.sent : 0} · virtual ${diag.counters ? diag.counters.virtual : 0}` }),
    ] }),
    // storage reveal: every save lands in %LOCALAPPDATA%\mf-visualizer; one button finds it
    jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 10.5, color: 'var(--ui-text-quaternary)' }, children: [
      jsx('button', { type: 'button', style: btn(false), onClick: () => revealLoops(setStatus), children: '📁 open storage folder' }),
      jsx('span', { children: 'clips.json · loops.json · exports/*.mid live in %LOCALAPPDATA%\mf-visualizer — FL can import the .mid files' }),
    ] }),
  ] })
}

function revealLoops(setStatus, where) {
  // v0.5.0a: /reveal is the REAL route (a /storage/reveal typo made this a silent 404);
  // and it LOUDLY reports - a reveal that can't speak is a reveal that didn't happen.
  api.rest('/reveal', { method: 'POST', body: { where: where || 'data' } })
    .then((r) => {
      if (r && r.ok) { if (setStatus) setStatus(`opened ${r.path}`) }
      else if (setStatus) setStatus(`reveal failed: ${(r && r.error) || 'backend said no'} - files live in ${window.__mfvisStorage || '%LOCALAPPDATA%\mf-visualizer'}`)
    })
    .catch(() => { if (setStatus) setStatus('reveal: backend unreachable (no /reveal route = stale backend - full QUIT + reopen)') })
}

function btn(active, accent) {
  return {
    border: `1px solid ${active ? (accent || 'var(--ui-accent)') : 'var(--ui-stroke-secondary)'}`,
    borderRadius: 6, padding: '6px 10px', background: 'transparent',
    color: active ? (accent || 'var(--ui-accent)') : 'var(--ui-text-secondary)', cursor: 'pointer', fontSize: 12,
  }
}

export default {
  id: 'mf-visualizer',
  name: 'MicroFreak',
  register(ctx) {
    api = ctx
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/mf-visualizer' }, render: () => jsx(Visualizer, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/mf-visualizer', label: 'MicroFreak', codicon: 'dashboard' } },
      { id: 'open', area: PALETTE_AREA, data: { id: 'mf-visualizer.open', label: 'Open MicroFreak visualizer', run: () => host.navigate('/mf-visualizer') } },
    ])
  },
}
