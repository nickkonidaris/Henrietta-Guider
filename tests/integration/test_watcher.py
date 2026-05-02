import queue
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from henrietta_guider.core.watcher import Watcher


def _write_fits(path: Path, ny: int = 64, nx: int = 64, value: float = 50.0) -> None:
    img = np.full((ny, nx), value, dtype=np.float32)
    fits.PrimaryHDU(img.astype(np.int16)).writeto(path, overwrite=True)


@pytest.mark.integration
class TestWatcher:
    def _settle_timeout(self) -> float:
        return 0.5  # tests don't need to wait for production 0.2 s

    def test_sutr_file_lands_on_sutr_queue(self, tmp_path: Path):
        with Watcher.start(tmp_path, settle_s=0.05) as w:
            written = tmp_path / "hen0042_017r.fits"
            _write_fits(written)
            evt = w.sutr_queue.get(timeout=self._settle_timeout())
        frame, sutr, arr, path = evt
        assert frame == 42
        assert sutr == 17
        assert arr.shape == (64, 64)
        assert Path(path) == written

    def test_slope_file_lands_on_slope_queue(self, tmp_path: Path):
        with Watcher.start(tmp_path, settle_s=0.05) as w:
            _write_fits(tmp_path / "hen0042.fits")
            evt = w.slope_queue.get(timeout=self._settle_timeout())
        frame, path = evt
        assert frame == 42
        assert Path(path).name == "hen0042.fits"

    def test_unmatched_filename_dropped(self, tmp_path: Path):
        with Watcher.start(tmp_path, settle_s=0.05) as w:
            _write_fits(tmp_path / "junk.fits")
            with pytest.raises(queue.Empty):
                w.sutr_queue.get(timeout=0.3)

    def test_stop_unblocks_queue_consumer(self, tmp_path: Path):
        # Calling stop() should be idempotent and clean up the observer.
        w = Watcher.start(tmp_path, settle_s=0.05).__enter__()
        w.stop()
        # Calling twice must not raise.
        w.stop()
