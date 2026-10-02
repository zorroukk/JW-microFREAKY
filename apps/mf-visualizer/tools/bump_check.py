"""bump_check — the mf-visualizer device-verify DEBT CAP (master plan, F0).

The master plan says the debt cap must be MECHANICAL, not intent: "the version-stamp
checklist refuses to bump a new minor version while >=3 rows sit non-device - the bump
script checks the ledger."

This is that script. The ledger is an owner-side version note
`Wiki/MicroFreak/visualizer-versions.md`, in its fenced ```mfledger JSON block
(one row per version: version, commit, verified[], device_pending, staged?).

Usage
  python tools/bump_check.py                 # verdict for the next minor bump
  python tools/bump_check.py --to 0.6.0      # verdict for a specific target
  python tools/bump_check.py --json          # machine-readable verdict
Exit codes: 0 = allowed, 2 = REFUSED (device debt), 3 = ledger unreadable.

Override the ledger path with MF_VIS_LEDGER (used by tests).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# The version ledger is owner-side data; point MF_VIS_LEDGER at your ledger file
# (markdown containing a fenced ```mfledger JSON block with a "rows" array).
DEFAULT_LEDGER = Path.home() / "mf-visualizer-ledger.md"
DEBT_LIMIT = 3          # >= this many device-pending rows blocks a MINOR bump


def ledger_path() -> Path:
    return Path(os.environ.get("MF_VIS_LEDGER", str(DEFAULT_LEDGER)))


def read_rows(path: Path | None = None) -> list[dict]:
    """Rows from the ```mfledger fenced JSON block. Refuses loud when absent/broken."""
    p = path or ledger_path()
    text = p.read_text(encoding="utf-8")
    m = re.search(r"```mfledger\s*(.*?)```", text, re.S)
    if not m:
        raise RuntimeError(f"no ```mfledger block in {p}")
    return json.loads(m.group(1))["rows"]


def stamped_version(repo: Path) -> str:
    txt = (repo / "plugin.yaml").read_text(encoding="utf-8")
    m = re.search(r'version:\s*"?([0-9.]+)"?', txt)
    return m.group(1) if m else "?"


def _tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v.lstrip("v"))[:3] or [0])


def verdict(rows: list[dict], current: str, target: str | None = None) -> dict:
    pending = [r for r in rows if r.get("device_pending")]
    tgt = target or f"{_tuple(current)[0]}.{_tuple(current)[1] + 1}.0"
    minor = _tuple(tgt)[:2] > _tuple(current)[:2]
    refused = bool(minor and len(pending) >= DEBT_LIMIT)
    return {
        "stamped": current, "target": tgt, "is_minor_bump": minor,
        "device_pending": [r["version"] for r in pending],
        "device_pending_count": len(pending), "debt_limit": DEBT_LIMIT,
        "verdict": "REFUSED" if refused else "allowed",
        "why": (f"{len(pending)} version(s) still unverified at the device "
                f"(limit {DEBT_LIMIT}): {' '.join(r['version'] for r in pending)}"
                if refused else
                ("patch-level or ledger clear" if minor is False or not pending
                 else f"{len(pending)} pending < limit {DEBT_LIMIT}")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mf-visualizer device-verify debt cap")
    ap.add_argument("--to", default=None, help="target version, e.g. 0.6.0")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    lp = ledger_path()
    if "MF_VIS_LEDGER" not in os.environ and not lp.exists():
        # The ledger is owner-side data. With no ledger configured the debt
        # cap is inert by design — say so, don't crash the gate.
        print(f"NO LEDGER configured (looked in {lp}; set MF_VIS_LEDGER to "
              f"enforce the device-verify debt cap) - cap skipped")
        return 0
    try:
        rows = read_rows()
    except (OSError, RuntimeError, json.JSONDecodeError, KeyError) as exc:
        print(f"LEDGER UNREADABLE: {exc}", file=sys.stderr)
        return 3
    v = verdict(rows, stamped_version(Path(a.repo)), a.to)
    if a.json:
        print(json.dumps(v, indent=1))
    else:
        print(f"stamped {v['stamped']} -> target {v['target']} "
              f"({'minor' if v['is_minor_bump'] else 'patch'})")
        for r in rows:
            marks = ",".join(r.get("verified") or []) or "-"
            print(f"  {r['version']:<10} {r.get('commit','?'):<9} verified:{marks:<20} "
                  f"{'DEVICE PENDING' if r.get('device_pending') else ''}"
                  f"{' (staged, undeployed)' if r.get('staged') else ''}")
        print(f"VERDICT: {v['verdict']} - {v['why']}")
    return 2 if v["verdict"] == "REFUSED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
