"""loopMIDI -> MicroFreak relay with send-log audit (P2 bridge core).

Opens exactly two ports: IN = loopMIDI port (FL MIDI Out targets this),
OUT = the MicroFreak output (name-gated). All CCs/notes/PC are forwarded
verbatim AND appended to the shared send-log so mf_status sees them.
SysEx/pitch-bend/aftertouch are DROPPED by contract (never forwarded).
"""
from __future__ import annotations

import argparse
import sys
import time

import mido

from .transport import (
    SendLog,
    find_port,
)

FORWARD_KINDS = {"control_change": "cc", "note_on": "note", "note_off": "note",
                 "program_change": "program"}


def resolve(name, names):
    """Exact match, else unique partial match (Windows appends dedupe suffixes)."""
    if name in names:
        return name
    hits = [n for n in names if name.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"port {name!r} unresolved among {names!r}")


def _entry(msg):
    if msg.type == "control_change":
        return {"cc": msg.control, "value": msg.value}
    if msg.type in ("note_on", "note_off"):
        return {"note": msg.note, "velocity": msg.velocity}
    return {"program": msg.program}


def relay(loop_port_name, mf_port_name, log, dry=False, max_seconds=None):
    src = mido.open_input(resolve(loop_port_name, mido.get_input_names()))
    dst = None if dry else mido.open_output(resolve(mf_port_name, mido.get_output_names()))
    counts = {"forwarded": 0, "dropped": 0}
    t0 = time.time()
    try:
        while True:
            msgs = list(src.iter_pending())
            if not msgs:
                time.sleep(0.005)
            for msg in msgs:
                kind = FORWARD_KINDS.get(msg.type)
                if kind is None:
                    counts["dropped"] += 1
                    continue
                detail = _entry(msg)
                detail["source"] = "relay"
                log.append(kind, detail)
                if dst is not None:
                    dst.send(msg)
                counts["forwarded"] += 1
            if max_seconds and time.time() - t0 > max_seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        src.close()
        if dst:
            dst.close()
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop-port", default="loopMIDI Port")
    ap.add_argument("--mf-port", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    a = ap.parse_args(argv)
    mf = a.mf_port or find_port(mido.get_output_names())
    if not mf:
        print("no MicroFreak output port; refusing", file=sys.stderr)
        return 2
    log = SendLog(path=SendLog.default_path())
    print(f"relay: {a.loop_port} -> {mf}{' [DRY]' if a.dry else ''} (ctrl-C to stop)")
    counts = relay(a.loop_port, mf, log, dry=a.dry, max_seconds=a.max_seconds)
    print("relay done:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
