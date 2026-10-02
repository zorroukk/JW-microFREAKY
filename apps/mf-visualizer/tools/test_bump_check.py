"""Battery for the debt-cap mechanism itself (tools/bump_check.py).

Run: python tools/test_bump_check.py   (from the repo root)
Proves the cap is MECHANICAL: >= 3 device-pending rows refuse a MINOR bump, a
clean ledger allows it, and a patch-level bump always passes. Uses a temp ledger
so it never touches the owner ledger.
"""
import json
import tempfile
from pathlib import Path

import bump_check as bc

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

def ledger(rows):
    d = Path(tempfile.mkdtemp(prefix="mfledger-")) / "ledger.md"
    d.write_text("# ledger\n\n```mfledger\n" +
                 json.dumps({"schema": "mfledger/1", "rows": rows}) + "\n```\n", encoding="utf-8")
    return d

R = lambda v, pend: {"version": v, "commit": "x", "verified": ["offline"], "device_pending": pend}

# 3 pending -> minor refused, patch allowed
p = ledger([R("v0.5.0", True), R("v0.5.1", True), R("v0.5.2", True)])
rows = bc.read_rows(p)
v = bc.verdict(rows, "0.5.2")
ok(v["verdict"] == "REFUSED" and v["is_minor_bump"] and v["device_pending_count"] == 3,
   f"3 pending did not refuse a minor bump: {v}")
ok(bc.verdict(rows, "0.5.2", "0.5.3")["verdict"] == "allowed", "patch bump was refused")
ok(bc.verdict(rows, "0.5.2", "0.6.0")["verdict"] == "REFUSED", "explicit minor target sliped through")

# 2 pending -> allowed (limit is >= 3)
p2 = ledger([R("v0.5.0", True), R("v0.5.1", True), R("v0.5.2", False)])
ok(bc.verdict(bc.read_rows(p2), "0.5.2")["verdict"] == "allowed", "2 pending refused (limit drift)")
ok(bc.verdict(bc.read_rows(p2), "0.5.2")["device_pending_count"] == 2, "pending count wrong")

# clean ledger -> allowed with an empty pending list
p3 = ledger([R("v0.5.0", False)])
v3 = bc.verdict(bc.read_rows(p3), "0.5.0")
ok(v3["verdict"] == "allowed" and v3["device_pending"] == [], f"clean ledger: {v3}")

# a ledger without the machine block must fail LOUD, never silently allow
bad = Path(tempfile.mkdtemp(prefix="mfledger-")) / "nolock.md"
bad.write_text("# no machine block here\n", encoding="utf-8")
try:
    bc.read_rows(bad)
    fails.append("ledger without an mfledger block was accepted")
except RuntimeError:
    pass

# the OWNER'S version ledger, when configured (MF_VIS_LEDGER or the default
# path), must parse and must currently be refusing — skipped silently when
# no ledger exists on this machine (ledger is owner-side data, not repo data).
_ledger = bc.ledger_path()
if _ledger.exists():
    try:
        real = bc.read_rows()
        stamped = bc.stamped_version(bc.Path(bc.__file__).resolve().parent.parent)
        ok(len(real) >= 7 and bc.verdict(real, stamped)["verdict"] == "REFUSED",
           "the real ledger is not refusing as expected")
    except Exception as exc:                                 # noqa: BLE001
        fails.append(f"real ledger unreadable: {exc}")

if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL OK — debt-cap battery (3 pending refuses a minor bump, 2 allows, patch always passes, "
      "missing block fails loud; owner-ledger check conditional)")
