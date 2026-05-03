import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from henrietta_guider.core.config import Config
from henrietta_guider.core.types import Stamp
from henrietta_guider.core.worker import Worker
from tests.integration.fakes import FakeArchon, FakeTCS


@pytest.mark.integration
class TestWorkerEndToEnd:
    def _stamp(self, ny: int = 256, nx: int = 256) -> Stamp:
        return Stamp(x_center=nx // 2, x_halfwidth=20, y_lo=20, y_hi=ny - 20)

    def test_sutr_files_produce_sqlite_rows(self, tmp_path: Path):
        cfg = Config()
        cfg.files.sqlite_db = str(tmp_path / "g.db")
        cfg.detector.y_middle_row = 128
        archon = FakeArchon(out_dir=tmp_path)
        tcs = FakeTCS.make()
        good = np.ones((archon.ny, archon.nx), dtype=bool)
        settle_s = 0.05
        # Spacing between SUTR writes must exceed settle_s; otherwise the
        # watcher's per-path settle timers race and the worker can pull
        # SUTRs out of order, which the Reducer's SanityChecker then
        # WARN_DISCARDs (a real-system safety check). The sleep here is
        # not about wall-clock margin — it ensures each settle expires
        # and queues its file before the next one is created.
        inter_sutr_gap = settle_s * 2
        with Worker.run(
            cfg=cfg,
            watch_dir=tmp_path,
            science_stamp=self._stamp(),
            bpm_good=good,
            tcs_socket=tcs.side_autoguider,
            settle_s=settle_s,
        ):
            # Send a slope file first to seed the template.
            archon.write_slope(42)
            time.sleep(0.3)  # let watcher settle and template build.
            # Then a sequence of SUTRs, paced past settle_s.
            archon.write_sutr(43, 1)
            time.sleep(inter_sutr_gap)
            archon.write_sutr(43, 2)
            time.sleep(inter_sutr_gap)
            archon.write_sutr(43, 3)
            # Poll for both SUTR 1 and SUTR 2 to land rather than relying
            # on a fixed sleep — keeps the test responsive to slow CI.
            db_path = cfg.files.sqlite_db
            for _ in range(20):
                with sqlite3.connect(db_path) as conn:
                    rows = conn.execute(
                        "SELECT frame_number, sutr_number FROM frames ORDER BY 1, 2"
                    ).fetchall()
                if (43, 1) in rows and (43, 2) in rows:
                    break
                time.sleep(0.1)
        # Verify rows landed.
        with sqlite3.connect(cfg.files.sqlite_db) as conn:
            rows = conn.execute(
                "SELECT frame_number, sutr_number FROM frames ORDER BY 1, 2"
            ).fetchall()
        assert (43, 1) in rows
        assert (43, 2) in rows
        tcs.close()

    def test_g_command_sent_when_drift_exceeds_deadband(self, tmp_path: Path):
        # Set up so the synthetic SUTRs imply a non-trivial dx, which
        # produces a controller command above the deadband.
        cfg = Config()
        cfg.files.sqlite_db = str(tmp_path / "g.db")
        cfg.loop.Kp_ra = 1.0
        cfg.loop.deadband_arcsec = 0.001
        cfg.loop.pacing_interval_s = 0.0
        archon = FakeArchon(out_dir=tmp_path)
        tcs = FakeTCS.make()
        good = np.ones((archon.ny, archon.nx), dtype=bool)
        settle_s = 0.05
        with Worker.run(
            cfg=cfg,
            watch_dir=tmp_path,
            science_stamp=self._stamp(),
            bpm_good=good,
            tcs_socket=tcs.side_autoguider,
            settle_s=settle_s,
        ):
            archon.write_slope(42)
            time.sleep(0.3)
            archon.write_sutr(43, 1)
            # Pace past settle_s so the watcher queues SUTR 1 before
            # SUTR 2 lands; without this the per-path settle timers race
            # and the reducer SanityChecker may discard the second SUTR.
            time.sleep(settle_s * 2)
            archon.write_sutr(43, 2, x_center=130)  # 2-px shift
            try:
                frame = tcs.recv_frame(timeout_s=2.0)
                assert frame[:1] == b"G"
                assert frame[5:6] == b"\r"
            finally:
                tcs.close()
