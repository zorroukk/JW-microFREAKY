"""Preset body decoding for Arturia MicroFreak (firmware 5).

Clean-room reimplementation of format facts documented in:
  - kmorrill/freakout docs/microfreak-sysex.md (MIT)
  - francoisgeorgy/microfreak-reader
  (ManageFreak's GPL implementation was studied for the algorithm shape only;
  no code copied. Same facts, written from scratch in Python.)

The 4672-byte SysEx body unpacks 8->7 bit into 4088 bytes. FW5 packs that as a
self-describing stream of group markers and tagged fields:

  group marker:  '@' 0x23 <3 ASCII bytes>          (first group: 0x23 + 3 bytes)
  field:         (0x40 + name_len) <name> 'c' <meta uint8> <value uint16le>

Value semantics (per freakout corpus of 320 FW5 presets):
  - metadata_scaled_integer: raw / 32767 * metadata        (engines, switches)
  - unsigned_normalized:     raw / 32767 -> 0..1           (cutoff, rates...)
  - bipolar_normalized:      s16 / 32767 -> -1..1          (Co1..Co7 matrix cells)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DATALEN = 4672
UNPACKED_LEN = 4088  # 4672 / 8 * 7

# Runtime index is 1..22; index 0 reserved (freakout + ManageFreak agree).
ENGINES_22: List[Optional[str]] = [
    None,
    "BasicWaves", "SuperWave", "Wavetable", "Harmo", "KarplusStr", "V.Analog",
    "Waveshaper", "Two Op. FM", "Formant", "Chords", "Speech", "Modal",
    "Noise", "Vocoder", "Bass", "SawX", "Harm", "WaveUser", "Sample",
    "Scan Grains", "Cloud Grains", "Hit Grains",
]

CATEGORIES = [
    "Bass", "Brass", "Keys", "Lead", "Organ", "Pad",
    "Percussion", "Sequence", "SFX", "Strings", "Template", "Vocoder",
]

# Normalized 0..1 fields (facts from freakout docs; group.name keys)
NORMALIZED = {
    "VCO.Param1", "VCO.Param2", "VCO.Param3",
    "VCF.Cutoff", "VCF.Reso",
    "EG1.RiseLvl", "EG1.RiseSlp", "EG1.FallLvl", "EG1.Hold",
    "EG1.FallSlp", "EG1.Amount", "Kbd.Glide",
    "Arp.Rate", "Arp.Spice", "Arp.Dice", "LFO.Rate",
    "EG2.Attack", "EG2.DecRel", "EG2.Sustain",
    "Gen.Volume", "Gen.UniSprd",
}

BIPOLAR_GROUPS = {"Co1", "Co2", "Co3", "Co4", "Co5", "Co6", "Co7"}

# CC the panel visualizer uses for each tagged field (where one exists).
# 7-bit CC domain: normalized 0..1 -> 0..127; bipolar -1..1 -> 0..127 centered 64;
# engine index 1..22 -> CC9 domain 0..127 (panel maps 0..127 over the engine list).
KEY_TO_CC = {
    "VCO.Type": 9, "VCO.Wave": 10, "VCO.Param1": 12, "VCO.Param2": 13,
    "VCF.Cutoff": 23, "VCF.Reso": 83,
    "EG1.RiseLvl": 102, "EG1.RiseSlp": 103, "EG1.Hold": 28, "EG1.Amount": 24,
    "EG2.Attack": 105, "EG2.Sustain": 29, "EG2.DecRel": 106,
    "Kbd.Glide": 5, "Arp.Rate": 91, "Arp.Spice": 2, "LFO.Rate": 93,
    "Gen.Volume": None,  # no CC
}


def unpack8to7(data: bytes) -> bytes:
    """8->7 bit unpack: every 8 wire bytes (bitmap + 7 data) -> 7 real bytes."""
    out = bytearray(len(data) // 8 * 7)
    for block in range(len(data) // 8):
        base = block * 8
        bitmap = data[base]
        ob = block * 7
        for i in range(7):
            out[ob + i] = data[base + 1 + i] | (0x80 if (bitmap >> i) & 1 else 0)
    return bytes(out)


def parse_structured(data: bytes) -> List[Dict[str, Any]]:
    """Tagged FW5 field stream -> [{key, group, name, meta, raw, s16, value, kind}].

    Returns [] for a stream without recognizable tags (legacy FW<=4 preset) —
    never raises; callers treat decode failures as 'preset not parsed'.
    """
    if len(data) != DATALEN:
        return []
    u = unpack8to7(data)
    fields: List[Dict[str, Any]] = []
    group: Optional[str] = None
    pos = 0
    n = len(u)
    while pos < n:
        gb: Optional[bytes] = None
        if pos == 0 and u[pos] == 0x23:
            gb = u[pos + 1:pos + 4]
            pos += 4
        elif u[pos] == 0x40 and pos + 1 < n and u[pos + 1] == 0x23:
            gb = u[pos + 2:pos + 5]
            pos += 5
        if gb is not None:
            if len(gb) != 3:
                break
            group = "".join(chr(c) for c in gb)
            continue
        name_len = u[pos] - 0x40
        end = pos + 1 + name_len
        if not group or name_len < 1 or name_len > 31 or end + 4 > n or u[end] != 0x63:
            break
        name = "".join(chr(c) for c in u[pos + 1:end])
        meta = u[end + 1]
        raw = u[end + 2] | (u[end + 3] << 8)
        s16 = raw - 0x10000 if raw >= 0x8000 else raw
        key = f"{group}.{name}"
        f: Dict[str, Any] = {"key": key, "group": group, "name": name,
                             "meta": meta, "raw": raw, "s16": s16}
        _interpret(f)
        fields.append(f)
        pos = end + 4
    return fields


def _interpret(f: Dict[str, Any]) -> None:
    """Attach normalized 'value' + kind per freakout value kinds."""
    key = f["key"]
    if key == "VCO.Type":
        meta = f["meta"] or 22
        idx = round(f["raw"] * meta / 32767)
        f["kind"] = "engine"
        f["index"] = idx
        f["text"] = (ENGINES_22[idx] if idx < len(ENGINES_22) else f"Engine {idx}") if idx else "?"
        f["value"] = idx / 22.0
    elif key in NORMALIZED:
        f["kind"] = "percent"
        f["value"] = f["raw"] / 32767.0
    elif f["group"] in BIPOLAR_GROUPS:
        f["kind"] = "bipolar"
        f["value"] = f["s16"] / 32767.0
    elif f["meta"] > 0 and f["group"] in ("VCO", "VCF", "EG1", "EG2", "Arp", "LFO", "Kbd", "Gen"):
        # metadata_scaled_integer: integer 0..meta for enumerated/bounded fields
        idx = round(f["raw"] * f["meta"] / 32767)
        f["kind"] = "scaled"
        f["index"] = idx
        f["value"] = idx / f["meta"]
    else:
        f["kind"] = "raw"
        f["value"] = f["raw"]


def to_cc_state(fields: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Map a decoded preset's fields onto the visualizer's cc dict shape.

    Values are projected to 0..127 (the panel's native domain). CC9 keeps the
    freakout engine-domain mapping (idx*127/21) so the header engine text and
    the panel engine chip agree.
    """
    cc: Dict[int, Dict[str, Any]] = {}
    for f in fields:
        ccn = KEY_TO_CC.get(f["key"])
        if ccn is None:
            continue
        if f["kind"] == "engine":
            # engines 1..22 spread over CC 0..127 (21 steps) — must invert the
            # panel's CC9 -> engine mapping in the frontend exactly.
            v = round((max(1, min(22, f.get("index", 1))) - 1) * 127 / 21)
        elif f["kind"] in ("percent", "scaled"):
            v = round(f["value"] * 127)
        elif f["kind"] == "bipolar":
            v = round(64 + f["value"] * 63)
        else:
            continue
        cc[ccn] = {"value": max(0, min(127, v)), "source": "preset", "name": f["key"]}
    return cc


