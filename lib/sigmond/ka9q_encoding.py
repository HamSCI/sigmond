"""ka9q-radio encoding utilities — shared by the Receiver Channels TUI
screen and by per-client receiver_channels parsers.

The numeric values mirror ka9q-python's ``Encoding`` enum
(S16LE=1, S16BE=2, OPUS=3, F32=4, AX25=5, F32BE=8).  We duplicate
the table here because the sigmond TUI runs in its own venv without
ka9q-python guaranteed importable from worker context, and client
parsers loaded via ``importlib.util.spec_from_file_location`` need a
stable place to look up encoding names without taking a dependency
on the TUI module.
"""

from __future__ import annotations

from typing import Optional

# Numeric → friendly name (for rendering live channels).
ENCODING_NAMES: dict[int, str] = {
    1: "s16le",
    2: "s16be",
    3: "opus",
    4: "f32",
    5: "ax25",
    8: "f32be",
}

# Friendly name (case-insensitive, plus a few common aliases) → numeric.
# Used to compare what a client config declares against what the live
# channel reports.
ENCODING_INTS: dict[str, int] = {
    "s16le": 1,
    "s16be": 2,
    "opus":  3,
    "f32":   4,
    "f32le": 4,
    "float": 4,     # wspr-recorder config alias for f32
    "ax25":  5,
    "f32be": 8,
}


def decode_encoding(enc: int | None) -> str:
    """Numeric encoding → friendly name (or raw integer string fallback)."""
    if enc is None:
        return "?"
    return ENCODING_NAMES.get(int(enc), str(enc))


def encoding_to_int(enc: str | None) -> Optional[int]:
    """Friendly name (case-insensitive) → numeric, or None if unknown.

    Returning None is meaningful: client parsers use it to signal
    "no encoding declared in this config", and the TUI degrades to
    "match any encoding" rather than filtering channels out.
    """
    if not enc:
        return None
    return ENCODING_INTS.get(str(enc).strip().lower())


__all__ = [
    "ENCODING_NAMES", "ENCODING_INTS",
    "decode_encoding", "encoding_to_int",
]
