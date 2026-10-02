"""Exhaustive battery for registry.py (F1 Registry v2). Run: python test_registry.py

Lane law: a registry that backs a hardware write path gets exhaustive coverage,
not curated examples. This walks all 21 documented live CCs x 7 boundary probes,
every alias, every section, every display law - and pins every pane-cited
constant against the LIVE desktop/plugin.js text, so drift between the pane's
display law and the backend registry FAILS here, not on stage.

Run from dashboard/:  python test_registry.py
"""
import json
import re
import sys
import tempfile
from pathlib import Path

import registry as RG

fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except (RG.RegistryError, ValueError, KeyError):
        return True


PROFILE = Path(RG.DEFAULT_PROFILE)
PLUGIN = Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"
js = PLUGIN.read_text(encoding="utf-8")

R = RG.REGISTRY

# --------------------------------------------------------------- load ------ #
ok(PROFILE.exists(), f"default profile missing: {PROFILE}")
ok(R.path is not None and Path(R.path) == PROFILE, "REGISTRY did not load the default path")
ok(R.schema_version == 1, f"schema_version changed: {R.schema_version}")
ok(R.profile_id == "official-v5", f"profile_id changed: {R.profile_id}")
ok(R.runtime_policy.get("minimum_inter_message_delay_ms") == 20,
   "inter-message delay policy not carried")

# ------------------------------------------------------------ 21 params ---- #
EXPECTED = 21
ok(len(R.params) == EXPECTED, f"param count: {len(R.params)} != {EXPECTED}")
ok(len(R.by_cc()) == EXPECTED, f"by_cc size: {len(R.by_cc())} != {EXPECTED}")
ccs = [p.cc for p in R.params.values()]
ok(len(set(ccs)) == len(ccs), "duplicate cc survives load")
SECVOCAB = {"oscillator", "filter", "envelope", "lfo-mod", "arp-clock", "global",
            "cycling-env"}
for name, p in R.params.items():
    ok(bool(p.section) and p.section in SECVOCAB, f"{name}: bad section {p.section!r}")
    ok(isinstance(p.kind, str) and bool(p.kind), f"{name}: missing kind")
    ok(isinstance(p.risk, str) and bool(p.risk), f"{name}: missing risk")
    ok(isinstance(p.min, int) and isinstance(p.max, int) and p.min <= p.max,
       f"{name}: bad min/max {p.min}..{p.max}")
    ok(isinstance(p.display, dict) and bool(p.display), f"{name}: missing display")
    ok(p.is_allowed is True, f"{name}: documented control flagged not-allowed")
    ok(isinstance(p.aliases, list), f"{name}: aliases is not a list")
    ok(R.lookup(name) is p, f"{name}: lookup(name) did not return the param")

# --------------------------------------------------------------- aliases --- #
profile = json.loads(PROFILE.read_text(encoding="utf-8"))
profile_aliases = profile["aliases"]
ok(len(profile_aliases) == 9, f"profile alias count changed: {len(profile_aliases)}")
seen_alias = set()
for alias, target in profile_aliases.items():
    ok(alias not in seen_alias, f"duplicate alias {alias!r}")
    seen_alias.add(alias)
    ok(alias not in R.params, f"alias {alias!r} shadows a parameter name")
    resolved = R.lookup(alias)
    ok(resolved is not None, f"alias {alias!r} does not resolve")
    if resolved is not None:
        ok(resolved.name == target, f"alias {alias!r} -> {resolved.name} (want {target})")
        ok(alias in resolved.aliases, f"{target}: alias {alias!r} missing from param.aliases")
# no alias resolves to two params, and alias lists are exactly the reverse map
for name, p in R.params.items():
    want = sorted(a for a, t in profile_aliases.items() if t == name)
    ok(list(p.aliases) == want, f"{name}: aliases {p.aliases} != profile reverse {want}")
ok(R.lookup("no_such_control") is None, "unknown name should not resolve")
ok(R.lookup(999) is None, "unknown cc should not resolve")
ok(R.lookup(None) is None, "None should not resolve")

