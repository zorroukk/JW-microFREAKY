// Smoke harness v2 for mf-visualizer plugin.js: fake react hooks + WS push.
// Proves: engine banding (floor, not round), 0-100% display, real 25-key
// keybed (E and A# exist!), WS delta merge, octave auto-follow.
import { copyFileSync, mkdirSync, writeFileSync, rmSync, readFileSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'

// Subject: the CANONICAL repo source (the running deploy is a target, not the
// source). Run: node tools/pane_smoke.mjs   (env MF_VIS_DEPLOY overrides the
// deployed-copy location when you have one).
const REPO = fileURLToPath(new URL('..', import.meta.url)).replace(/[\\/]+$/, '')
const DEPL = process.env.MF_VIS_DEPLOY || (REPO + '/desktop')
const PANE_STAGE_NEEDLE = 'F2+F2.5+F0a offline'
const DEPLOY_NOTE = 'deploy target (may be older than the source by design)'
const ROOT = tmpdir() + '/mfvis_run'
rmSync(ROOT, { recursive: true, force: true })
mkdirSync(ROOT + '/node_modules/@hermes/plugin-sdk', { recursive: true })
mkdirSync(ROOT + '/node_modules/react', { recursive: true })

writeFileSync(ROOT + '/node_modules/@hermes/plugin-sdk/package.json',
  JSON.stringify({ name: '@hermes/plugin-sdk', type: 'module', exports: { '.': './index.mjs' } }))
writeFileSync(ROOT + '/node_modules/@hermes/plugin-sdk/index.mjs',
  `export const PALETTE_AREA='palette';export const ROUTES_AREA='routes';export const SIDEBAR_NAV_AREA='nav';export const host={navigate:()=>{}}\n`)

writeFileSync(ROOT + '/node_modules/react/package.json',
  JSON.stringify({ name: 'react', type: 'module', exports: { '.': './index.mjs', './jsx-runtime': './index.mjs' } }))
writeFileSync(ROOT + '/node_modules/react/index.mjs', `
export const hookState = [];
let idx = 0; export const flags = { dirty: false };
const timers = [];
export function collect(){ idx = 0 }
export function useState(init){ const i = idx++; if (!hookState[i]) hookState[i] = { v: typeof init === 'function' ? init() : init }; const s = hookState[i]; return [s.v, (x) => { s.v = typeof x === 'function' ? x(s.v) : x; flags.dirty = true }] }
export function useRef(init){ const i = idx++; if (!hookState[i]) hookState[i] = { current: init }; return hookState[i] }
export function useMemo(fn){ return fn() }
export function useCallback(fn){ return fn }
export const effectSeen = new Set();
export function useEffect(fn){ if (!effectSeen.has(idx)) { effectSeen.add(idx); Promise.resolve().then(() => { const d = fn(); if (typeof d === 'function') timers.push(d) }) } idx++ }
export function flushDisposers(){ for (const t of timers) try { t() } catch {} }
export const jsx = (type, props, key) => ({ type, props: key !== undefined ? { ...props, key } : props })
export const jsxs = (type, props, key) => ({ type, props: key !== undefined ? { ...props, key } : props })
`)
copyFileSync(REPO + '/desktop/plugin.js', ROOT + '/plugin.mjs')   // CANONICAL repo source (v0.4.0a lesson)

const React = await import(pathToFileURL(ROOT + '/node_modules/react/index.mjs').href)

globalThis.window = { addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, hidden: false }
// v0.4.2 fast analyzer: rAF paint loop needs these stubs (effects DO run)
globalThis.performance = { now: () => Date.now() }
const rafCbs = []
globalThis.requestAnimationFrame = (cb) => { rafCbs.push(cb); return rafCbs.length }
globalThis.cancelAnimationFrame = () => {}
const createdIntervals = []
const realSetInterval = globalThis.setInterval
globalThis.setInterval = (fn, ms) => { const h = realSetInterval(fn, ms); createdIntervals.push(h); return h }

// ---- canned backend ----
const now = Date.now() / 1000
const seed = {
  seq: 1, mock: false, api: 4, syx: ['in 52 35B', 'in 15 1B'],
  cc: { '23': { value: 67, source: 'panel', ts: now, name: 'cutoff' }, '9': { value: 0, source: 'preset', ts: now, name: 'oscillator_type' } },
  events: [{ seq: 1, kind: 'cc', cc: 23, value: 67, source: 'panel', ts: now }],
  params: { cutoff: { cc: 23 }, oscillator_type: { cc: 9 } },
  active_notes: [48, 49], program: 12, listening: true, live: false,
  matrix: { amounts: [[0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0]] },
  preset: { name: '2 Faces', fields: 106, matrix: null }, sendlog: 'x',
  spectrum: { src: 'loopback:Test Capture', model: false, hz: 48000, bands: Array.from({ length: 64 }, (_, i) => Math.max(0, 0.9 - Math.abs(i - 18) / 24)) }, in_port: 'Arturia MicroFreak 3', out_port: 'Arturia MicroFreak 4',
}
let pushDelta = null
const ctx = {
  rest: async (path) => (path === '/state' ? JSON.parse(JSON.stringify(seed)) : path === '/spectrum' ? JSON.parse(JSON.stringify(seed.spectrum)) : { ok: true }),
  socket: (path, onMsg) => { pushDelta = (m) => { for (const [k, v] of Object.entries(m)) if (k !== 'type') seed[k] = v; onMsg(m) }; return () => { pushDelta = null } },
  registerMany: (cs) => { ctx.contribs.push(...cs) },
  contribs: [],
}

const mod = await import(pathToFileURL(ROOT + '/plugin.mjs').href)
mod.default.register(ctx)
const page = ctx.contribs.find((c) => c.id === 'page')

function renderTree() {
  const el = page.render()
  React.collect()
  return typeof el.type === 'function' ? el.type(el.props) : el
}
function walk(node, visit) {
  if (node == null || typeof node === 'boolean') return
  if (typeof node === 'string' || typeof node === 'number') { visit(String(node)); return }
  if (Array.isArray(node)) { for (const c of node) walk(c, visit); return }
  if (node.type) {
    if (typeof node.type === 'function') { walk(node.type(node.props), visit); return } // stateless sub-components (Keybed)
    walk(node.props?.children, visit); return
  }
  visit(node)
}
function texts(node, acc = []) { walk(node, (t) => acc.push(t)); return acc }
function keyTags(node, acc = []) {
  const rec = (n) => {
    if (n == null || typeof n !== 'object') return
    if (Array.isArray(n)) { n.forEach(rec); return }
    if (n.type) { if (typeof n.type === 'function') { rec(n.type(n.props)); return } if (n.props?.key !== undefined) acc.push(String(n.props.key)); rec(n.props?.children); return }
    if (n.children !== undefined) rec(n.children)
  }
  rec(node)
  return acc
}

let tree = renderTree()
await new Promise(r => setTimeout(r, 60))       // let effects + async refresh resolve
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
let all = texts(tree).join('\n')
const tags = keyTags(tree)

const fails = []
const ok = (cond, label) => { if (!cond) fails.push(label) }

ok(all.includes('BasicWaves'), 'cc9=0 must render BasicWaves (round() bug regression)')
ok(all.includes('PANEL 805Hz'), 'cutoff cc67 -> 805Hz on the 16Hz-26.9kHz exp law (v0.2.6)')
ok(tags.filter(t => t.startsWith('w')).length === 15, `15 white keys, got ${tags.filter(t => t.startsWith('w')).length}`)
ok(tags.filter(t => t.startsWith('b')).length === 10, `10 black keys, got ${tags.filter(t => t.startsWith('b')).length}`)
ok(tags.includes('w52'), 'E4 (was skipped by old KEYS generator)')
ok(tags.includes('b58'), 'A#4 (was skipped by old KEYS generator)')
ok(tags.includes('w72'), 'top C5 present')
ok(tags.filter(t => t.startsWith('mw')).length === 28 && tags.filter(t => t.startsWith('mb')).length === 20, 'v0.2.7e: owner octave mirrors RESTORED under the bed - two 24-key tiling windows (14w/10b each)')
ok(!['48', '72'].some((n) => tags.includes('mw' + n) || tags.includes('mb' + n)) && tags.filter(t => t === 'w48').length === 1, 'tiling: boundary notes (anchor, anchor+24) belong ONLY to the main bed - no dual-ownership latch fight (v0.2.6c)')
ok(tags.indexOf('mw47') < tags.indexOf('mb25') && tags.indexOf('mw96') < tags.indexOf('mb73') && tags.indexOf('w60') < tags.indexOf('b61'), 'all beds: whites painted before blacks - hit area matches visual (owner fix v0.2.6)')
ok(!all.includes('mirror follows the device window') && !all.includes('oct −') && !all.includes('oct +'), 'no mini-bed captions anywhere (v0.2.7c)')
ok(!all.includes('SPICE'), 'v0.2.7b: SPICE excised from the face (CC2 still drivable off-panel)')
ok((()=>{const j=JSON.stringify(tree);return j.includes('translate(185 78)') && j.includes('translate(84 192)') && j.includes('"x":709') && j.includes('"y":246') && j.includes('"height":162') && j.includes('translate(14 440)') && j.includes(' 592)') && j.includes('translate(140 356)') && j.includes('translate(48 389)')})(), 'v0.3.0 geometry: row1 78 (TYPE 185/78), RATE 192, box 246, HUD 356, HOLD 389, keybed 440 + minis 592, analyzer strip 409-439')
ok(all.includes('C3') && all.includes('C5'), 'octave labels C3..C5')
ok(all.includes('keys C3'), 'OLED shows the window')

{
  const paneSrcNow = readFileSync(REPO + '/desktop/plugin.js', 'utf8')
  const downCount = (paneSrcNow.match(/onPointerDown: \(e\) => onDown/g) || []).length
  ok(downCount >= 4, `v0.4.1 press-to-speak: ${downCount} key pointer-DOWN sites (owner: keys used to sound only on RELEASE)`)
  ok(!/onKey: playNote|onClick: \(\) => onKey/.test(paneSrcNow), 'no click-to-sound key bindings remain')
}


// WS delta: SuperWave + a note above the window = auto re-anchor
React.flags.dirty = false
pushDelta({ type: 'delta', seq: 2, cc: { '9': { value: 6, source: 'panel', ts: now }, '23': { value: 127, source: 'panel', ts: now } }, events: [{ seq: 2, kind: 'note', note: 84, velocity: 90, source: 'panel', ts: now }, { seq: 2, kind: 'cc', cc: 9, value: 11, source: 'panel', ts: now }], active_notes: [84], program: 12, live: false, listening: true, listen_error: null, preset: seed.preset })
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
ok(all.includes('SuperWave'), 'panel cc9=11 (device transmit for SuperWave) must show SuperWave')
ok(all.includes('PANEL 26.9kHz'), 'cutoff cc127 must display exactly 26.9kHz (owner endpoint)')
ok(/keys C4/.test(all) || /keys C5/.test(all), `re-anchor on note 84, got ${all.match(/keys [^\n]*/)?.[0]}`)

// -- engine max
pushDelta({ type: 'delta', seq: 3, cc: { '9': { value: 127, source: 'panel', ts: now } }, events: [], active_notes: [84], program: 12, live: false, listening: true, preset: null })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
ok(all.includes('Hit Grains'), 'cc9=127 must be Hit Grains (22nd)')

// -- v0.2.7a: matrix mimic EXCISED (fake write path); real headers kept
ok(!['CycEnv', 'Key/Arp'].some((s) => all.includes(s)) && all.includes('DIGITAL OSCILLATOR') && all.includes('CYCLING ENVELOPE') && !all.includes('KEYBOARD'), 'matrix source labels excised, real headers survive')
ok(!all.includes('PRESSURE'), 'v0.2.7c: pressure bar removed by owner (it never echoed a device path); P-box keeps lowercase press % line')
ok(all.includes('⟳ Read preset'), 'Read preset button')
// -- v0.3.1 PANIC kill switch (Class B: documented messages only)
ok(all.includes('PANIC'), 'PANIC pill rendered in function band')
const paneSrc = readFileSync(REPO + '/desktop/plugin.js', 'utf8')
ok(paneSrc.includes("'/panic'") && paneSrc.includes('#e5484d') && paneSrc.includes('panicFlash'),
   'PANIC wired: POST /panic, danger red, momentary flash state')
ok(/PANE_BUILD = 'v0\.5\.0a'/.test(paneSrc), 'footer stamp stays v0.5.0a - the at-device arbiter (debt cap)')
ok(/PANE_STAGE = '/.test(paneSrc) && paneSrc.includes(PANE_STAGE_NEEDLE), 'v0.6 stage marker in the footer (undeployed work says so)')
ok(paneSrc.includes('staleApi') && paneSrc.includes("< 4"), 'api-4 guard present: a stale backend must be refused, not guessed at')
const apiSrc = readFileSync(REPO + '/dashboard/plugin_api.py', 'utf8')
for (const rt of ['/reveal', '/loop/record', '/loop/play', '/loop/stop', '/loop/double', '/loop/halve', '/loop/length', '/loop/overdub', '/loop/delete', '/loop/export', '/clip/export'])
  ok(paneSrc.includes(`'${rt}'`) && (apiSrc.includes(`@router.post("${rt}")`) || apiSrc.includes(`@router.get("${rt}")`)), `v0.5 PAIR: ${rt} called by pane AND registered by backend`)
ok(paneSrc.includes("analyzer: ") && paneSrc.includes("r.src || '?'"), 'v0.4.4 analyzer-source honesty in status line')
ok(!tags.some(t => t.startsWith('dh') || t.startsWith('m0-') || t.startsWith('rl')), 'matrix cells/headers/row-labels all gone')

// -- chord label: C-E-G from the device must read "now: C4 E4 G4 (C maj)"
pushDelta({ type: 'delta', seq: 4, cc: {}, events: [
  { seq: 4, kind: 'note', note: 60, velocity: 88, source: 'panel', ts: now },
  { seq: 5, kind: 'note', note: 64, velocity: 80, source: 'panel', ts: now },
  { seq: 6, kind: 'note', note: 67, velocity: 76, source: 'panel', ts: now },
], active_notes: [60, 64, 67], program: 12, live: false, listening: true, preset: null,
   note_stats: { '60': { count: 7, avg_vel: 81, max_vel: 96, total_ms: 2100, last_with: [64, 67] } },
   chords: [{ count: 4, pcs: [0, 4, 7], last_ts: now, top_note: 67 }] })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
// two-stage spin: first render registers the 150ms pending chord, the second
// (after the pane's own 170ms ping) sees the committed plate + progression.
const settle = async (d) => { pushDelta(d)
  const spin = async () => { React.flags.dirty = true
    for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise((r2) => setTimeout(r2, 10)); tree = renderTree() } }
  await new Promise((r2) => setTimeout(r2, 40)); await spin()
  await new Promise((r2) => setTimeout(r2, 220)); await spin()
  const txt = texts(tree).join('\n')
  ok(txt.includes('MOD LAB'), 'v0.4 MOD LAB drawer renders');
  ok(txt.includes('record a ride') || txt.includes('stop & save'), 'v0.4 clip studio transport present');
  ok(txt.includes('+ macro') || txt.includes('no macros yet'), 'v0.4 macro bank present')
  if (process.env.MFDEBUG) console.log('DBG>', txt.split('\n').filter((l) => /\u266a|held|analyzer|\u2192/.test(l)).join(' || '), '| spbars:', JSON.stringify(tree).includes('spb30'))
  return txt }
let cd = await settle({ type: 'delta', seq: 40, cc: {}, events: [], active_notes: [60, 64, 67], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('\u266a C'), 'C-E-G plate reads bare C (major implied) after the 150ms settle')
ok(cd.includes('held') && cd.includes('C4 E4 G4'), 'plate shows the actual notes + hold timer')
// -- deep engine battery: half-dim, inversion, six-add-nine, cluster --
cd = await settle({ type: 'delta', seq: 41, cc: {}, events: [], active_notes: [60, 63, 66, 70], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('Cm7\u266d5'), 'C-Eb-Gb-Bb names as Cm7b5 (half-diminished)')
ok(!cd.includes('Cm7\u266d5 (symmetric)'), 'half-dim is NOT flagged symmetric (rotation must reproduce the exact interval set)')
cd = await settle({ type: 'delta', seq: 42, cc: {}, events: [], active_notes: [64, 67, 72, 76], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('C / E\u00b71st inv'), 'C major with E in the bass reports its inversion')
cd = await settle({ type: 'delta', seq: 43, cc: {}, events: [], active_notes: [60, 62, 64, 67, 69], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('C6add9'), 'C-D-E-G-A names as C6add9, not a pc dump')
cd = await settle({ type: 'delta', seq: 44, cc: {}, events: [], active_notes: [60, 61, 62, 63], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('tone cluster'), 'chromatic pile reports honestly as a tone cluster')
cd = await settle({ type: 'delta', seq: 45, cc: {}, events: [], active_notes: [60, 64, 67], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('\u266a C') && cd.includes('Cm7\u266d5 \u2192'), 'progression history keeps the committed chords')
// -- spectrum strip renders from state.spectrum --
ok(cd.includes('analyzer: loopback') && JSON.stringify(tree).includes('spb30'), 'analyzer draws 64 log bands from state.spectrum')

// ---- v0.4.2 FAST ANALYZER pins (owner: spectrum laggy -> snappy + direct) ----
ok(/api\.rest\('\/spectrum'\)/.test(paneSrc), 'fast analyzer polls tiny GET /spectrum (~16 Hz) instead of riding /state')
ok(!/sm\.vals\[i\] \* 0\.72/.test(paneSrc) && !/setInterval\(refresh, 5000\)/.test(paneSrc), 'per-render x0.72 smoothing + redundant 5s full-poll are GONE from source')
ok(/FALL_PER_S = 1 \/ 0\.2/.test(paneSrc) && /sm\.vals\[i\] - FALL_PER_S \* dt/.test(paneSrc),
   'v0.5.1a LINEAR fall in the display domain (matches backend SPEC_RELEASE_S=0.2)')
ok(!/Math\.exp\(-dt \/ 0\.15\)/.test(paneSrc), 'the old exponential fall (slow dB-space tail) is GONE from source')
ok(/PK_FALL_PER_S = 1 \/ 0\.3/.test(paneSrc) && /sm\.peaks\[i\] - PK_FALL_PER_S \* dt/.test(paneSrc),
   'peaks fall on their own linear rate, cleared by 0.3 s (no tick outlives the bar law)')
ok(paneSrc.indexOf("'spb' + i") < paneSrc.indexOf("'spp' + i"), 'analyzer bars pushed BEFORE peak lines (SVG paint-order law)')
ok(/spBarRefs\.current\[i\] = el/.test(paneSrc) && /spPkRefs\.current\[i\] = el/.test(paneSrc), 'rAF owns bar/peak geometry via refs - no band value through the reconciler')
{
  let frames = 0
  for (let n = 0; n < 4 && rafCbs.length; n++) {
    const cbs = rafCbs.splice(0)
    for (const cb of cbs) cb(Date.now() + 16 * ++frames)
  }
  ok(frames >= 4, `rAF paint loop ran ${frames} frames without throwing (empty refs = guard path proven)`)
}
ok(cd.includes('10k'), 'log grid shows the 10kHz marker')
ok(JSON.stringify(tree).includes('analyzer: loopback:Test Capture'), 'HUD names the real capture source honestly')
ok(all.includes('x7') && all.includes('x4'), 'per-key stats (x7) and chord count (x4) rendered')

// -- assigns from a read reach the matrix column headers
pushDelta({ type: 'delta', seq: 7, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true,
   preset: { name: 'Test', slot: 47, matrix: [[0,0,64,0,0,0,0],[0,0,0,0,0,0,0],[0,0,0,0,0,0,0],[0,0,0,0,0,0,0],[0,0,0,0,0,0,0]], assigns: ['VCO.Param3', 'LFO.Rate', ''] } })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
ok(all.includes('P47'), 'OLED shows the read slot number')
ok(!all.includes('note-off'), 'feed has no release lines')

// -- v0.4.3 SCALE LOCK pills render + BLOCK-not-snap law (see mfvis_scale_test.mjs)
ok(all.includes('SCALE OFF'), 'scale pill rendered (OFF default)')
ok(all.includes('C') && all.includes('PANIC'), 'key pill + panic coexist in function band')
ok(!all.includes('MAJOR'), 'scale locked off: no scale name on the face')

// ---- v0.4.3 SCALE UX rework pins (owner: wrong key lights / missing scales / cycle-to-reset) ----
{
  const scSeg = paneSrc.slice(paneSrc.indexOf('const SCALES = ['), paneSrc.indexOf('const inScale'))
  ok((scSeg.match(/\['[A-Z][A-Z ]*', ?\[/g) || []).length === 18, 'SCALES = OFF + 17 (locrian/harm-min/mel-min/hung-min/lyd-dom/spanish/hiraj/whole added)')
}
ok(!/quantize\(/.test(paneSrc), 'nearest-tone snap helper GONE from the pane (owner: "lights up a completely different one" = broken)')
ok(/inScale\(n0, SCALES\[scaleIdx\]\[1\], keyIdx\)/.test(paneSrc), 'press-time gate at the send choke point (playNote + noteDown)')
ok((paneSrc.match(/const n = n0/g) || []).length === 3, 'playNote+noteDown+noteUp: after the gate the touched note is the sent note - unchanged')
ok(/ungated by law/.test(paneSrc), 'noteUp release is NEVER gated (mid-press scale switch cannot strand a note-off)')
ok(/longHoldFiredAt/.test(paneSrc), 'hold-to-clear plumbing (module-level: re-renders must not reset a mid-hold flag)')
ok((paneSrc.match(/onHold: \(\) => \{ set(ScaleIdx|KeyIdx)\(0\)/g) || []).length === 2, 'BOTH scale and root pills clear on 550 ms hold or right-click')
ok(/out of \$\{KEY_NAMES\[keyIdx\]\} \$\{SCALES\[scaleIdx\]\[0\]\}/.test(paneSrc), 'a locked press names the note AND the scale in status text')
ok(/dead\(n\) \? 0\.12 : 0\.35/.test(paneSrc) && /dead\(n\) \? 0\.1 : 0\.28/.test(paneSrc), 'out-of-scale whites dim on the main bed AND the minis')
ok((paneSrc.match(/steps: SCALES\[scaleIdx\]\[1\], root: keyIdx/g) || []).length === 3, 'all three beds receive the live scale')

// -- v0.3.2 bank-aware slot guess: no read yet, pgm 5 in bank 1 => P134 (was P6)
pushDelta({ type: 'delta', seq: 8, cc: {}, events: [], active_notes: [], program: 5, bank: 1, live: false, listening: true, preset: null })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
if (!all.includes('P134')) console.log("P134-DEBUG:", (all.match(/P\d+[^\n]{0,20}/g) || []).slice(0, 6), "| has bank in state render?", /bank\s*1/.test(all))
ok(all.includes('P134'), 'slot guess is bank-aware (bank1 pgm5 -> P134)')

// -- anchor discipline (screenshot bug 2026-09-16): clicks and near-edge
// notes must NEVER drag the window; only far physical notes or OCT buttons.
async function reD(d) { pushDelta(d); React.flags.dirty = true
  for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
  return texts(tree).join('\n') }
let w = await reD({ type: 'delta', seq: 20, cc: {}, events: [{ seq: 20, kind: 'note', note: 96, velocity: 96, source: 'ui', ts: now }], active_notes: [96], program: 12, live: false, listening: true, preset: null })
ok(/keys C4/.test(w), 'a CLICKED note (96, source ui) must not move the window - now that no minibed exists to click it')
w = await reD({ type: 'delta', seq: 21, cc: {}, events: [{ seq: 21, kind: 'note', note: 85, velocity: 96, source: 'panel', ts: now }], active_notes: [85], program: 12, live: false, listening: true, preset: null })
ok(/keys C4/.test(w), 'physical C# just past the top C (85) must not slide the octave')
w = await reD({ type: 'delta', seq: 22, cc: {}, events: [{ seq: 22, kind: 'note', note: 92, velocity: 96, source: 'panel', ts: now }], active_notes: [92], program: 12, live: false, listening: true, preset: null })
ok(/keys C5/.test(w), 'physical note a full octave up (92, OCT+ story) must follow to C5')

// -- engine table regression (owner 2026-09-16): the REAL ENGINES_22 from the pane must
// carry 22 unique names with Speech between Chords and Modal (old table: Speech absent,
// Modal twice). Band decode is exercised exhaustively by the 22x2-edge probe below.
{
  const tbl = mod.ENGINES_22 ?? (mod.default && mod.default.ENGINES_22)
  if (tbl) {
    const names = tbl.slice(1)
    ok(names.length === 22 && new Set(names).size === 22, 'ENGINES_22 has exactly 22 unique engine names')
    ok(names[9] === 'Chords' && names[10] === 'Speech' && names[11] === 'Modal', 'order: Chords -> Speech -> Modal (no doubled Modal, no missing Speech)')
  }
}



// -- v0.4.1 ENGINE TRUTH battery (community capture: panel TRANSMITS non-uniform values;
// the device RECEIVES contiguous ranges; the pane must decode each world correctly)
{
  const rows = mod.ENGINE_ROWS ?? (mod.default && mod.default.ENGINE_ROWS)
  let bad = []
  for (let k = 0; k < 22; k++) {
    const [name, tx, rmin, rmax] = rows[k]
    for (const [val, src2, tag] of [[tx, 'panel', 'PANEL-TX'], [Math.round((rmin + rmax) / 2), 'visualizer', 'PANE-MID']]) {
      pushDelta({ type: 'delta', seq: 70 + k * 2, cc: { '9': { value: val, source: src2, ts: now } }, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null })
      React.flags.dirty = true
      for (let i2 = 0; i2 < 40 && React.flags.dirty; i2++) { React.flags.dirty = false; await new Promise((r) => setTimeout(r, 10)); tree = renderTree() }
      const lines = texts(tree)
      if (!lines.some((l) => l.trim() === name)) bad.push(`${tag} ${name}: cc=${val} src=${src2} rendered something else`)
    }
  }
  ok(bad.length === 0, 'all 22 engines: panel transmits + pane receive-midpoints both decode to their own name' + (bad.length ? ': ' + bad.slice(0, 4).join(' ; ') : ''))
}

// -- the REAL domains the panel shows (owner readout + manual 5.0 sec 8/9/12)
pushDelta({ type: 'delta', seq: 8, cc: { '91': { value: 64, source: 'panel', ts: now }, '93': { value: 127, source: 'panel', ts: now }, '105': { value: 90, source: 'panel', ts: now }, '28': { value: 91, source: 'panel', ts: now }, '106': { value: 75, source: 'panel', ts: now } }, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
ok(all.includes('146.5bpm'), 'arp rate cc91=64 -> 146.5bpm (owner-fitted 0.86 curve, .5 quantization)')
ok(all.includes('146.5bpm') && !all.includes('/8'), 'free lane shows bpm only')
ok(all.includes('100.0Hz'), 'lfo rate cc93=127 must display exactly 100Hz')
ok(all.includes('3.2s'), 'attack cc105=90 -> 3.2s (10x^3.3 fit)')
ok(all.includes('5.1s'), 'hold cc28=91 -> 5.1s (square curve, owner: 5s)')
ok(all.includes('5.4s'), 'dec/rel cc106=75 -> 5.4s (owner panel value, exact)')
pushDelta({ type: 'delta', seq: 11, cc: { '92': { value: 70, source: 'panel', ts: now } }, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null })
React.flags.dirty = true
for (let i = 0; i < 40 && React.flags.dirty; i++) { React.flags.dirty = false; await new Promise(r => setTimeout(r, 10)); tree = renderTree() }
all = texts(tree).join('\n')
ok(all.includes('1/8'), 'arp synced cc92=70 must map to division 1/8 (lane switch on the same knob)')
ok(!['DICE','UTILITY','PARA','RANDOM','PATTERN','BEND','ARP|SEQ','OCT|MOD','UP|A','ORDER|B','AMP MOD','MASTER','SAVE ·','shift is a panel','ARP / SEQ'].some((t) => all.includes(t)), 'panel-side-only controls + ARP/SEQ header excised from the face (v0.2.6d/7a)')
ok(all.includes('HOLD') && !all.includes('SPICE') && all.includes('OCT'), 'real-MIDI controls survive v0.2.7b sweep (HOLD=CC64, OCT window ±; SPICE off-face by owner design)')
ok(all.includes('in 52 35B'), 'raw sysex debug trail surfaces in the footer')

// ---- v0.6 PAIR pins: every new route is BOTH called by the pane AND registered ----
for (const rt of ['/chord', '/chord/play', '/chord/release', '/theory/chord', '/diag',
                  '/stress/start', '/stress/stop', '/stress/export']) {
  const reg = apiSrc.includes(`@router.post("${rt}")`) || apiSrc.includes(`@router.get("${rt}")`)
  ok(paneSrc.includes(`'${rt}'`) && reg, `v0.6 PAIR: ${rt} called by pane AND registered by backend`)
}
// agent/CLI surface (no pane control by design): registered + covered by test_routes.py
for (const rt of ['/theory', '/theory/progression', '/tutor']) {
  const reg = apiSrc.includes(`@router.post("${rt}")`) || apiSrc.includes(`@router.get("${rt}")`)
  ok(reg && apiSrc.includes('@router.get("/theory")'), `v0.6 agent surface: ${rt} registered by backend (read-only)`)
  ok(!paneSrc.includes(`'${rt}'`), `v0.6 agent surface: ${rt} is deliberately NOT a pane control`)
}

// ---- v0.6 CHORD LAB + F0a DIAGNOSTICS render ----
cd = await settle({ type: 'delta', seq: 62, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true,
  preset: null, chord: { held: [], count: 0 },
  diag: { running: false, run: null, rate_hz_now: 0, counters: { attempted: 0, sent: 0, virtual: 0, refused: 0, notes_on: 0, notes_off: 0, by_source: {} }, sendlog_rows: 0 } })
ok(cd.includes('Chord Lab') && cd.includes('play & hold') && cd.includes('release'), 'v0.6 chord lab controls render')
ok(cd.includes('Diagnostics (F0a)') && cd.includes('cc_sweep') && cd.includes('export mfstress/1'), 'v0.6 diagnostics row renders')
ok(cd.includes('readout') && cd.includes('preview:'), 'v0.6 readout button + backend-built chord preview render')
ok(!paneSrc.includes('PARAPHONIC'), 'v0.6 pane text avoids the excised-face vocabulary')
ok(cd.includes('LIVE off: runs are VIRTUAL'), 'v0.6 harness states its honesty mode in the UI')
ok(cd.includes('nothing held'), 'v0.6 cold chord-lab hint')
cd = await settle({ type: 'delta', seq: 63, cc: {}, events: [], active_notes: [60, 64, 67, 71], program: 12, live: false, listening: true,
  preset: null, chord: { held: [60, 64, 67, 71], count: 2, last: { label: 'Cmaj7', notes: [60, 64, 67, 71], action: 'hold', sent: [], virtual: [60, 64, 67, 71], source: 'chord:lab', gen: 1, at: now } },
  diag: { running: true, run: { id: 'cc_sweep-1', pattern: 'cc_sweep', state: 'running' }, rate_hz_now: 41, counters: { attempted: 120, sent: 0, virtual: 120, refused: 0, notes_on: 0, notes_off: 0, by_source: {} }, sendlog_rows: 0 } })
ok(cd.includes('HELD: Cmaj7 · 60 64 67 71'), 'v0.6 held-chord readout names the chord + notes')
ok(cd.includes('STOP (running…)'), 'v0.6 running harness exposes a STOP button')
ok(cd.includes('rate now 41/s'), 'v0.6 harness rate readout is live from /state')

// ---- v0.6 stale-backend honesty: an api-3 backend must disable, not fire ----
cd = await settle({ type: 'delta', seq: 64, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null, api: 3 })
ok(cd.includes('backend is older than this pane') && cd.includes('backend older than this pane (no /stress routes)'),
   'v0.6 stale api 3 backend is refused loudly (silent-404 lesson)')
cd = await settle({ type: 'delta', seq: 65, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null, api: 4 })

// -- v0.5 LOOP STUDIO: section renders; then a live saved loop with transport --
cd = await settle({ type: 'delta', seq: 60, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null })
ok(cd.includes('Loop studio') && cd.includes('record loop') && cd.includes('open storage folder'), 'v0.5 loop studio + storage reveal render')
ok(cd.includes('no loops yet'), 'v0.5 empty-loops hint')
cd = await settle({ type: 'delta', seq: 61, cc: {}, events: [], active_notes: [], program: 12, live: false, listening: true, preset: null,
  loops: [{ id: 'L9', name: 'route take', notes: 4, bars: 2, bpm: 120.0, spb: 4, created: 1 }],
  loops_playing: [{ id: 'L9', loop: 'loop', stretch: 1, position_ms: 1500, span_ms: 4000, generation: 1 }],
  loop_rec: null })
ok(cd.includes('route take') && cd.includes('4n · 2bars'), 'v0.5 loop card shows the saved loop')
ok(cd.includes('once'), 'v0.5 replay-mode select present')

React.flushDisposers()

for (const h of createdIntervals) clearInterval(h)
if (fails.length) { console.error('SMOKE FAIL:\n - ' + fails.join('\n - ')); process.exit(1) }
{
  const h = (f) => existsSync(f) ? createHash('sha256').update(readFileSync(f)).digest('hex').slice(0, 12) : 'missing'
  console.log(`source ${h(`${REPO}/desktop/plugin.js`)} vs ${DEPLOY_NOTE} ${h(`${DEPL}/plugin.js`)}`)
}
console.log('SMOKE v4 PASS: engine banding, % domain, 25-key bed, WS deltas, octave follow, matrix EXCISED, pressure strip, read button, chord + per-key stats, OLED slot read, pressure-bar EXCISED, minis RESTORED, deep chords (m7b5/inv/6add9/cluster), FAST analyzer (rAF paint + /spectrum), BLOCK-not-snap scale lock, PANIC kill switch, v0.5 LOOP STUDIO + storage reveal, v0.6 CHORD LAB + F0a DIAGNOSTICS + 10 v0.6 pair pins')
