"""F1 Registry v2 - single backend authority for the 21 documented live-CC controls.

Authority chain (never invented):
  * landing domain / kind / risk / aliases  <- dashboard/control-profile-v5.json
    (Arturia MicroFreak User Manual 5.0.1, Appendix D).
  * UI display domain                       <- desktop/plugin.js, the pane's
    proven display laws, cited per entry by CONSTANT NAME.

Provenance labels carried on every display entry:
  'pane-law' -> domain decoded from a named constant in plugin.js
  'profile'  -> domain pinned by control-profile-v5.json itself
  'none'     -> no domain pinned anywhere; honestly raw 0..127 cc-domain

REFUSE-CLOSED: a CC that is not one of the 21 documented live CCs is refused
('not-on-surface'); nothing is ever invented onto the wire.

stdlib only, numpy-free. Import fails LOUD: a broken profile raises here, it
never degrades into a silent empty registry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "RegistryError", "Param", "Registry", "REGISTRY", "DEFAULT_PROFILE",
    "load", "lookup", "validate", "by_cc", "sections", "describe",
]

DEFAULT_PROFILE = Path(__file__).resolve().parent / "control-profile-v5.json"

# Section vocabulary (manual headers). CC -> section for all 21 params.
SECTION_BY_CC: Dict[int, str] = {
    2: "oscillator", 5: "oscillator", 9: "oscillator", 10: "oscillator",
    12: "oscillator", 13: "oscillator",
    23: "filter", 83: "filter", 26: "filter",
    24: "cycling-env", 28: "cycling-env", 102: "cycling-env", 103: "cycling-env",
    105: "envelope", 106: "envelope",
    29: "global", 64: "global",
    91: "arp-clock", 92: "arp-clock",
    93: "lfo-mod", 94: "lfo-mod",
}

# --- pane-mirrored tables (pinned against desktop/plugin.js by the battery) --
ENGINES_22: List[str] = [
    "BasicWaves", "SuperWave", "Wavetable", "Harmo", "KarplusStr", "V.Analog",
    "Waveshaper", "Two Op. FM", "Formant", "Chords", "Speech", "Modal",
    "Noise", "Bass", "SawX", "Harm", "WaveUser", "Sample", "Scan Grains",
    "Cloud Grains", "Hit Grains", "Vocoder",
]
ARP_DIVS: List[str] = [
    "1/1", "1/2", "1/2T", "1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T",
    "1/32", "1/32T",
]
LFO_DIVS: List[str] = [
    "8/1", "4/1", "2/1", "1/1", "1/2", "1/2T", "1/4", "1/4T", "1/8", "1/8T",
    "1/16", "1/16T",
]

_RAW_LABEL = "raw 0..127 cc-domain (no pane/profile display law pinned)"

# Display law per CC. `constant` is the pane constant name that pins the
# domain; the battery proves every `constant` really exists in plugin.js.
DISPLAY_BY_CC: Dict[int, Dict[str, Any]] = {
    2: {
        "type": "raw", "unit": "cc", "basis": "none", "constant": None,
        "law": _RAW_LABEL + " - the pane's SPICE face control was excised "
                            "(plugin.js:206,909); CC2 stays drivable via register/chat",
    },
    5: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[5]",
        "law": "10*(t/127)^3 s, approximately (glide unprobed, pane tip 'time mode 0-10s')",
        "max_s": 10, "exponent": 3,
    },
    9: {
        "type": "enum", "unit": "engine", "basis": "pane-law", "constant": "ENGINE_ROWS",
        "law": "22 detented engines; DISPLAY = nearest transmit value (source=panel), "
               "SEND = centre of the engine's receive range (other sources)",
        "options": list(ENGINES_22), "count": len(ENGINES_22),
    },
    10: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    12: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    13: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    23: {
        "type": "hz", "unit": "Hz", "basis": "pane-law", "constant": "FMT[23]",
        "law": "exponential 16Hz..26.9kHz between owner-confirmed endpoints",
        "min_value": 16, "max_value": 26900, "scale": "exponential",
    },
    24: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    26: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain (wraps a bipolar-encoded, unknown-centre device control)",
    },
    28: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[28]",
        "law": "10*(t/127)^2 s (cycling-env hold, pane tip 0ms-10s)",
        "max_s": 10, "exponent": 2,
    },
    29: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    64: {
        "type": "enum", "unit": "toggle", "basis": "pane-law", "constant": "LedButton",
        "law": "on = value > 63 (pane HOLD button)",
        "options": ["off", "on"],
    },
    83: {
        "type": "percent", "unit": "%", "basis": "pane-law", "constant": "toPct",
        "law": "0-100% panel domain: round(cc*100/127)",
    },
    91: {
        "type": "steps", "unit": "bpm", "basis": "pane-law", "constant": "FMT[91]",
        "law": "30+210*(t/127)^0.86 bpm, quarter-step quantized",
        "min_bpm": 30, "max_bpm": 240, "exponent": 0.86, "quantize": 0.5,
    },
    92: {
        "type": "enum", "unit": "division", "basis": "pane-law", "constant": "ARP_DIVS",
        "law": "floor(t*11/128) index into ARP_DIVS (pane FMT[92])",
        "options": list(ARP_DIVS), "count": len(ARP_DIVS),
    },
    93: {
        "type": "hz", "unit": "Hz", "basis": "pane-law", "constant": "FMT[93]",
        "law": "0.06*(100/0.06)^((t/127)^1.02) Hz",
        "min_value": 0.06, "max_value": 100, "scale": "exponential",
    },
    94: {
        "type": "enum", "unit": "division", "basis": "pane-law", "constant": "LFO_DIVS",
        "law": "floor(t*12/128) index into LFO_DIVS (pane FMT[94])",
        "options": list(LFO_DIVS), "count": len(LFO_DIVS),
    },
    102: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[102]",
        "law": "10*(t/127)^3 s (cycling-env rise, pane tip 0ms-10.0s)",
        "max_s": 10, "exponent": 3,
    },
    103: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[103]",
        "law": "10*(t/127)^3 s (cycling-env fall, pane tip 0ms-10.0s)",
        "max_s": 10, "exponent": 3,
    },
    105: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[105]",
        "law": "10*(t/127)^3.3 s (attack)",
        "max_s": 10, "exponent": 3.3,
    },
    106: {
        "type": "time", "unit": "s", "basis": "pane-law", "constant": "FMT[106]",
        "law": "13*(t/127)^(5/3) s (decay/release, mode dependent)",
        "max_s": 13, "exponent": 1.6666666666666667,
    },
}

DISPLAY_BASES = ("pane-law", "profile", "none")
DISPLAY_TYPES = ("percent", "hz", "enum", "steps", "raw", "time")
_PROVENANCE_TYPES = ("hz", "enum", "steps", "time")


class RegistryError(ValueError):
    """Loud failure: a registry that cannot be trusted must not exist."""


@dataclass(frozen=True)
class Param:
    name: str
    cc: int
    min: int
    max: int
    kind: str
    risk: str
    section: str
    display: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    is_allowed: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "cc": self.cc, "min": self.min, "max": self.max,
            "kind": self.kind, "risk": self.risk, "section": self.section,
            "display": dict(self.display), "aliases": list(self.aliases),
            "is_allowed": self.is_allowed,
        }


class Registry:
    """One authority for the 21 documented live CCs."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        self.schema_version = data.get("schema_version")
        self.profile_id = data.get("profile_id")
        self.evidence = data.get("evidence", {})
        self.runtime_policy = data.get("runtime_policy", {})
        self.scope = data.get("scope", {})

        params_raw = data.get("parameters")
        if not isinstance(params_raw, dict) or not params_raw:
            raise RegistryError("profile has no parameters map")

        aliases_raw = data.get("aliases") or {}
        if not isinstance(aliases_raw, dict):
            raise RegistryError("profile aliases must be a map")

        # reverse alias index: param -> [aliases]
        alias_for: Dict[str, List[str]] = {name: [] for name in params_raw}
        for alias, target in aliases_raw.items():
            if target not in params_raw:
                raise RegistryError(f"alias {alias!r} points at unknown param {target!r}")
            if alias in params_raw:
                raise RegistryError(f"alias {alias!r} shadows a parameter name")
            alias_for[target].append(alias)

        params: Dict[str, Param] = {}
        for name, spec in params_raw.items():
            if not isinstance(spec, dict):
                raise RegistryError(f"param {name!r} is not an object")
            for key in ("cc", "min", "max", "kind", "risk"):
                if key not in spec:
                    raise RegistryError(f"param {name!r} missing {key!r}")
            cc = spec["cc"]
            if not isinstance(cc, int) or isinstance(cc, bool) or not (0 <= cc <= 127):
                raise RegistryError(f"param {name!r} has unusable cc {cc!r}")
            section = SECTION_BY_CC.get(cc)
            if section is None:
                raise RegistryError(f"param {name!r} (cc {cc}) has no section assignment")
            display = DISPLAY_BY_CC.get(cc)
            if display is None:
                raise RegistryError(f"param {name!r} (cc {cc}) has no display law")
            params[name] = Param(
                name=name, cc=cc, min=int(spec["min"]), max=int(spec["max"]),
                kind=str(spec["kind"]), risk=str(spec["risk"]), section=section,
                display=dict(display), aliases=list(sorted(alias_for[name])),
                is_allowed=cc in DISPLAY_BY_CC,
            )

        ccs = [p.cc for p in params.values()]
        if len(set(ccs)) != len(ccs):
            raise RegistryError("duplicate cc in profile")

        self.params = params
        self.aliases = dict(aliases_raw)
        self._by_cc = {p.cc: p for p in params.values()}
        self._by_alias = {a: params[t] for a, t in aliases_raw.items()}

    # ------------------------------------------------------------ lookup --
    def lookup(self, name_or_alias: Any) -> Optional[Param]:
        if isinstance(name_or_alias, str):
            return self.params.get(name_or_alias) or self._by_alias.get(name_or_alias)
        if isinstance(name_or_alias, int) and not isinstance(name_or_alias, bool):
            return self._by_cc.get(name_or_alias)
        return None

    def get(self, cc: int) -> Optional[Param]:
        if isinstance(cc, int) and not isinstance(cc, bool):
            return self._by_cc.get(cc)
        return None

    def by_cc(self) -> Dict[int, Param]:
        return dict(self._by_cc)

    def sections(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for p in self.params.values():
            out.setdefault(p.section, []).append(p.name)
        for names in out.values():
            names.sort()
        return out

    # ---------------------------------------------------------- validate --
    def validate(self, cc: Any, value: Any) -> Tuple[bool, Optional[int], str]:
        """REFUSE-CLOSED. Returns (ok, clamped_or_none, why).

        why in {'ok', 'clamped', 'not-on-surface', 'non-int'}.
        """
        if not isinstance(cc, int) or isinstance(cc, bool):
            return (False, None, "not-on-surface")
        p = self._by_cc.get(cc)
        if p is None:
            return (False, None, "not-on-surface")
        if not isinstance(value, int) or isinstance(value, bool):
            return (False, None, "non-int")
        if value < p.min:
            return (True, p.min, "clamped")
        if value > p.max:
            return (True, p.max, "clamped")
        return (True, value, "ok")

    # ---------------------------------------------------------- describe --
    def describe(self, name_or_cc: Any) -> Dict[str, Any]:
        if isinstance(name_or_cc, int) and not isinstance(name_or_cc, bool):
            p = self._by_cc.get(name_or_cc)
        else:
            p = self.lookup(name_or_cc)
        if p is None:
            raise KeyError(f"unknown control: {name_or_cc!r}")
        disp = p.display
        unit = disp.get("unit") or "cc"
        tip = f"{p.name} (CC{p.cc}) - {p.section} - {disp.get('law', _RAW_LABEL)}"
        return {
            "name": p.name, "cc": p.cc, "min": p.min, "max": p.max,
            "kind": p.kind, "risk": p.risk, "section": p.section,
            "display": dict(disp), "aliases": list(p.aliases),
            "is_allowed": p.is_allowed, "unit": unit, "tooltip": tip,
        }


def load(path: Optional[Any] = None) -> Registry:
    """Load the control profile. Raise RegistryError (a ValueError) on any
    defect - never return an empty/partial registry."""
    p = Path(path) if path is not None else DEFAULT_PROFILE
    try:
        raw = Path(p).read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read profile {p}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RegistryError(f"profile {p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"profile {p} is not a JSON object")
    return Registry(data, path=p)


REGISTRY = load()

# module-level thin wrappers (spec: module-level API)
lookup = REGISTRY.lookup
validate = REGISTRY.validate
by_cc = REGISTRY.by_cc
sections = REGISTRY.sections
describe = REGISTRY.describe