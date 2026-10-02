"""smf — Standard MIDI File (format 1) writer for mfpat/1 loops (v0.5).

Pure byte math, no device IO. Track 0: tempo + name + end. Track 1: notes
(ch 0) and optional CC tracks flattened onto the quantize grid. Ticks are
grid-relative (TPQ 1920 from modmath). This is the answer to "get what I
played INTO FL Studio": FL imports type-1 .mid natively; clips.json/loops.json
are our schema and FL cannot read them.
"""
from __future__ import annotations

import struct
from typing import List, Tuple

from modmath import TPQ, ms_to_ticks, step_ms

_META_TEMPO = b"\xff\x51\x03"


def varlen(n: int) -> bytes:
    """MIDI delta-time variable length quantity."""
    if n < 0:
        raise ValueError("delta ticks cannot be negative")
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _track(events: List[Tuple[int, bytes]]) -> bytes:
    """events: (abs_tick, payload). Sorts, deltas, wraps MTrk."""
    body = bytearray()
    last = 0
    for tick, payload in sorted(events, key=lambda e: e[0]):
        tick = max(tick, last)                    # never rewind inside a track
        body += varlen(tick - last) + payload
        last = tick
    body += varlen(0) + b"\xff\x2f\x00"           # end of track
    return b"MTrk" + struct.pack(">L", len(body)) + bytes(body)


def format1(pattern: dict) -> bytes:
    bpm = float(pattern["bpm"])
    if not (10.0 <= bpm <= 999.0):
        raise ValueError("bpm out of domain")
    span_ticks = ms_to_ticks(60000.0 / bpm * 4 * int(pattern["bars"]), bpm)
    meta = [(0, _META_TEMPO + struct.pack(">I", max(1, int(round(60000000.0 / bpm))))[1:]),
            (0, b"\xff\x06\x00")]                 # marker at bar 1
    name = str(pattern.get("name", ""))[:32].encode("ascii", "replace")
    meta.append((0, bytes([0xFF, 0x03, len(name)]) + name))
    track_notes = []
    for nt in pattern.get("notes", []):
        on = ms_to_ticks(float(nt["t_ms"]), bpm)
        off = max(on + 1, ms_to_ticks(float(nt["t_ms"]) + float(nt["dur"]), bpm))
        n = int(nt["note"]) % 128
        track_notes.append((off, bytes([0x80, n, 0])))               # off first at ties
        track_notes.append((on, bytes([0x90, n, max(1, min(127, int(nt.get("vel", 96))))])))
    for tr in pattern.get("cc_tracks", []):
        cc = int(tr["cc"]) % 128
        pts = sorted(tr.get("points", []), key=lambda p: float(p["t_ms"]))
        spb = int(pattern.get("spb", 4))
        steps = int(pattern["bars"]) * 4 * spb
        pi = -1
        for s in range(steps + 1):
            t_ms = s * step_ms(bpm, spb)
            while pi + 1 < len(pts) and float(pts[pi + 1]["t_ms"]) <= t_ms:
                pi += 1
            if pi < 0:
                continue                          # nothing moved yet: no automation
            v = max(0, min(127, int(pts[pi]["v"])))
            track_notes.append((ms_to_ticks(t_ms, bpm), bytes([0xB0, cc, v])))
    return (b"MThd" + struct.pack(">LHHH", 6, 1, 2, TPQ)
            + _track(meta) + _track(track_notes))
