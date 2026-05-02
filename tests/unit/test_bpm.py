from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from henrietta_guider.core.bpm import load_bpm


def _write_synthetic_bpm(path: Path, ny: int = 32, nx: int = 32, n_bad: int = 3) -> np.ndarray:
    """Write a synthetic 7-HDU BPM (HDU 0 master, others diagnostic-ish)."""
    master = np.ones((ny, nx), dtype=np.uint8)
    rng = np.random.default_rng(0)
    bad_idx = rng.choice(ny * nx, size=n_bad, replace=False)
    master.flat[bad_idx] = 0
    extensions = [
        ("COVERAGE", np.ones((ny, nx), dtype=np.uint8)),
        ("DEAD", np.zeros((ny, nx), dtype=np.uint8)),
        ("HOT", np.zeros((ny, nx), dtype=np.uint8)),
        ("NOISY", np.zeros((ny, nx), dtype=np.uint8)),
        ("NOISY_DARK", np.zeros((ny, nx), dtype=np.uint8)),
        ("REF_PIX", np.zeros((ny, nx), dtype=np.uint8)),
    ]
    hdul = fits.HDUList([fits.PrimaryHDU(master)])
    for name, data in extensions:
        hdu = fits.ImageHDU(data)
        hdu.header["EXTNAME"] = name
        hdul.append(hdu)
    hdul.writeto(path, overwrite=True)
    return master


@pytest.mark.unit
class TestLoadBPM:
    def test_master_returned_as_bool_good_is_true(self, tmp_path: Path):
        bpm_path = tmp_path / "bpm.fits"
        master = _write_synthetic_bpm(bpm_path, ny=8, nx=8, n_bad=3)
        good = load_bpm(bpm_path)
        assert good.dtype == np.bool_
        assert good.shape == (8, 8)
        # master == 1 -> good == True; master == 0 -> good == False.
        np.testing.assert_array_equal(good, master.astype(bool))

    def test_only_hdu0_is_read(self, tmp_path: Path):
        # Write a master that's all-good and a diagnostic HDU 2 ("DEAD")
        # full of "bad". The loader must NOT combine them.
        bpm_path = tmp_path / "bpm.fits"
        master = np.ones((4, 4), dtype=np.uint8)
        dead = np.ones((4, 4), dtype=np.uint8)  # "every pixel dead"
        hdul = fits.HDUList(
            [
                fits.PrimaryHDU(master),
                fits.ImageHDU(np.zeros((4, 4), dtype=np.uint8)),  # COVERAGE
                fits.ImageHDU(dead),  # DEAD
            ]
        )
        hdul[1].header["EXTNAME"] = "COVERAGE"
        hdul[2].header["EXTNAME"] = "DEAD"
        hdul.writeto(bpm_path, overwrite=True)
        good = load_bpm(bpm_path)
        # If load_bpm reads only HDU 0: every pixel good.
        assert good.all()

    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_bpm(tmp_path / "no-such-file.fits")
