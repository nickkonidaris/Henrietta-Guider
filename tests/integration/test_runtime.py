"""Integration tests for AutoguiderRuntime."""

from __future__ import annotations

import socket
import time
from pathlib import Path

import numpy as np
import pytest

from henrietta_guider.core.config import Config
from henrietta_guider.core.types import Stamp
from henrietta_guider.runtime import AutoguiderRuntime
from tests.integration.fakes import FakeArchon


@pytest.mark.integration
class TestAutoguiderRuntime:
    def _stamp(self, ny: int = 256, nx: int = 256) -> Stamp:
        return Stamp(x_center=nx // 2, x_halfwidth=20, y_lo=20, y_hi=ny - 20)

    def _cfg(self, tmp_path: Path) -> Config:
        cfg = Config()
        cfg.tcs.bind_host = "127.0.0.1"
        cfg.tcs.listen_port = 0  # ephemeral
        cfg.files.sqlite_db = str(tmp_path / "g.db")
        return cfg

    def test_runtime_starts_listener_accepts_runs_worker(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        archon = FakeArchon(out_dir=tmp_path)
        good = np.ones((archon.ny, archon.nx), dtype=bool)
        statuses: list[str] = []

        rt = AutoguiderRuntime(
            cfg=cfg,
            watch_dir=tmp_path,
            science_stamp=self._stamp(),
            bpm_good=good,
            on_status=lambda s: statuses.append(s),
        )
        client: socket.socket | None = None
        try:
            rt.start()
            assert rt.wait_for_listening(timeout_s=2.0)
            port = rt.bound_port
            assert port is not None and port > 0
            # Connect a client; the runtime's accept thread should pick it up.
            client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            assert rt.wait_for_connected(timeout_s=2.0)
            # Wait for Worker.run to enter and publish itself.
            for _ in range(50):
                if rt.worker is not None:
                    break
                time.sleep(0.05)
            assert rt.worker is not None
            # on_status should have surfaced "listening", "connected",
            # and "running" (in that order, with possibly other text).
            assert any(s.startswith("listening") for s in statuses)
            assert any(s.startswith("connected") for s in statuses)
            assert any(s == "running" for s in statuses)
            ordered = [s for s in statuses if not s.startswith("error")]
            listening_idx = next(i for i, s in enumerate(ordered) if s.startswith("listening"))
            connected_idx = next(i for i, s in enumerate(ordered) if s.startswith("connected"))
            running_idx = next(i for i, s in enumerate(ordered) if s == "running")
            assert listening_idx < connected_idx < running_idx
        finally:
            if client is not None:
                client.close()
            rt.stop()
        assert "stopped" in statuses

    def test_runtime_stop_cancels_blocked_accept(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        archon_nx = archon_ny = 256
        good = np.ones((archon_ny, archon_nx), dtype=bool)
        rt = AutoguiderRuntime(
            cfg=cfg,
            watch_dir=tmp_path,
            science_stamp=self._stamp(),
            bpm_good=good,
        )
        rt.start()
        assert rt.wait_for_listening(timeout_s=2.0)
        # No client ever connects. stop() must close the listener and let
        # the accept thread exit cleanly.
        t0 = time.monotonic()
        rt.stop(join_timeout_s=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"stop() took {elapsed:.3f}s — accept did not unblock"
        assert rt._accept_thread is not None
        assert not rt._accept_thread.is_alive()

    def test_runtime_handles_bind_failure(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        # Try to bind to a privileged port that we almost certainly cannot
        # use. SO_REUSEADDR doesn't help here; the bind will EACCES /
        # EPERM unless running as root.
        cfg.tcs.listen_port = 1
        good = np.ones((256, 256), dtype=bool)
        statuses: list[str] = []
        rt = AutoguiderRuntime(
            cfg=cfg,
            watch_dir=tmp_path,
            science_stamp=self._stamp(),
            bpm_good=good,
            on_status=lambda s: statuses.append(s),
        )
        rt.start()
        # Wait briefly for the accept thread to attempt + fail bind.
        for _ in range(20):
            if any(s.startswith("error:") for s in statuses):
                break
            time.sleep(0.05)
        rt.stop(join_timeout_s=2.0)
        assert any(s.startswith("error:") for s in statuses), statuses
        # And listening_evt should never have been set.
        assert not rt._listening_evt.is_set()
