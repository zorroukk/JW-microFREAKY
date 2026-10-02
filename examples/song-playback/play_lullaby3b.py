import mido, time, json

# v3 — informed by ACTUAL LISTENING (qwen3.8-omni-flash on OST track "04. House")
# + ROM tempo curve (rubato waltz!). Law applied: ONE gentle 3-note harp sweep per
# bar (root + delayed triad), 1.5 octaves under the lead, everything plucked —
# the "triplet of backing plucky notes" the user hears in the song.
port = [n for n in mido.get_output_names() if "microfreak" in n.lower()][0]
out = mido.open_output(port)
print("playing on", repr(port))

CURVE = json.load(open("lull_tempo.json"))   # [(beat, bpm)...] from the ROM file itself
def sec(t):
    acc, prev, bpm = 0.0, 0.0, CURVE[0][1]
    for (tb, newbpm) in CURVE:
        if t <= tb:
            return acc + (t - prev) * 60.0 / bpm
        acc += (tb - prev) * 60.0 / bpm
        prev, bpm = tb, newbpm
    return acc + (t - prev) * 60.0 / bpm

d = json.load(open("lull_zeldaplays_tracks.json"))
# lead: ROM T6 verbatim at the ocarina-file octave (user-approved register)
# v4 ENDING FIX (owner: "leads got very off the tone at the end"). Root cause:
# the ROM's final note leaps to D5 and holds it 5.1 beats — at +12 that was D6,
# the highest AND loudest AND longest note of the whole tune, breaking the
# song's own cadence (statement 1 ends D4→C4→B3→A3 held over the D bass bar).
# Mirror statement 1: keep the bright leap but settle it: D5→C5→B4→A4(soft hold).
# Also: the T1 flourish carried one chromatic A#5 (Bb) — the only off-key note
# in the entire arrangement over that hold. Dropped, along with its high tail.
CAD_T = 43.0
MEL = []
for (t, dur, n) in d["6"]:
    if not (0.5 <= t): continue
    if abs(t - CAD_T) < 1e-6:
        MEL += [(43.0,1.81,74,106),(45.0,0.31,72,100),(45.5,0.29,71,96),
                (46.0,2.15,69,108)]           # D5 -> C5 -> B4 -> A4 held, gentle close
    else:
        MEL.append((t, dur, n + 12, 116 if dur >= 1.2 else 108))

# bar roots: the ROM's own walking bass C-C-C-C-B-Bb-A-D, mapped onto bars of 3 beats
BARS = [(1,"C"),(4,"C"),(7,"C"),(10,"C"),(13,"B"),(16,"Bb"),(19,"A"),(22,"D"),
        (25,"C"),(28,"C"),(31,"C"),(34,"C"),(37,"B"),(40,"Bb"),(43,"A"),(46,"D")]
TRIAD = {"C":(48,(60,64,67)), "B":(47,(59,62,66)), "Bb":(46,(58,62,65)),
         "A":(45,(57,60,64)), "D":(50,(57,62,66))}
ACC = []
for (b, ch) in BARS:
    r, (x, y, z) = TRIAD[ch]
    ACC.append((b + 0.02, 2.3, r, 70))                    # root on the downbeat
    ACC.append((b + 1.02, 0.9, x, 58))                    # sweep begins on beat 2...
    ACC.append((b + 1.18, 0.9, y, 52))                   # ...spread like a harp run
    ACC.append((b + 1.34, 0.9, z, 48))
GLISS = [(t, min(dur,1.0), n, 56) for (t, dur, n) in d["1"] if 43.2 <= t <= 45.2 and 53 < n <= 77]

ev = list(MEL)
def fits(t, du):
    marks = []
    for (s, dd, n, v) in ev:
        marks.append((s, 1)); marks.append((s + dd, -1))
    marks.append((t, 1)); marks.append((t + du, -1))
    marks.sort(key=lambda m: (m[0], m[1]))
    c = mx = 0
    for _, dl in marks:
        c += dl; mx = max(mx, c)
    return mx <= 4
for (t, dur, n, v) in sorted(ACC + GLISS, key=lambda e: (-e[3], e[0])):
    if fits(t, dur):
        ev.append((t, dur, n, v))

stream = []
for (t, dur, n, v) in ev:
    on = sec(t); off = sec(t + dur)
    stream.append((on, "note_on", n, v)); stream.append((off, "note_off", n, 0))
stream.sort(key=lambda e: (e[0], e[1] == "note_on"))
depth = mx = 0
for _, kind, _, _ in stream:
    depth += 1 if kind == "note_on" else -1
    mx = max(mx, depth)
print("max voices:", mx, "| events:", len(ev), "| duration %.1fs" % (stream[-1][0]+3))
assert mx <= 4

try:
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
    time.sleep(0.3)
    t0 = time.perf_counter()
    for (s, kind, n, v) in stream:
        dt = t0 + s - time.perf_counter()
        if dt > 0: time.sleep(dt)
        out.send(mido.Message(kind, note=n, velocity=v, channel=0))
    time.sleep(2.5)
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
finally:
    out.close()
print("done")
