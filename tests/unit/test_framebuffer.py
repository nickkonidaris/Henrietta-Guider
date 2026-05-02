import numpy as np
import pytest

from henrietta_guider.core.framebuffer import FrameBuffer


def _read(value: float, shape: tuple[int, int] = (4, 4)) -> np.ndarray:
    return np.full(shape, value, dtype=np.float32)


@pytest.mark.unit
class TestFrameBufferKEqualsOne:
    def test_first_read_does_not_emit(self):
        fb = FrameBuffer(K=1, stride=1)
        out = fb.add(frame_number=42, sutr_number=1, read=_read(100.0))
        assert out is None

    def test_second_read_emits_difference(self):
        fb = FrameBuffer(K=1, stride=1)
        fb.add(42, 1, _read(100.0))
        out = fb.add(42, 2, _read(150.0))
        assert out is not None
        np.testing.assert_array_almost_equal(out, _read(50.0))

    def test_frame_boundary_clears_buffer(self):
        fb = FrameBuffer(K=1, stride=1)
        fb.add(42, 1, _read(100.0))
        fb.add(42, 2, _read(150.0))
        # New frame: buffer must clear; this read is _001 of frame 43,
        # so no guide image yet.
        out = fb.add(43, 1, _read(200.0))
        assert out is None
        # Next read on frame 43 differences against frame 43's _001,
        # NOT frame 42's last read.
        out = fb.add(43, 2, _read(220.0))
        np.testing.assert_array_almost_equal(out, _read(20.0))


@pytest.mark.unit
class TestFrameBufferKAndStride:
    def test_K2_emits_after_4_reads(self):
        # K=2, stride=1: needs 2K=4 reads in the buffer; window-difference is
        # mean(reads[3..4]) - mean(reads[1..2]).
        fb = FrameBuffer(K=2, stride=1)
        for sutr, val in enumerate([10, 20, 30, 40], start=1):
            out = fb.add(99, sutr, _read(float(val)))
            if sutr < 4:
                assert out is None
        # mean(30, 40) - mean(10, 20) = 35 - 15 = 20
        np.testing.assert_array_almost_equal(out, _read(20.0))

    def test_K2_stride_2_skips_every_other(self):
        # K=2, stride=2: emits every 2 reads after warm-up, not every 1.
        fb = FrameBuffer(K=2, stride=2)
        emits = []
        for sutr, val in enumerate([10, 20, 30, 40, 50, 60], start=1):
            out = fb.add(99, sutr, _read(float(val)))
            if out is not None:
                emits.append(out.mean())
        # After read 4: mean(30,40)-mean(10,20)=20.
        # After read 5: stride=2 not yet -> skip.
        # After read 6: mean(50,60)-mean(30,40)=20.
        assert emits == pytest.approx([20.0, 20.0])

    def test_buffer_size_is_2K(self):
        fb = FrameBuffer(K=3, stride=1)
        for sutr in range(1, 8):
            fb.add(1, sutr, _read(float(sutr)))
        # Buffer must hold the most recent 2*K=6 reads.
        assert len(fb._buf) == 6  # implementation detail; test pins it
