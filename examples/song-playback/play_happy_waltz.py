"""Happy Birthday — 4-voice waltz arrangement for Arturia MicroFreak (direct USB).

Run with any python that has mido installed (mf-mcp venv):
  <venv-with-mido>/Scripts/python.exe play_happy_waltz.py

Arrangement law: docs/06-song-from-request.md
"""
import mido, time

TEMPO = 108.0
B = 60/TEMPO
name = [n for n in mido.get_output_names() if "microfreak" in n.lower()][0]
out = mido.open_output(name)
print("playing on", repr(name))

G4, A4, B4, C5, D5, E5, F5, G5 = 67, 69, 71, 72, 74, 76, 77, 79
# ---- voice 1: melody, canonical lines (verified against abc-notation score) ----
MEL = [
    (0.0, G4, 0.5, 100), (0.5, G4, 0.5, 100), (1.0, A4, 1.0, 108), (2.0, G4, 1.0, 100),
    (3.0, C5, 1.0, 110), (4.0, B4, 2.0, 105),
    (6.0, G4, 0.5, 100), (6.5, G4, 0.5, 100), (7.0, A4, 1.0, 108), (8.0, G4, 1.0, 100),
    (9.0, D5, 1.0, 112), (10.0, C5, 2.0, 105),
    (12.0, G4, 0.5, 102), (12.5, G4, 0.5, 102), (13.0, G5, 1.0, 120), (14.0, E5, 1.0, 108),
    (15.0, C5, 1.0, 105), (16.0, B4, 1.0, 100), (17.0, A4, 2.0, 95),
    (19.0, F5, 0.5, 108), (19.5, F5, 0.5, 108), (20.0, E5, 1.0, 112), (21.0, C5, 1.0, 105),
    (22.0, D5, 1.0, 108), (23.0, C5, 3.0, 115),
]

# ---- voices 2-4: waltz oom-pah-pah ----
CH = {0:"C",1:"C",2:"C", 3:"G7",4:"G7",5:"G7", 6:"C",7:"C",8:"C",
      9:"G7",10:"C",11:"C", 12:"C",13:"C",14:"C", 15:"C",16:"G7",17:"G7",
      18:"F",19:"F",20:"C",21:"C",22:"G7",23:"C",24:"C",25:"C"}
BASS = {0:48, 3:43, 6:48, 9:43, 10:48, 12:48, 15:48, 16:43, 18:41,
        20:48, 22:43, 23:36}
PAIR = {"C": (60, 64), "G7": (59, 65), "F": (60, 65)}  # G7 pair carries the real 7th (F)

ACC = []
for b, ch in CH.items():
    if b in BASS:
        dur = 2.9 if b == 23 else 0.90
        ACC.append((float(b), BASS[b], dur, 80 if b < 12 else 86))
    elif b % 3 in (1, 2) or b in (11, 17):
        p, q = PAIR[ch]
        v = 60 if b < 12 else (64 if b < 20 else 70)
        ACC.append((float(b), p, 0.45, v))
        ACC.append((float(b), q, 0.45, v))

ev = [(t, (d if d >= 3.0 else d * 0.92), n_, v) for (t, n_, d, v) in MEL] + \
     [(t, d, n_, v) for (t, n_, d, v) in ACC]

stream = []
for (s, d, n_, v) in ev:
    stream.append((s, "note_on", n_, v))
    stream.append((s + d, "note_off", n_, 0))
stream.sort(key=lambda e: (e[0], e[1] == "note_off"))

depth = mx = 0
for _, kind, _, _ in stream:
    depth += 1 if kind == "note_on" else -1
    mx = max(mx, depth)
print("max simultaneous voices:", mx)
assert mx <= 4, "arrangement exceeds the 4-voice budget"

try:
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
    time.sleep(0.35)
    t0 = time.perf_counter()
    for (t, kind, n_, v) in stream:
        dt = t0 + t * B - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        out.send(mido.Message(kind, note=n_, velocity=v, channel=0))
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
finally:
    out.close()
print("done, %.1fs" % (stream[-1][0] * B + 1))
