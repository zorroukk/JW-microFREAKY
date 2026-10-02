"""gate — the pre-finish verification gate for mf-visualizer (master plan, addendum).

Every change class has a required proof. This runs ALL of them, offline, and fails
loud if any lane is red. It is the mechanical version of the checklist the owner
approved in the 2026-09-21 addendum:

  backend changed          -> every standalone battery (they boot the real backend)
  pane (plugin.js) changed -> node --check + the fake-react pane smoke harness
  MIDI send path changed   -> the routes battery's mock + sendlog assertions
  docs/plan changed        -> <owner ledger row>  (checked by hand; not automated)
  version stamp proposed   -> the device-verify debt cap (tools/bump_check.py)

Usage: python tools/gate.py [--quick]     (--quick skips the pane smoke)
Exit: 0 all green, 1 something failed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASH = REPO / "dashboard"
BATTERIES = ("test_modmath.py", "test_modengine.py", "test_loopengine.py",
             "test_preset_codec.py", "test_smf.py", "test_theory.py", "test_spectrum.py",
             "test_registry.py", "test_commands.py", "test_routes.py")


def run(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-1:] or [""]
    return p.returncode, (tail[0] if tail else "") + ("" if p.returncode == 0 else
                                                     f"  [stderr: {(p.stderr or '').strip().splitlines()[-1:]} ]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the pane smoke harness")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    fails, t0 = [], time.time()

    code, out = run([a.python, "-m", "py_compile", "plugin_api.py", "theory.py",
                     "chordlab.py", "stress.py", "modengine.py", "smf.py",
                     "registry.py", "commands.py"], DASH)
    print(f"{'PASS' if code == 0 else 'FAIL'} py_compile backend modules  {out}")
    fails += [] if code == 0 else ["py_compile"]

    for b in BATTERIES:
        code, out = run([a.python, b], DASH)
        print(f"{'PASS' if code == 0 else 'FAIL'} {b:<22} {out[:110]}")
        fails += [] if code == 0 else [b]

    if not a.quick:
        code, out = run(["node", "--check", "desktop/plugin.js"], REPO)
        print(f"{'PASS' if code == 0 else 'FAIL'} node --check pane        {out[:110]}")
        fails += [] if code == 0 else ["node --check"]
        code, out = run(["node", "tools/pane_smoke.mjs"], REPO, timeout=600)
        print(f"{'PASS' if code == 0 else 'FAIL'} pane smoke harness       {out[:110]}")
        fails += [] if code == 0 else ["pane_smoke"]

    code, out = run([a.python, "tools/test_bump_check.py"], REPO)
    print(f"{'PASS' if code == 0 else 'FAIL'} debt-cap battery          {out[:110]}")
    fails += [] if code == 0 else ["test_bump_check"]
    code, out = run([a.python, "tools/bump_check.py"], REPO)
    print(f"{'INFO' if code in (0, 2) else 'FAIL'} debt cap verdict          "
          f"{'REFUSED for a minor bump (device debt)' if code == 2 else out[:110]}")
    fails += [] if code in (0, 2) else ["bump_check"]     # 2 = refused on purpose, not a gate failure

    print(f"\n{'GATE PASS' if not fails else 'GATE FAIL: ' + ', '.join(fails)} "
          f"in {time.time() - t0:.1f}s")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
