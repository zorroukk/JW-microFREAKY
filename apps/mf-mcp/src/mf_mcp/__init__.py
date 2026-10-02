"""mf_mcp - MCP server for Arturia MicroFreak.

Architecture note (differs from fl_studio_mcp on purpose): the MicroFreak
has NO read-back over standard MIDI - it never answers CC reads (no MIDI 2.0
on this unit per lane docs), and sends nothing unsolicited (probe 2026-09-06:
0 messages in a 1.5 s passive window). Therefore device state is modeled
from an append-only send log + explicit preset reload as the rollback unit.
"""
__version__ = "0.1.0"
