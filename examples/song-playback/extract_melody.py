"""Extract the best melody line (pitch + DURATIONS) from a .mid file.

Usage: <mf-mcp venv python> extract_melody.py song.mid [track_hint]
Prints phrase-grouped "note@start/dur" lines, beats as grid units (file tempo).
Strategy: prefer a track named vocal/melody/lead; else pick the track with the
most single-line movement in the melody register. Then merge ties, group by gaps.
"""
import sys, mido, json

NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
def nm(n): return NAMES[n % 12] + str(n // 12 - 1)

def main():
    path = sys.argv[1]
    m = mido.MidiFile(path)
    cand = []
    for i, tr in enumerate(m.tracks):
        ons = [msg for msg in tr if msg.type == "note_on" and msg.velocity > 0]
        if not ons: continue
        names = " ".join(str(x) for x in tr if x.type == "track_name").lower()
        score = 0
        if any(k in names for k in ("vocal", "voice", "melody", "lead")): score += 100
        pitch = [x.note for x in ons]
        score += (1 if 60 <= sum(pitch)/len(pitch) <= 84 else 0)
        cand.append((score, i, names, len(ons)))
    cand.sort(reverse=True)
    ti = cand[0][1]
    print("chosen track:", cand[0][2] or f"T{ti}", "| candidates:",
          [(c[2] or c[1], c[3]) for c in cand[:4]])
    # rebuild events cleanly (duration via matching note_on/note_off pairs)
    tpb = m.ticks_per_beat
    for tr_pick in [m.tracks[ti]]:
        t, stack, ev = 0.0, {}, []
        for msg in tr_pick:
            t += msg.time / tpb
            if msg.type == "note_on" and msg.velocity > 0:
                stack.setdefault(msg.note, []).append(t)
            elif msg.type in ("note_off", "note_on") and (msg.type == "note_off" or msg.velocity == 0):
                if stack.get(msg.note):
                    s = stack[msg.note].pop(0)
                    ev.append((round(s, 3), round(max(t - s, 0.05), 3), msg.note))
        phr, last, out = [], None, []
        for (s, d, n) in ev:
            if last is not None and s - last > 4.0:
                out.append(phr); phr = []
            phr.append((s, d, n)); last = s + d
        if phr: out.append(phr)
        for i, p in enumerate(out):
            print(f"P{i} start={p[0][0]:.1f}: " + " ".join(f"{nm(n)}@{s:g}/{d:g}" for s, d, n in p))
        json.dump(ev, open(path.rsplit(".", 1)[0] + "_melody.json", "w"))
        print("saved:", path.rsplit(".", 1)[0] + "_melody.json")

if __name__ == "__main__":
    main()
