"""TCS guide-port wire format. See Wireformat.md.

The TCS accepts a 6-byte ASCII frame:

    G <xx> <yy> <CR>

Where xx and yy are signed offsets in 0.05" steps. The encoded value n
in 00..99 decodes as:

    n in 00..50  ->  signed value =  n
    n in 51..99  ->  signed value =  n - 100   (so 51 = -49, 99 = -1)

Range:  -2.45" ... +2.50"  on each axis (asymmetric).

The link is fire-and-forget; the TCS silently drops commands while it is
slewing or while its `guider_cmd_processing` flag is false.
"""

from __future__ import annotations

GUIDE_STEP_ARCSEC: float = 0.05
WIRE_LENGTH: int = 6  # bytes
WIRE_CR: bytes = b"\r"
MAX_POS_STEPS: int = 50
MAX_NEG_STEPS: int = -49


def encode_step(steps: int) -> str:
    """Encode a signed step count (-49..+50) as a two-character ASCII pair.

    Values outside the legal range are clamped (defence in depth; callers
    should already have applied the controller's max_command_arcsec
    clip).
    """
    if steps > MAX_POS_STEPS:
        steps = MAX_POS_STEPS
    elif steps < MAX_NEG_STEPS:
        steps = MAX_NEG_STEPS
    n = steps if steps >= 0 else steps + 100
    return f"{n:02d}"


def decode_step(encoded: str) -> int:
    """Decode a two-character ASCII pair as a signed step count."""
    if len(encoded) != 2 or not encoded.isdigit():
        raise ValueError(f"invalid step encoding: {encoded!r}")
    n = int(encoded)
    return n if n <= MAX_POS_STEPS else n - 100


def encode_command(ra_arcsec: float, dec_arcsec: float) -> bytes:
    """Encode a (RA, Dec) sky offset in arcseconds to a 6-byte wire frame."""
    ra_steps = round(ra_arcsec / GUIDE_STEP_ARCSEC)
    dec_steps = round(dec_arcsec / GUIDE_STEP_ARCSEC)
    return f"G{encode_step(ra_steps)}{encode_step(dec_steps)}".encode("ascii") + WIRE_CR


def decode_command(frame: bytes) -> tuple[float, float]:
    """Decode a wire frame back to (RA, Dec) arcseconds.

    Useful for retrospective log analysis and round-trip property tests.
    Raises ValueError on malformed frames.
    """
    if len(frame) != WIRE_LENGTH:
        raise ValueError(f"wrong length: expected {WIRE_LENGTH}, got {len(frame)}")
    if frame[:1] != b"G":
        raise ValueError(f"wrong prefix: expected b'G', got {frame[:1]!r}")
    if frame[5:6] != WIRE_CR:
        raise ValueError(f"missing CR at byte 5; got {frame[5:6]!r}")
    ra = decode_step(frame[1:3].decode("ascii")) * GUIDE_STEP_ARCSEC
    dec = decode_step(frame[3:5].decode("ascii")) * GUIDE_STEP_ARCSEC
    return ra, dec
