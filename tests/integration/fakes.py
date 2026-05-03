"""Fakes for integration tests."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits


def write_fits(path: Path, data: np.ndarray) -> None:
    fits.PrimaryHDU(data.astype(np.int16)).writeto(path, overwrite=True)


def _gaussian_column(
    ny: int,
    nx: int,
    x_center: float,
    amp: float = 1000.0,
    sigma: float = 1.5,
    sky: float = 200.0,
) -> np.ndarray:
    x = np.arange(nx)[None, :].astype(np.float32)
    img = np.full((ny, nx), sky, dtype=np.float32)
    img += amp * np.exp(-((x - x_center) ** 2) / (2 * sigma**2))
    return img.astype(np.int16)


@dataclass
class FakeArchon:
    """Writes synthetic henNNNN_sssr.fits + henNNNN.fits frames into a
    tempdir at a configurable cadence. Drives the watcher via filesystem
    events; no atomic rename (per William's preliminary answer)."""

    out_dir: Path
    ny: int = 256
    nx: int = 256

    def write_slope(self, frame: int, x_center: float | None = None) -> Path:
        x = float(self.nx // 2) if x_center is None else float(x_center)
        p = self.out_dir / f"hen{frame:04d}.fits"
        write_fits(p, _gaussian_column(self.ny, self.nx, x))
        return p

    def write_sutr(self, frame: int, sutr: int, x_center: float | None = None) -> Path:
        x = float(self.nx // 2) if x_center is None else float(x_center)
        p = self.out_dir / f"hen{frame:04d}_{sutr:03d}r.fits"
        write_fits(p, _gaussian_column(self.ny, self.nx, x))
        return p


@dataclass
class FakeTCS:
    """A pair of sockets; the autoguider talks to one end, the test
    inspects the other."""

    side_autoguider: socket.socket
    side_test: socket.socket

    @classmethod
    def make(cls) -> FakeTCS:
        a, b = socket.socketpair()
        return cls(side_autoguider=a, side_test=b)

    def recv_frame(self, timeout_s: float = 1.0) -> bytes:
        self.side_test.settimeout(timeout_s)
        return self.side_test.recv(6)

    def close(self) -> None:
        self.side_autoguider.close()
        self.side_test.close()
