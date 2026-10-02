import mido, time

TEMPO = 70.2
B = 60/TEMPO
name = [n for n in mido.get_output_names() if "microfreak" in n.lower()][0]
out = mido.open_output(name)
print("playing on", repr(name))

CH = {
 "Eb":   (39, (51, 55, 58)),
 "Gm":   (43, (50, 55, 58)),
 "Cm7":  (48, (51, 55, 60)),
 "Bb":   (46, (50, 53, 58)),
 "Ab":   (44, (48, 51, 56)),
 "Bbs4": (46, (51, 53, 58)),
}
SPANS = ([(0,4,"Eb"),(4,4,"Gm"),(8,4,"Cm7"),(12,4,"Bb"),
          (16,4,"Eb"),(20,4,"Gm"),(24,4,"Cm7"),(28,4,"Bb"),      # verse L1
          (32,4,"Eb"),(36,4,"Gm"),(40,4,"Cm7"),(44,4,"Bb"),      # verse L2
          (48,4,"Eb"),(52,4,"Gm"),(56,4,"Cm7"),(60,4,"Bb")]      # verse L3 + payoff
      + [(66,4,"Ab"),(70,4,"Eb"),(74,4,"Bb"),(78,4,"Bbs4"),      # chorus L1
         (82,4,"Ab"),(86,4,"Eb"),(90,4,"Bb"),(94,4,"Bbs4"),      # chorus L2
         (98,4,"Eb"),(102,4,"Ab"),(106,4,"Bb"),(110,4,"Eb")]     # chorus L3 -> resolve
      + [(114,4,"Eb"),(118,4,"Gm"),(122,4,"Cm7"),(126,4,"Bb"),(130,3.5,"Eb")])

Bb4,C5,D5,Eb5,F5,G5,Ab5,Bb5 = 70,72,74,75,77,79,80,82

# ---- VERSE lines 1-3: SAME melody (payoff-line law confirmed). MIDI spacing,
# ---- letter-source pitches (Bb4 chant low, lift to G5 on "try your", sag D->C).
def vline(b):
    return [(b,   Bb4, .45, 92), (b+.5, Bb4, .45, 92), (b+1,  G5,  .95, 98),
            (b+2, G5,  .45, 98), (b+2.5,Eb5, 1.45,102),
            (b+4, Bb4, .45, 92), (b+4.5,Bb4, .45, 92), (b+5,  Eb5, .95, 98),
            (b+6, D5,  .45, 96), (b+6.5,C5,  2.8, 100)]          # shortened sag
MEL = vline(15) + vline(31)
MEL += [(47, Bb4,.45,92), (47.5, Bb4,.45,92), (48, G5,.95,98), (49, G5,.45,98),
        (49.5, Eb5,1.45,102), (51, Eb5,.95,98), (52, D5,.95,96),
        (53, D5,.45,96), (53.5, C5,2.4,100)]
# ---- PAYOFF "Stuck in re-verse": different melody (steps up Bb4 Eb5 F5 G5,
# ---- peaks Bb5 on "verse", slides down to Eb5 into the chorus)
MEL += [(61.0, Bb4,.7, 100), (61.7, Eb5,.7, 104), (62.5, F5,.7, 106),
        (63.2, G5,.8, 110), (64.0, Bb5,1.5,116), (65.5, Eb5,2.2,104)]

# ---- CHORUS: full-scale descent then C->Eb skip-up lift (Pianote+pianodaddy agree).
MEL += [(66.0, Ab5,.7, 114), (66.7, G5,.7, 108), (67.4, F5,1.0,108), (68.4, Eb5,.6, 106),
        (69.0, D5,.5, 104), (69.5, C5,.5, 102), (70.0, Bb4,3.2,105),      # "-home" held
        (73.5, Bb4,.5, 98), (74.0, C5,.5, 100), (74.5, Eb5,1.4,104)]      # "and ig-" over Bbs4
MEL += [(76.5, Ab5,.7, 112), (77.2, G5,.7, 108), (77.9, F5,1.0,108), (78.9, Eb5,.6, 106),
        (79.5, D5,.5, 104), (80.0, C5,.5, 102), (80.5, Bb4,3.2,105),      # "-bones" held
        (84.0, Bb4,.5, 98), (84.5, C5,.5, 100), (85.0, Eb5,1.4,104)]      # "and I"
MEL += [(87.0, Ab5,.7, 112), (87.7, G5,.7, 108), (88.4, F5,1.0,106), (89.4, Eb5,.6, 104),
        (90.0, Ab5,.7, 112), (90.7, G5,.7, 108), (91.4, F5,.6, 104), (92.0, Eb5,2.8,108)]  # "try to fix you"
MEL += [(95.0, Ab5,.7, 112), (95.7, G5,.7, 108), (96.4, F5,.9, 106), (97.3, Eb5,.6, 104),
        (98.0, Ab5,.7, 110), (98.7, G5,.7, 106), (99.4, F5,.6, 104), (100.0, Eb5,3.6,110)] # resolve
MEL += [(104.5, Eb5,.5, 90), (105.0, G5,.4, 88), (105.5, Eb5,1.2, 88)]    # echo flick

def gate(d):
    return d * (0.93 if d >= 2 else 0.88)      # leaner releases = less drag

ev = [(t, gate(d), n, v) for (t, n, d, v) in MEL]
ROLL = [0, 1, 2, 1]
for (s, L, ch) in SPANS:
    root, arp = CH[ch]
    loud = 1 if s >= 66 else 0
    for bs in [s + k*2 for k in range(int(L/2))]:
        ev.append((bs + 0.05, gate(1.75), root, 66 + 10*loud))
    for e in range(int(L)*2):
        note = arp[ROLL[e % 4]]
        ev.append((s + e*0.5 + 0.03, 0.42, note, 48 + 16*loud))

stream = []
for (t, d, n, v) in ev:
    stream.append((t, "note_on", n, v))
    stream.append((t + d, "note_off", n, 0))
stream.sort(key=lambda e: (e[0], e[1] == "note_off"))

depth = mx = 0
for _, kind, _, _ in stream:
    depth += 1 if kind == "note_on" else -1
    mx = max(mx, depth)
print("max simultaneous voices:", mx)
assert mx <= 4

try:
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
    time.sleep(0.3)
    t0 = time.perf_counter()
    for (t, kind, n, v) in stream:
        dt = t0 + t*B - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        out.send(mido.Message(kind, note=n, velocity=v, channel=0))
    time.sleep(2.0)
    out.send(mido.Message("control_change", control=123, value=0, channel=0))
finally:
    out.close()
print("done, %.0fs" % (stream[-1][0]*B + 2.5))
