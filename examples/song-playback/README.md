# examples/song-playback — proven "play me a song" arrangements

Runnable demonstrations of docs/06. Requirements: Python with `mido`
(`pip install mido`), a MicroFreak connected over USB and powered on, and NO
other MIDI path holding the Freak (close the relay / other DAWs).

Run any of them straight:

```
python play_happy_waltz.py      # self-verifies the ≤4 voice law, prints max depth
python play_saria4.py           # verbatim BGM arrangement + greedy allocator (reads lostwoods_tracks.json)
python play_lullaby3b.py        # rubato waltz on the ROM's own tempo curve (reads lull_zeldaplays_tracks.json + lull_tempo.json)
python play_fixyou_half3.py     # arpeggio ballad, first half (~116 s)
python extract_melody.py <file.mid>   # L1: picks vocal/lead track, dumps note@start/dur
```

Each script ends with CC123 (all notes off) even on crash paths. If
`open_output` fails, the collision check is firing — something else holds the
Freak port (docs/08).

| File | Role |
|---|---|
| `play_happy_waltz.py` | the reference arrangement: 3/4 parlor waltz, 1 melody + 1 bass + 2 pair voices |
| `play_saria4.py` | verbatim game-tune arrangement: melody track as lead (+12), bass track as bass, one accompaniment track through the ≤4 allocator, RAW source durations |
| `play_lullaby3b.py` | the full-chain build: piecewise `sec(beat)` integrator over the file's `set_tempo` curve, harp-sweep texture per bar, dry-sim before hardware |
| `play_fixyou_half3.py` | pop ballad: 8th-note roll self-scheduling inside the voice budget, held notes as windows |
| `extract_melody.py` | the L1 tool: auto-picks `vocal`/`melody`/`lead` tracks from a human-made .mid |
| `*.mid` | the original transcribed source sequences (game ROM-midi collections / community files) — inputs for `extract_melody.py` and the track-dump step; the play scripts themselves read the derived `*_tracks.json` dumps, which ship here so everything runs offline |
| `*.json` | measured tempo curves + track-role dumps (the data half of "read it, don't guess it") |

**Private-use note:** the `.mid`/`.json` inputs are transcriptions of
copyright songs for private study of this pipeline. Don't republish this
folder's data files; substitute your own licensed sources when adapting.
