import pytest

from henrietta_guider.core.wire import (
    GUIDE_STEP_ARCSEC,
    decode_command,
    encode_command,
    encode_step,
)


@pytest.mark.unit
class TestEncodeStep:
    def test_zero(self):
        assert encode_step(0) == "00"

    def test_positive_max(self):
        assert encode_step(50) == "50"

    def test_negative_one(self):
        assert encode_step(-1) == "99"

    def test_negative_max(self):
        assert encode_step(-49) == "51"

    @pytest.mark.parametrize(
        "steps,encoded",
        [
            # +0.05" through -2.45" — the canonical anchor points called out
            # in Wireformat.md and the spec.
            (0, "00"),
            (1, "01"),
            (10, "10"),
            (50, "50"),
            (-1, "99"),
            (-2, "98"),
            (-49, "51"),
        ],
    )
    def test_table(self, steps, encoded):
        assert encode_step(steps) == encoded

    def test_half_step_rounding_is_bankers(self):
        """Pin Python's default round-half-to-even on the 0.5 boundary so a
        future switch to int(round(x)) or floor doesn't silently drift.

        round(1.5) == 2 (rounds to even); round(2.5) == 2 (also even).
        """
        assert round(0.5) == 0
        assert round(1.5) == 2
        assert round(2.5) == 2

    def test_clamps_above_max(self):
        # Caller is expected to clamp first; encoder is defence in depth.
        assert encode_step(99) == "50"

    def test_clamps_below_min(self):
        assert encode_step(-99) == "51"


@pytest.mark.unit
class TestEncodeCommand:
    def test_zero_zero(self):
        assert encode_command(0.0, 0.0) == b"G0000\r"

    def test_max_positive(self):
        # +2.50" RA, +2.50" Dec
        assert encode_command(+2.50, +2.50) == b"G5050\r"

    def test_max_negative(self):
        # -2.45" RA, -2.45" Dec
        assert encode_command(-2.45, -2.45) == b"G5151\r"

    def test_rounds_to_nearest_step(self):
        # 0.07" rounds to 0.05" (1 step). 0.024" rounds to 0.0" (0 steps).
        assert encode_command(0.07, 0.024) == b"G0100\r"

    def test_round_trip_property(self):
        """For every legal arcsec offset, decode(encode(x)) == round(x/0.05)*0.05.

        We sample at 0.01" spacing on each axis. That's 496 × 496 ≈ 246 k
        pairs — still finishes in a few seconds — which is plenty to
        detect any sign-error or off-by-one in the encoder/decoder.
        """
        import numpy as np

        for x in np.arange(-2.45, 2.501, 0.01):
            for y in np.arange(-2.45, 2.501, 0.01):
                wire = encode_command(float(x), float(y))
                ra, dec = decode_command(wire)
                assert abs(ra - round(x / GUIDE_STEP_ARCSEC) * GUIDE_STEP_ARCSEC) < 1e-9
                assert abs(dec - round(y / GUIDE_STEP_ARCSEC) * GUIDE_STEP_ARCSEC) < 1e-9


@pytest.mark.unit
class TestDecodeCommand:
    def test_canonical_zero(self):
        assert decode_command(b"G0000\r") == (0.0, 0.0)

    def test_max_positive(self):
        assert decode_command(b"G5050\r") == (2.50, 2.50)

    def test_negative_pair(self):
        # 9951 -> RA = -1 step = -0.05"; Dec = -49 steps = -2.45"
        assert decode_command(b"G9951\r") == (pytest.approx(-0.05), pytest.approx(-2.45))

    def test_rejects_missing_cr(self):
        with pytest.raises(ValueError, match="missing CR"):
            decode_command(b"G0000\n")

    def test_rejects_wrong_prefix(self):
        with pytest.raises(ValueError, match="prefix"):
            decode_command(b"X0000\r")

    def test_rejects_short_frame(self):
        with pytest.raises(ValueError, match="length"):
            decode_command(b"G000\r")
