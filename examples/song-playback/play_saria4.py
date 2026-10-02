import mido, time, json

# Revamp: arrange from the ACTUAL Lost Woods BGM ROM arrangement (lostwoods_tracks.json):
# T2 = melody verbatim, T4 = bridge melody, T13 = real bass (F/C pedals, Dm-G-C-A bridge,
# thirds-duet low line), T3 = real harp figure. Tempo from file math ~97 BPM.
# One full cycle 4.6->76.6 beats + fading head tag = the game's own loop point.
TEMPO = 97.0
B = 60/TEMPO
port = [n for n in mido.get_output_names() if "microfreak" in n.lower()][0]
out = mido.open_output(port)
print("playing on", repr(port))

d = json.load(open("lostwoods_tracks.json"))
T0 = 4.2   # cycle window
def take(track, lo, hi, vel, heldvel):
    return [(t - T0, dur, n, heldvel if dur >= 0.7 else vel)
            for (t, dur, n) in d[track] if lo <= t <= hi]

MEL = take("2", 4.2, 77.0, 100, 114) + take("4", 36.0, 41.0, 98, 110)
MEL += take("2", 76.6, 85.2, 88, 100)          # fading head tag
MEL += [(81.0, 4.8, 65, 104)]                   # final held F4
BAS = take("13", 4.2, 77.0, 66, 70) + take("13", 76.6, 85.2, 56, 58)
HRP = take("3", 4.2, 77.0, 48, 52) + take("3", 76.6, 85.2, 40, 42)
BAS += [(81.0, 5.2, 41, 62)]                    # final F bass
HRP += [(81.0, 4.8, 57, 46), (81.0, 4.8, 60, 44)]  # held A3+C4 = Fmaj shimmer close

ev = []
for (t, dur, n, v) in MEL:
    ev.append((t, max(dur, 0.06), n, v))

def fits(t, d):
    lo, hi = t, t + d
    marks = []
    for (s, dd, n, v) in ev:
        marks.append((s, 1)); marks.append((s + dd, -1))
    marks.append((lo, 1)); marks.append((hi, -1))
    marks.sort(key=lambda m: (m[0], m[1]))
    c = mx = 0
    for _, dl in marks:
        c += dl; mx = max(mx, c)
    return mx <= 4

# priority: melody (locked) > bass > harp; later events re-check cheaply
for (t, dur, n, v) in sorted(BAS + HRP, key=lambda e: (-e[3], e[0])):
    if fits(t, max(dur, 0.06)):
        ev.append((t, max(dur, 0.06), n, v))

stream = []
for (t, d, n, v) in ev:
    stream.append((t, "note_on", n, v)); stream.append((t + d, "note_off", n, 0))
stream.sort(key=lambda e: (e[0], e[1] == "note_on"))
depth = mx = 0
for _, kind, _, _ in stream:
    depth += 1 if kind == "note_on" else -1
    mx = max(mx, depth)
print("max voices:", mx, "| events:", len(ev), "| spans %.1f beats" % (stream[-1][0]*1.0))
assert mx <= 4

# last held note of the tag gets a proper long release
tag_end = stream[-1][0]*B + 2
try:
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
    time.sleep(0.3)
    t0 = time.perf_counter()
    for (t, kind, n, v) in stream:
        dt = t0 + t*B - time.perf_counter()
        if dt > 0: time.sleep(dt)
        out.send(mido.Message(kind, note=n, velocity=v, channel=0))
    time.sleep(2.5)
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
finally:
    out.close()
print("done, %.0fs" % (stream[-1][0]*B + 3))
