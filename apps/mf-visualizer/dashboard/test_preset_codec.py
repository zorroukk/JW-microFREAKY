"""Tests for preset_codec — vectors from ManageFreak run-tests.js (hardware-verified format)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from preset_codec import unpack8to7, parse_structured, parse_mfp, to_cc_state, bitset_to_characteristics, ENGINES_22

ok = 0
fail = 0

def check(name, cond):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name)

# --- unpack8to7 vector (ManageFreak test 'round-trip .mfp con byte >127'):
# serialize packs byte i as (i*7+13)&0xff. Verify unpack of a known block:
# block: bitmap=0xff -> all 7 MSBs set, data 0x00..0x06 -> out 0x80..0x86
check("unpack all-MSBs", unpack8to7(bytes([0xff,0,1,2,3,4,5,6])) == bytes([0x80,0x81,0x82,0x83,0x84,0x85,0x86]))
# bitmap=0x05 -> bits 0 and 2 set -> out[0]|=0x80, out[2]|=0x80
check("unpack sparse bitmap", unpack8to7(bytes([0x05, 0x11,0x22,0x33,0x44,0x55,0x66,0x77])) == bytes([0x91,0x22,0xB3,0x44,0x55,0x66,0x77]))
# length not multiple of 8: tail ignored
check("unpack len%8", len(unpack8to7(bytes(9))) == 7)

# --- parse_structured on a synthetic tagged body: build unpacked, pack 7->8
def pack7to8(u: bytes) -> bytes:
    out = bytearray()
    for b in range(0, len(u), 7):
        blk = u[b:b+7]
        bitmap = 0
        data = []
        for i, v in enumerate(blk):
            if v & 0x80: bitmap |= 1 << i
            data.append(v & 0x7f)
        data += [0] * (7 - len(blk))
        out.append(bitmap); out.extend(data)
    return bytes(out)

# group '@#VCO' then field Wave len=4 meta=100 raw=16384 ; Co1 col group with bipolar
u = bytearray(4088)
pos = 0
u[pos:pos+5] = b'@#VCO'; pos += 5
name = b'Wave'
u[pos] = 0x40 + len(name); u[pos+1:pos+1+len(name)] = name; u[pos+1+len(name)] = 0x63
u[pos+2+len(name)] = 100
raw = 16384
u[pos+3+len(name)] = raw & 0xff; u[pos+4+len(name)] = raw >> 8
pos += 5 + len(name)
u[pos:pos+5] = b'@#Co1'; pos += 5
name = b'C3'
u[pos] = 0x40 + len(name); u[pos+1:pos+1+len(name)] = name; u[pos+1+len(name)] = 0x63
u[pos+2+len(name)] = 22
s = -16384 & 0xffff
u[pos+3+len(name)] = s & 0xff; u[pos+4+len(name)] = s >> 8
body = pack7to8(bytes(u))
check("packed to 4672", len(body) == 4672)

fields = parse_structured(body)
check("two fields parsed", len(fields) == 2)
f0 = fields[0]; f1 = fields[1]
check("field0 key/meta/raw", f0["key"] == "VCO.Wave" and f0["meta"] == 100 and f0["raw"] == 16384)
check("field0 percent-ish", abs(f0["value"] - 0.5) < 1e-3)
check("field1 s16 negative", f1["s16"] == -16384)
check("field1 bipolar", abs(f1["value"] + 0.5) < 1e-3)

# CC projection: VCO.Wave cc10 = round(.5*127)=64 ; Co1 not in KEY_TO_CC -> skipped
cc = to_cc_state(fields)
check("cc projection", cc.get(10, {}).get("value") == 64 and len(cc) == 1)

# engine mapping: VCO.Type meta=22 raw=16384 -> idx = round(16384*22/32767)=11 -> Speech
u2 = bytearray(4088)
u2[0:5] = b'@#VCO'
name = b'Type'
p = 5
u2[p] = 0x40 + len(name); u2[p+1:p+1+len(name)] = name; u2[p+1+len(name)] = 0x63
u2[p+2+len(name)] = 22
raw = 16384
u2[p+3+len(name)] = raw & 0xff; u2[p+4+len(name)] = raw >> 8
fs = parse_structured(pack7to8(bytes(u2)))
check("engine idx", fs[0].get("index") == 11 and fs[0]["text"] == "Speech")

# --- parse_mfp: reference vector copied from ManageFreak hardware-verified test
data = bytearray(4672)
data[0] = 0xff; data[1] = 0x80; data[2] = 0x7f
text = '22 serialization::archive 10 0 4 3 174 4 Test 2 0 0 18 000000000000000000 0 0 99 4672'
for i in range(4672):
    b = data[i]
    text += ' ' + str(b - 256 if b > 127 else b)
text += '\n'
p = parse_mfp(text.encode())
check("mfp name/category", p["name"] == "Test" and p["category"] == 2 and p["category_name"] == "Keys")
check("mfp p1", p["p1"] == 99)
check("mfp bytes>127", p["data"][0] == 0xff and p["data"][1] == 0x80 and p["data"][2] == 0x7f)
check("mfp version", p["version"] == "174")

# empty-name + init=1 vector
text2 = '22 serialization::archive 10 0 4 3 174 0  0 0 0 18 000000000000000000 1 0 5 4672' + ' 0' * 4672 + '\n'
p2 = parse_mfp(text2.encode())
check("mfp empty name", p2["name"] == '' and p2["init"] == 1 and p2["p1"] == 5)

# init empty (datalen=0)
p3 = parse_mfp('22 serialization::archive 10 0 4 3 174 4 Init 0 0 0 18 000000000000000000 1 0 0 0\n'.encode())
check("mfp init empty", p3["name"] == "Init" and p3["init"] == 1 and len(p3["data"]) == 0)

# characteristics bitset: Soundtrack leftmost, Acid rightmost
bs = '1' + '0'*16 + '1'
chars = bitset_to_characteristics(bs)
check("char bitset", chars == ["Soundtrack", "Acid"])

# names are length-prefixed EXACT chars: internal double space, trailing space
t4 = '22 serialization::archive 10 0 4 3 174 11 My  Nice Pad 2 0 0 18 000000000000000000 0 0 0 4672' + ' 0' * 4672 + '\n'
check("mfp double-space name", parse_mfp(t4.encode())["name"] == "My  Nice Pad")
t5 = '22 serialization::archive 10 0 4 3 174 5 Ixos  3 0 0 18 000000000000000000 0 0 0 4672' + ' 0' * 4672 + '\n'
check("mfp trailing-space name", parse_mfp(t5.encode())["name"] == "Ixos ")
# under-declared length (sloppy community writer) -> finish the word, don't crash
t6 = '22 serialization::archive 10 0 4 3 174 6 test_empty-A449 1 0 0 18 000000000000000000 1 0 0 0\n'
check("mfp under-declared name", parse_mfp(t6.encode())["name"] == "test_empty-A449")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