# --------------------------------------------------------------- validate -- #
probes = 0
for name, p in R.params.items():
    mid = (p.min + p.max) // 2
    # (value, expect_ok, expect_value, expect_why)
    cases = [
        (p.min - 1, True, p.min, "clamped"),
        (p.min, True, p.min, "ok"),
        (p.min + 1, True, p.min + 1, "ok"),
        (mid, True, mid, "ok"),
        (p.max - 1, True, p.max - 1, "ok"),
        (p.max, True, p.max, "ok"),
        (p.max + 1, True, p.max, "clamped"),
    ]
    for value, e_ok, e_val, e_why in cases:
        probes += 1
        got_ok, got_val, got_why = R.validate(p.cc, value)
        ok(got_ok is e_ok, f"{name} cc{p.cc} v={value}: ok={got_ok} want {e_ok}")
        ok(got_val == e_val, f"{name} cc{p.cc} v={value}: value={got_val} want {e_val}")
        ok(got_why == e_why, f"{name} cc{p.cc} v={value}: why={got_why!r} want {e_why!r}")
    # names and aliases are valid first args to validate too? validate is cc-first.
    ok(R.validate(p.cc, p.min)[0] is True, f"{name}: cc-first validate failed")

# off-surface cc probes: documented CCs must be refused, never invented onto wire
for bad in (0, 1, 3, 50, 120):
    ok_ok, val, why = R.validate(bad, 64)
    ok(ok_ok is False, f"off-surface cc {bad} was accepted")
    ok(val is None, f"off-surface cc {bad} returned a value")
    ok(why == "not-on-surface", f"off-surface cc {bad} why={why!r}")
# a couple of extra off-surface probes around the documented set edge
for bad in (8, 11, 22, 25, 63, 65, 82, 90, 101, 107, 127):
    got_ok, _, why = R.validate(bad, 64)
    ok(got_ok is False and why == "not-on-surface", f"off-surface cc {bad} not refused ({why})")

# non-int values refused (floats, strings, None, bools, containers)
for bad_val in (None, 3.7, "64", "0", [5], {"v": 5}, True, False):
    got_ok, val, why = R.validate(23, bad_val)
    ok(got_ok is False, f"non-int value {bad_val!r} accepted")
    ok(val is None, f"non-int value {bad_val!r} returned a value")
    ok(why == "non-int", f"non-int value {bad_val!r} why={why!r}")
# non-int cc refused as not-on-surface
for bad_cc in ("23", None, 3.5, True):
    got_ok, _, why = R.validate(bad_cc, 64)
    ok(got_ok is False and why == "not-on-surface", f"non-int cc {bad_cc!r} not refused")

# --------------------------------------------------------------- sections -- #
sec = R.sections()
ok(sum(len(v) for v in sec.values()) == EXPECTED,
   f"sections cover {sum(len(v) for v in sec.values())} != {EXPECTED}")
covered = sorted(n for names in sec.values() for n in names)
ok(covered == sorted(R.params), "sections do not cover every param exactly once")
ok(set(sec) <= SECVOCAB, f"section vocabulary drift: {sorted(set(sec) - SECVOCAB)}")
# the lane's table, pinned
PIN = {2: "oscillator", 5: "oscillator", 9: "oscillator", 10: "oscillator",
       12: "oscillator", 13: "oscillator", 23: "filter", 83: "filter", 26: "filter",
       24: "cycling-env", 28: "cycling-env", 102: "cycling-env", 103: "cycling-env",
       105: "envelope", 106: "envelope", 29: "global", 64: "global",
       91: "arp-clock", 92: "arp-clock", 93: "lfo-mod", 94: "lfo-mod"}
ok(len(PIN) == EXPECTED, "section pin table does not cover 21")
for cc, want in PIN.items():
    p = R.get(cc)
    ok(p is not None, f"cc {cc} not in registry")
    if p is not None:
        ok(p.section == want, f"cc {cc} ({p.name}) section {p.section} != {want}")