# Matrix source rows as named inside each Co column group (verified against the
# 448-preset firmware-5 corpus): Cycling-Env, Env2, LFO, expr (pressure/vel), key.
MATRIX_ROWS = ["EG1", "EG2", "LFO", "Xpr", "Key"]
MATRIX_ROWS_LABEL = ["Cycling Env", "ENV", "LFO", "Pressure/Vel", "Key/Arp/Seq"]


def matrix_from_fields(fields: List[Dict[str, Any]]) -> List[List[Any]]:
    """Mod matrix as 5 source rows x 7 dest columns, amounts -64..64 (display domain).

    The MF stores 7 destination COLUMNS (Co1..Co7), each with 5 bipolar cells
    named EG1/EG2/LFO/Xpr/Key (the sources).
    """
    grid = [[0] * 7 for _ in range(5)]
    row_ix = {n: i for i, n in enumerate(MATRIX_ROWS)}
    for f in fields:
        if f["group"] not in BIPOLAR_GROUPS or f["kind"] != "bipolar":
            continue
        col = BIPOLAR_GROUPS_ORDER.index(f["group"])
        row = row_ix.get(f["name"])
        if row is not None:
            grid[row][col] = round(f["value"] * 64)
    return grid


BIPOLAR_GROUPS_ORDER = ["Co1", "Co2", "Co3", "Co4", "Co5", "Co6", "Co7"]


