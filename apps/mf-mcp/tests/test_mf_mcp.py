"""Offline tests for mf_mcp: profile resolution, transport safety, rollback units.

All run against MockBackend - zero MIDI leaves the process (and the port-name
gate makes opening anything but a MicroFreak impossible even in live mode).
"""
from __future__ import annotations

import json

import pytest

from mf_mcp.profile import Profile, ProfileError
from mf_mcp.transport import MockBackend, SendLog, TransportError, find_port

PROFILE = Profile.load()


def test_ping_port_finder():
    names = ["Focusrite USB MIDI", "Arturia MicroFreak 1", "FL Studio"]
    assert find_port(names) == "Arturia MicroFreak 1"
    assert find_port(["Microsoft GS"]) is None


def test_profile_shape():
    assert len(PROFILE.params) == 21
    assert PROFILE.resolve("cutoff")[1]["cc"] == 23
    assert PROFILE.resolve("osc_type")[0] == "oscillator_type"  # alias
    assert PROFILE.resolve(" Cutoff ")[0] == "cutoff"  # normalize
    with pytest.raises(ProfileError):
        PROFILE.resolve("does_not_exist")


def test_clamp():
    key, spec = PROFILE.resolve("cutoff")
    assert PROFILE.clamp(key, spec, 999) == spec.get("max", 127)
    assert PROFILE.clamp(key, spec, -5) == spec.get("min", 0)


def test_sendlog_tracks_last_cc():
    log = SendLog(path=None)
    backend = MockBackend(log)
    backend.send_cc(23, 50)
    backend.send_cc(23, 90)
    assert log.last_cc(23) == 90
    assert log.last_cc(83) is None


def test_backend_validation():
    backend = MockBackend(SendLog(path=None))
    with pytest.raises(TransportError):
        backend.send_cc(23, 128)
    with pytest.raises(TransportError):
        backend.send_cc(128, 0)
    with pytest.raises(TransportError):
        backend.send_note(60, 0, 20)  # velocity must be >=1
    with pytest.raises(TransportError):
        backend.program_change(200)


def test_note_emits_on_and_off():
    backend = MockBackend(SendLog(path=None), min_gap_ms=0)
    backend.send_note(60, 20, 20)
    types = [m.type for m in backend.sent]
    assert types == ["note_on", "note_off"]


def test_profile_disclaims_sysex_writes():
    # safety contract: the profile must explicitly NOT claim sysex support
    scope = PROFILE.meta["scope"]
    assert "sysex_write_support" in scope.get("does_not_claim", [])
    assert "documented_live_midi_cc" in scope.get("supports", [])


def test_sendlog_persistence(tmp_path):
    p = tmp_path / "log.jsonl"
    a = SendLog(path=p)
    a.append("cc", {"cc": 23, "value": 90})
    a.append("note", {"note": 60})
    b = SendLog(path=p)  # fresh process simulation
    assert b.last_cc(23) == 90
    assert len(b.snapshot()) == 2


def test_sendlog_skips_malformed_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        "not json\n"
        "{broken\n"
        + json.dumps({"ts": 1, "kind": "cc", "cc": 5, "value": 7})
        + "\n",
        encoding="utf-8",
    )
    b = SendLog(path=p)
    assert b.last_cc(5) == 7
    assert len(b.snapshot()) == 1
