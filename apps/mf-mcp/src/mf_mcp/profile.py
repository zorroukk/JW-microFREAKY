"""FW5 control profile loader + name/alias resolution with validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "control-profile-v5.json"
RESERVED_CCS = {0, 6, 7, 10, 11, 64}  # bank/select, data, volume, expression, sustain-sw, hold-1


class ProfileError(ValueError):
    pass


class Profile:
    def __init__(self, data: dict[str, Any]):
        if data.get("device") != "Arturia MicroFreak":
            raise ProfileError("profile is not for Arturia MicroFreak")
        self.params: dict[str, dict[str, Any]] = data.get("parameters") or {}
        if not self.params:
            raise ProfileError("profile has no parameters")
        for name, spec in self.params.items():
            cc = spec.get("cc")
            if not isinstance(cc, int) or not 0 <= cc <= 127:
                raise ProfileError(f"parameter {name!r} has bad cc {cc!r}")
        self.aliases: dict[str, str] = data.get("aliases") or {}
        self.meta = {k: data.get(k) for k in ("profile_id", "firmware_basis", "scope")}

    @classmethod
    def load(cls, path: str | Path | None = None) -> Profile:
        p = Path(path) if path else DEFAULT_PROFILE_PATH
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def resolve(self, name: str) -> tuple[str, dict[str, Any]]:
        key = name.strip().lower().replace(" ", "_").replace("-", "_")
        key = self.aliases.get(key, key)
        spec = self.params.get(key)
        if spec is None:
            raise ProfileError(
                f"unknown parameter {name!r}; known: {sorted(self.params)}"
            )
        return key, spec

    def clamp(self, key: str, spec: dict[str, Any], value: int) -> int:
        lo, hi = int(spec.get("min", 0)), int(spec.get("max", 127))
        return max(lo, min(hi, int(value)))