# ---------------------------------------------------------------------------
# .mfp text container (Boost serialization::archive, same bytes MCC writes)
# ---------------------------------------------------------------------------
def parse_mfp(raw: bytes) -> Dict[str, Any]:
    """Parse a .mfp (text Boost archive) into {version,name,category,init,p1,data,characteristics}.

    Raises ValueError on anything that isn't a MicroFreak single-preset archive.
    """
    # Boost text archives serialize std::string as '<len> <exact chars>' —
    # names may legally contain double/trailing spaces ('93s  Rave', 'Ixos '),
    # so read length-prefixed strings by CHARACTERS, not by whitespace tokens.
    text = raw.decode("latin-1", "replace").replace("\r\n", "\n").strip()
    pos = 0

    def skip_ws() -> None:
        nonlocal pos
        while pos < len(text) and text[pos] in " \t":
            pos += 1

    def word() -> str:
        nonlocal pos
        skip_ws()
        start = pos
        while pos < len(text) and text[pos] not in " \t":
            pos += 1
        if pos == start:
            raise ValueError("preset file truncated")
        return text[start:pos]

    def num() -> int:
        try:
            return int(word())
        except ValueError:
            raise ValueError("expected an integer token")

    if num() != 22 or word() != "serialization::archive":
        raise ValueError("not a Boost serialization archive")
    if num() != 10 or num() != 0 or num() != 4:
        raise ValueError("unexpected archive version fields")
    ver_len = num()
    skip_ws()
    if pos + ver_len > len(text):
        raise ValueError("preset file truncated")
    version = text[pos:pos + ver_len]
    pos += ver_len
    name_len = num()
    name = ""
    if name_len > 0:
        skip_ws()
        if pos + name_len > len(text):
            raise ValueError("preset file truncated")
        name = text[pos:pos + name_len]
        pos += name_len
        # Tolerate writers that under-declare the length (seen in a community
        # "test_empty" slot generator): if we stopped mid-word, finish the word.
        if pos < len(text) and text[pos] not in " \t\n":
            end = pos
            while end < len(text) and text[end] not in " \t\n":
                end += 1
            name += text[pos:end]
            pos = end
    category = num()
    if num() != 0 or num() != 0:
        raise ValueError("expected p0 fields not found")
    chars_len = num()
    if chars_len != 18:
        raise ValueError("unexpected characteristics field")
    bitset = word()
    characteristics = bitset_to_characteristics(bitset)
    init = num()
    if num() != 0:
        raise ValueError("expected p4 field not found")
    p1 = num()
    datalen = num()
    data = bytearray(datalen)
    for k in range(datalen):
        data[k] = num() & 0xFF
    return {"version": version, "name": name, "category": category,
            "category_name": CATEGORIES[category] if 0 <= category < len(CATEGORIES) else "",
            "init": init, "p1": p1, "data": bytes(data),
            "characteristics": characteristics}