# ---------------------------------------------------------------- display -- #
def base_token(const):
    return re.split(r"[\[(]", const)[0] if const else None

for name, p in R.params.items():
    d = p.display
    ok(d.get("type") in RG.DISPLAY_TYPES, f"{name}: bad display type {d.get('type')!r}")
    ok(d.get("basis") in RG.DISPLAY_BASES, f"{name}: bad basis {d.get('basis')!r}")
    ok(isinstance(d.get("unit"), str) and bool(d["unit"]), f"{name}: missing unit")
    ok(isinstance(d.get("law"), str) and bool(d["law"]), f"{name}: missing law")
    basis, typ, const = d.get("basis"), d.get("type"), d.get("constant")
    if basis == "none":
        # an unpinned entry may ONLY be honestly raw
        ok(typ == "raw", f"{name}: basis 'none' must be type 'raw', got {typ!r}")
        ok(const is None, f"{name}: basis 'none' must not cite a constant")
    else:
        # any entry that claims a domain must cite the pane constant that pins it
        ok(bool(const), f"{name}: basis {basis!r} cites no constant")
        tok = base_token(const)
        ok(tok and tok in js, f"{name}: constant {const!r} not found in plugin.js")
    if typ == "hz":
        # a unit-bearing frequency domain must be pane-pinned, with endpoints present
        ok(basis == "pane-law", f"{name}: 'hz' display must be pane-law (got {basis})")
        ok(float(d.get("min_value", 0)) > 0 and float(d.get("max_value", 0)) > float(d.get("min_value", 0)),
           f"{name}: bad hz endpoints")
        ok(str(d["min_value"]) in js and str(d["max_value"]) in js,
           f"{name}: hz endpoints {d.get('min_value')}/{d.get('max_value')} not in plugin.js")
    if typ == "time":
        ok(basis == "pane-law" and float(d.get("max_s", 0)) > 0 and float(d.get("exponent", 0)) > 0,
           f"{name}: bad time law")
    if typ == "steps":
        ok(basis == "pane-law" and float(d.get("max_bpm", 0)) > float(d.get("min_bpm", 0)),
           f"{name}: bad steps law")

# the one honest 'none': CC2 SPICE - the pane excised its face control, so no
# domain exists to mirror. If a SPICE band is ever added to plugin.js, this
# assertion fails and forces the registry to be upgraded with it.
ok(R.get(2).display["basis"] == "none" and R.get(2).display["type"] == "raw",
   "CC2 spice display is no longer honestly 'none' - pane may have repinned it")
# ...and pin the excisions that justify it
ok("SPICE + mini beds + pressure bar all excised" in js,
   "pane comment excising SPICE not found - re-derive CC2's display basis")
ok("SPICE off the face" in js, "pane note 'SPICE off the face' not found")

# ------------------------------------------------- pane <-> registry pin ---- #
m = re.search(r"const ENGINE_ROWS = \[(.*?)\n\]", js, re.S)
ok(m is not None, "pane ENGINE_ROWS block not found")
if m:
    pane_engines = re.findall(r"\['([^']+)',", m.group(1))
    ok(len(pane_engines) == 22, f"pane engine count {len(pane_engines)} != 22")
    ok(pane_engines == RG.ENGINES_22, f"registry engines drift from pane: {pane_engines}")
    ok(R.get(9).display["options"] == pane_engines, "CC9 display options drift from pane")
    ok(pane_engines[-1] == "Vocoder", "pane engine order changed (Vocoder must be last)")

m = re.search(r"const ARP_DIVS = \[([^\]]*)\]", js)
ok(m is not None, "pane ARP_DIVS not found")
if m:
    divs = re.findall(r"'([^']+)'", m.group(1))
    ok(divs == RG.ARP_DIVS and len(divs) == 11, f"ARP_DIVS drift: {divs}")
    ok(R.get(92).display["options"] == divs, "CC92 options drift from pane ARP_DIVS")

