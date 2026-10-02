"""Brute-force MIDI loopback pairing map: which OUTPUT name reaches which INPUT.

Run with any venv that has mido:
  <venv-with-mido>/Scripts/python.exe midi_pairing_map.py

Use when the FL->loopMIDI->relay chain shows zero traffic despite verified
config: if a loopMIDI cable does not even loop to ITSELF here, the Windows
MIDI driver graph is corrupted (port churn / storm) and only a reboot fixes
it. Kill relay/daemon readers first and let the drain pass run: stale
queued backlog (storm-era messages) otherwise masquerades as live pairing.
"""
import time

import mido


def main():
    ins = mido.get_input_names()
    outs = [n for n in mido.get_output_names() if "GS Wavetable" not in n]
    readers = {n: mido.open_input(n) for n in ins}
    time.sleep(0.3)
    for r in readers.values():          # drain pre-queued stale backlog
        list(r.iter_pending())
    tag = {}
    for i, on in enumerate(outs):
        tag[30 + i] = on                # one distinct note number per output
    for note, on in tag.items():
        try:
            o = mido.open_output(on)
        except Exception as e:          # noqa: BLE001
            print("open fail", on, e)
            continue
        o.send(mido.Message("note_on", note=note, velocity=99))
        o.close()
    time.sleep(0.8)
    hits = set()
    for iname, r in readers.items():
        for m in r.iter_pending():
            if m.type == "note_on" and getattr(m, "velocity", 0):
                hits.add((tag.get(m.note, "?"), iname))
    for r in readers.values():
        r.close()
    print("PAIRINGS (out -> in):")
    for src, dst in sorted(hits):
        print("  %-28s -> %s" % (src, dst))
    looped = {src for src, _ in hits}
    for on in outs:
        if on not in looped:
            print("DEAD (no self-loopback):", on)


if __name__ == "__main__":
    main()
