#!/usr/bin/env python
"""FL 21.2 FLP piano-roll dump/patch. Format verified on FL 21.2.2 build 3914.

Usage:
  python flp_notes.py dump  <file.flp>
  python flp_notes.py patch <in.flp> <out.flp> <edits.json>

edits.json: {"records": [{"which": 0, "pos": 2064, "len": 2040}, ...]}
  'which' = 0-based index across all note records (event 224 payloads, file order);
  omitted fields keep their original value. Ticks: 96 = 1 beat.
"""
import json, struct, sys

NOTE_EVENT = 224
REC_W = 24
TPB = 96  # ticks per beat


def parse_events(buf):
    out = []
    i = 0
    n = len(buf)
    while i < n:
        id_ = buf[i]
        i += 1
        if id_ < 64:
            v = buf[i:i + 1]; i += 1
        elif id_ < 128:
            v = buf[i:i + 2]; i += 2
        elif id_ < 192:
            v = buf[i:i + 4]; i += 4
        else:
            size = 0
            shift = 0
            while i < n:
                b = buf[i]; i += 1
                size |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            v = buf[i:i + size]; i += size
        out.append((id_, v))
    if i != n:
        raise ValueError("trailing bytes in event stream — unknown layout, do not patch")
    return out


def load(path):
    data = open(path, "rb").read()
    assert data[:4] == b"FLhd", "not an FLP"
    fmt, chans, ppq = struct.unpack_from("<HHH", data, 8)
    assert data[14:18] == b"FLdt"
    evs = parse_events(data[22:])
    tempo = next((int.from_bytes(v, "little") / 1000.0
                  for _, v in evs if len(v) == 4
                  and int.from_bytes(v, "little") % 1000 == 0
                  and 40000 <= int.from_bytes(v, "little") <= 300000), None)
    ver = next((v.decode("ascii").rstrip("\0")
                for k, v in evs if k == 199 and len(v) < 32), "?")
    recs = []
    for k, v in evs:
        if k == NOTE_EVENT and len(v) % REC_W == 0:
            for j in range(0, len(v), REC_W):
                recs.append(list(struct.unpack_from("<6I", v, j)))
    return data, fmt, chans, ppq, tempo, ver, recs


def show(tempo, recs):
    print(f"tempo={tempo} BPM  notes={len(recs)}")
    for idx, (pos, flags, ln, key, vp, cp) in enumerate(recs):
        b = (lambda t: t / TPB)
        print(f"  [{idx}] key={key:3d} pos={pos:6d} ({b(pos):8.4f}b) "
              f"len={ln:6d} ({b(ln):8.4f}b) end={b(pos + ln):8.4f}b vel={vp & 0xFFFF} flags={flags}")


def main():
    if len(sys.argv) < 3 or (sys.argv[1] == "patch" and len(sys.argv) < 5):
        sys.exit(__doc__)
    mode = sys.argv[1]
    if mode == "dump":
        _, _, _, _, tempo, ver, recs = load(sys.argv[2])
        print(f"FL version {ver}")
        show(tempo, recs)
    elif mode == "patch":
        src, dst, edits_p = sys.argv[2], sys.argv[3], sys.argv[4]
        data, fmt, chans, ppq, tempo, ver, recs = load(src)
        edits = json.load(open(edits_p))["records"]
        for e in edits:
            r = recs[e["which"]]
            if "pos" in e:
                assert 0 <= e["pos"] < 2 ** 31
                r[0] = int(e["pos"])
            if "len" in e:
                assert 1 <= e["len"] < 2 ** 31
                r[2] = int(e["len"])
        # rebuild each note-event payload and swap it byte-identically in size
        chunks = [struct.pack("<6I", *r) for r in recs]
        out = bytearray(data)
        it = iter(chunks)
        nswap = 0
        for k, v in parse_events(data[22:]):
            if k == NOTE_EVENT and len(v) % REC_W == 0:
                # consume the matching number of records; payloads keep order
                cnt = len(v) // REC_W
                new = b"".join(next(it) for _ in range(cnt))
                assert len(new) == len(v)
                i = data.find(v, 22)
                assert data.count(v) == 1 and i >= 0, "payload not unique — refusing"
                out[i:i + len(v)] = new
                nswap += 1
        assert nswap == sum(1 for k, v in parse_events(data[22:])
                            if k == NOTE_EVENT and len(v) % REC_W == 0)
        open(dst, "wb").write(bytes(out))
        _, _, _, _, t3, _, recs3 = load(dst)
        diff = sum(1 for a, b in zip(data, out) if a != b)
        print(f"wrote {dst}: {diff} bytes changed (size {len(out)} == {len(data)})")
        show(t3, recs3)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