m = re.search(r"const LFO_DIVS = \[([^\]]*)\]", js)
ok(m is not None, "pane LFO_DIVS not found")
if m:
    divs = re.findall(r"'([^']+)'", m.group(1))
    ok(divs == RG.LFO_DIVS and len(divs) == 12, f"LFO_DIVS drift: {divs}")
    ok(R.get(94).display["options"] == divs, "CC94 options drift from pane LFO_DIVS")

# the sync/free duals exist in the pane as alts, and the pane's own FMT keys
for cc in (91, 92, 93, 94):
    ok(f"{cc}:" in js, f"pane FMT entry for cc{cc} not found")
ok(R.get(91).display["type"] == "steps" and R.get(92).display["type"] == "enum",
   "arp free/sync display types swapped or changed")
ok(R.get(93).display["type"] == "hz" and R.get(94).display["type"] == "enum",
   "lfo free/sync display types swapped or changed")

# ------------------------------------------------------------ describe ----- #
for name, p in R.params.items():
    d = R.describe(name)
    ok(d["name"] == name and d["cc"] == p.cc and d["section"] == p.section,
       f"{name}: describe mismatch")
    ok(json.loads(json.dumps(d)) == json.loads(json.dumps(d)), f"{name}: describe not JSON-safe")
    rt = json.loads(json.dumps(d))
    ok(rt == json.loads(json.dumps(d)), f"{name}: JSON round-trip unstable")
    ok(isinstance(d["tooltip"], str) and d["tooltip"], f"{name}: empty tooltip")
ok(R.describe(23)["name"] == "cutoff", "describe by cc failed")
ok(R.describe("filter_cutoff")["cc"] == 23, "describe by alias failed")
ok(raises(R.describe, "nope"), "describe of unknown control did not raise")
# JSON round-trip of the whole registry surface
dump = json.dumps({n: R.describe(n) for n in R.params}, sort_keys=True)
ok(json.loads(dump) == json.loads(dump), "registry describe dump not stable")

# ------------------------------------------------------ load refuse-closed - #
ok(RG.load(PROFILE) is not None, "explicit load of the real profile failed")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    bad = td / "truncated.json"
    bad.write_text('{"schema_version": 1, "parameters": {"cutoff":', encoding="utf-8")
    ok(raises(RG.load, bad), "truncated profile did not fail loud")

    mutated = json.loads(PROFILE.read_text(encoding="utf-8"))
    del mutated["parameters"]["cutoff"]
    p2 = td / "short.json"
    p2.write_text(json.dumps(mutated), encoding="utf-8")
    ok(raises(RG.load, p2), "profile with a dropped param did not fail loud")

    mutated = json.loads(PROFILE.read_text(encoding="utf-8"))
    mutated["parameters"]["wave"]["cc"] = 9   # collision with oscillator_type
    p3 = td / "dupecc.json"
    p3.write_text(json.dumps(mutated), encoding="utf-8")
    ok(raises(RG.load, p3), "profile with a duplicate cc did not fail loud")

    mutated = json.loads(PROFILE.read_text(encoding="utf-8"))
    mutated["aliases"]["osc_type"] = "ghost_param"
    p4 = td / "badalias.json"
    p4.write_text(json.dumps(mutated), encoding="utf-8")
    ok(raises(RG.load, p4), "profile with a dangling alias did not fail loud")

    ok(raises(RG.load, td / "does-not-exist.json"), "missing profile did not fail loud")

# ---------------------------------------------------------------- report --- #
if fails:
    for f in fails:
        print("FAIL:", f)
    print(f"FAILED — {len(fails)} registry assertion(s)")
    sys.exit(1)

print(f"NOTE: CC2 spice display basis='none' (raw 0..127) — the pane excised the "
      f"SPICE face control (plugin.js:206,909); no domain exists to mirror.")
print(f"ALL OK — registry battery passed ({len(R.params)} params, "
      f"{len(profile_aliases)} aliases, {probes} validate probes, "
      f"{len(R.sections())} sections, 22 engines / 11+12 divisions pinned to pane)")
sys.exit(0)