# Leftmost bit = Soundtrack ... rightmost bit = Acid (freakout docs list them
# alphabetically right-to-left, so this array is the reversed alphabet).
CHARACTERISTICS = [
    "Soundtrack", "Soft", "Simple", "Short", "Quiet", "Noise", "Long", "Hard",
    "Funky", "Ensemble", "Digital", "Dark", "Complex", "Bright", "Bizarre",
    "Ambient", "Aggressive", "Acid",
]


def bitset_to_characteristics(bitset: str) -> List[str]:
    if len(bitset) != 18 or any(c not in "01" for c in bitset):
        return []
    return [n for bit, n in zip(bitset, CHARACTERISTICS) if bit == "1"]

# ---------------------------------------------------------------------------
# Mat.Assign1..3 destination IDs -> named targets.
# A destination ID is the op-41 live-word address of the target field. Table
# transcribed from kmorrill/freakout src/minifreak_patch/microfreak_live_map.py
# (MIT), itself hardware-correlated on FW 5.0.0.36 with readback + exact
# restore proofs. All aliases map back to the same field name.
# ---------------------------------------------------------------------------
_LIVE_WORDS: Dict[str, List[int]] = {
    "Arp.Dice": [0x030F, 0x0407], "Arp.Dir": [0x0408], "Arp.Div": [0x030A, 0x0402],
    "Arp.Enable": [0x020F, 0x0308, 0x0400], "Arp.Range": [0x0309, 0x0401],
    "Arp.Rate": [0x030B, 0x0403], "Arp.SeqOn": [0x0409], "Arp.Spice": [0x030E, 0x0406],
    "Arp.Swing": [0x030C, 0x0404], "Arp.Sync": [0x030D, 0x0405],
    "Co1.EG1": [0x060D, 0x0906, 0x0A00], "Co1.EG2": [0x060E, 0x0907, 0x0A01],
    "Co1.Key": [0x090A, 0x0A04], "Co1.LFO": [0x060F, 0x0908, 0x0A02], "Co1.Xpr": [0x0909, 0x0A03],
    "Co2.EG1": [0x090C, 0x0A06, 0x0B00], "Co2.EG2": [0x090D, 0x0A07, 0x0B01],
    "Co2.Key": [0x0A0A, 0x0B04], "Co2.LFO": [0x090E, 0x0A08, 0x0B02], "Co2.Xpr": [0x090F, 0x0A09, 0x0B03],
    "Co3.EG1": [0x0A0C, 0x0B06, 0x0C00], "Co3.EG2": [0x0A0D, 0x0B07, 0x0C01],
    "Co3.Key": [0x0B0A, 0x0C04], "Co3.LFO": [0x0A0E, 0x0B08, 0x0C02], "Co3.Xpr": [0x0A0F, 0x0B09, 0x0C03],
    "Co4.EG1": [0x0B0C, 0x0C06, 0x0D00], "Co4.EG2": [0x0B0D, 0x0C07, 0x0D01],
    "Co4.Key": [0x0C0A, 0x0D04], "Co4.LFO": [0x0B0E, 0x0C08, 0x0D02], "Co4.Xpr": [0x0B0F, 0x0C09, 0x0D03],
    "Co5.EG1": [0x0C0C, 0x0D06, 0x0E00], "Co5.EG2": [0x0C0D, 0x0D07, 0x0E01],
    "Co5.Key": [0x0D0A, 0x0E04], "Co5.LFO": [0x0C0E, 0x0D08, 0x0E02], "Co5.Xpr": [0x0C0F, 0x0D09, 0x0E03],
    "Co6.EG1": [0x0D0C, 0x0E06, 0x0F00], "Co6.EG2": [0x0D0D, 0x0E07, 0x0F01],
    "Co6.Key": [0x0E0A, 0x0F04], "Co6.LFO": [0x0D0E, 0x0E08, 0x0F02], "Co6.Xpr": [0x0D0F, 0x0E09, 0x0F03],
    "Co7.EG1": [0x0E0C, 0x0F06, 0x1000], "Co7.EG2": [0x0E0D, 0x0F07, 0x1001],
    "Co7.Key": [0x0F0A, 0x1004], "Co7.LFO": [0x0E0E, 0x0F08, 0x1002], "Co7.Xpr": [0x0E0F, 0x0F09, 0x1003],
    "EG1.Amount": [0x0109, 0x0206], "EG1.FallLvl": [0x0106, 0x0203, 0x100D],
    "EG1.FallSlp": [0x0108, 0x0205, 0x100F], "EG1.Hold": [0x0107, 0x0204, 0x100E],
    "EG1.Mode": [0x0103, 0x0200, 0x100A], "EG1.RiseLvl": [0x0104, 0x0201, 0x100B],
    "EG1.RiseSlp": [0x0105, 0x0202, 0x100C],
    "EG2.Attack": [0x000B, 0x0601], "EG2.DecRel": [0x000C, 0x0602], "EG2.Legato": [0x000E, 0x0604],
    "EG2.Mode": [0x000A, 0x0600], "EG2.Sustain": [0x000D, 0x0603],
    "Gen.Chord": [0x050C, 0x0707], "Gen.ChOffs1": [0x050D, 0x0708], "Gen.ChOffs2": [0x050E, 0x0709],
    "Gen.ChOffs3": [0x050F, 0x070A], "Gen.Parafon": [0x040F, 0x0505, 0x0700],
    "Gen.PolyCnt": [0x0507, 0x0702], "Gen.PrstVol": [0x0508, 0x0703], "Gen.UniCnt": [0x050B, 0x0706],
    "Gen.UniSprd": [0x050A, 0x0705], "Gen.Volume": [0x0509, 0x0704],
    "Kbd.GlMode": [0x010E, 0x020B, 0x0304], "Kbd.Glide": [0x010A, 0x0207, 0x0300],
    "Kbd.Hold": [0x010D, 0x020A, 0x0303], "Kbd.Octave": [0x010B, 0x0208, 0x0301],
    "Kbd.PresAmp": [0x010F, 0x020C, 0x0305], "Kbd.Root": [0x020E, 0x0307],
    "Kbd.Scale": [0x020D, 0x0306], "Kbd.Veloc": [0x010C, 0x0209, 0x0302],
    "LFO.Div": [0x040B, 0x0501], "LFO.Rate": [0x040C, 0x0502], "LFO.Retrig": [0x040E, 0x0504],
    "LFO.Shape": [0x040A, 0x0500], "LFO.Sync": [0x040D, 0x0503],
    "Seq.GateLen": [0x070E, 0x1202], "Seq.XiceRst": [0x070D, 0x1201], "Seq.Length": [0x070C, 0x1200],
    "Seq.Smooth1": [0x070F, 0x1203], "Seq.Smooth2": [0x1204], "Seq.Smooth3": [0x1205],
    "Seq.Smooth4": [0x1206],
    "VCF.Cutoff": [0x0101, 0x0F0E, 0x1008], "VCF.Reso": [0x0102, 0x0F0F, 0x1009],
    "VCF.Type": [0x0100, 0x0F0D, 0x1007],
    "VCO.BendRng": [0x0007], "VCO.Param1": [0x0001], "VCO.Param2": [0x0003], "VCO.Param3": [0x0005],
    "Voc.HissMod": [0x1207, 0x1300], "Voc.HissVol": [0x1208, 0x1301],
}

DEST_BY_ID: Dict[int, str] = {}
for _name, _addrs in _LIVE_WORDS.items():
    for _a in _addrs:
        DEST_BY_ID.setdefault(_a, _name)


def assign_labels(fields: List[Dict[str, Any]]) -> List[str]:
    """Human names for Mat.Assign1..3 destination IDs ('' when unknown)."""
    out = []
    for target in ("Assign1", "Assign2", "Assign3"):
        val = next((f.get("value") for f in fields
                    if f.get("group") == "Mat" and f.get("name") == target), None)
        out.append(DEST_BY_ID.get(int(val), "") if val is not None else "")
    return out
