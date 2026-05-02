import json
import sqlite3
from pathlib import Path

import pytest

from henrietta_guider.core.store import FrameRecord, Store
from henrietta_guider.core.types import MeasurementRow


@pytest.mark.unit
class TestStore:
    def _row(
        self, frame: int = 10, sutr: int = 5, stamp_id: int = 0, dx: float | None = 0.05
    ) -> MeasurementRow:
        return MeasurementRow(
            frame_number=frame,
            sutr_number=sutr,
            stamp_id=stamp_id,
            signal_snr=210.0,
            dx_px=dx,
            dy_px=-0.02,
            xcor_peak_value=1.23e6,
            xcor_curvature_x=-1500.0,
            xcor_curvature_y=-1700.0,
            trace_fwhm_x_px=3.4,
            trace_flux_adu=1.84e5,
            sky_background_adu=62.1,
            stamp_x_center=110,
            stamp_x_halfwidth=25,
            stamp_y_lo=600,
            stamp_y_hi=1980,
            template_frame_number=42,
            quality_flags=("frame_skip",),
        )

    def _frame(self, frame: int = 10, sutr: int = 5) -> FrameRecord:
        return FrameRecord(
            frame_number=frame,
            sutr_number=sutr,
            timestamp_utc="2026-04-30T08:14:22.137",
            frame_path=f"/data/2026-04-30/hen{frame:04d}_{sutr:03d}r.fits",
            ramp_complete=False,
            ha_hours=1.234,
            dec_deg=-29.501,
            pa_deg=45.0,
            airmass=1.18,
            temperature_c=-50.0,
            focus_position=12345.0,
            cmd_ra_arcsec=0.0,
            cmd_dec_arcsec=0.05,
            cmd_suppressed_by=None,
            err_ra_arcsec=0.018,
            err_dec_arcsec=0.052,
            guiding_state="GUIDING",
        )

    def test_creates_schema_in_new_file(self, tmp_path: Path):
        db = tmp_path / "test.db"
        with Store.open(db) as st:  # noqa: F841
            pass
        # Schema should exist.
        with sqlite3.connect(db) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "frames" in tables
        assert "stamp_measurements" in tables

    def test_wal_mode_set(self, tmp_path: Path):
        db = tmp_path / "wal.db"
        with Store.open(db) as st:
            cursor = st._conn.execute("PRAGMA journal_mode")
            assert cursor.fetchone()[0].lower() == "wal"

    def test_write_frame_round_trip(self, tmp_path: Path):
        db = tmp_path / "roundtrip.db"
        with Store.open(db) as st:
            frame = self._frame()
            row = self._row()
            st.write_frame(frame, [row])
        # Read back via a fresh connection (proves data was committed
        # and is durable across the close).
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            f_row = conn.execute(
                "SELECT * FROM frames WHERE frame_number=10 AND sutr_number=5"
            ).fetchone()
            assert f_row["timestamp_utc"] == frame.timestamp_utc
            assert f_row["guiding_state"] == "GUIDING"
            assert f_row["ramp_complete"] == 0  # bool False -> int 0
            m_row = conn.execute(
                "SELECT * FROM stamp_measurements "
                "WHERE frame_number=10 AND sutr_number=5 AND stamp_id=0"
            ).fetchone()
            assert m_row["dx_px"] == pytest.approx(0.05)
            assert m_row["signal_snr"] == pytest.approx(210.0)
            assert json.loads(m_row["quality_flags"]) == ["frame_skip"]

    def test_ramp_complete_true_round_trip(self, tmp_path: Path):
        db = tmp_path / "ramp.db"
        with Store.open(db) as st:
            frame = FrameRecord(
                frame_number=42,
                sutr_number=23,
                timestamp_utc="2026-04-30T08:14:22.137",
                frame_path="/x/hen0042.fits",
                ramp_complete=True,  # the slope-fit final
                ha_hours=None,
                dec_deg=None,
                pa_deg=None,
                airmass=None,
                temperature_c=None,
                focus_position=None,
                cmd_ra_arcsec=None,
                cmd_dec_arcsec=None,
                cmd_suppressed_by="alerted",
                err_ra_arcsec=None,
                err_dec_arcsec=None,
                guiding_state="ALERTED",
            )
            st.write_frame(frame, [])
        with sqlite3.connect(db) as conn:
            (rc,) = conn.execute(
                "SELECT ramp_complete FROM frames WHERE frame_number=42"
            ).fetchone()
            assert rc == 1  # bool True -> int 1

    def test_write_frame_with_two_stamps(self, tmp_path: Path):
        db = tmp_path / "two.db"
        with Store.open(db) as st:
            frame = self._frame()
            sci = self._row(stamp_id=0, dx=0.1)
            cmp_ = self._row(stamp_id=1, dx=0.2)
            st.write_frame(frame, [sci, cmp_])
            with sqlite3.connect(db) as conn:
                rows = conn.execute(
                    "SELECT stamp_id, dx_px FROM stamp_measurements "
                    "WHERE frame_number=10 AND sutr_number=5 ORDER BY stamp_id"
                ).fetchall()
                assert rows == [(0, pytest.approx(0.1)), (1, pytest.approx(0.2))]

    def test_indexes_present(self, tmp_path: Path):
        db = tmp_path / "idx.db"
        with Store.open(db) as st:
            indexes = {
                r[0] for r in st._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
        assert "idx_frames_time" in indexes
        assert "idx_frames_hadec" in indexes

    def test_empty_quality_flags_round_trip(self, tmp_path: Path):
        # The common case: an in-family frame with no sanity tags.
        from dataclasses import replace

        db = tmp_path / "empty.db"
        with Store.open(db) as st:
            row_no_flags = replace(self._row(), quality_flags=())
            st.write_frame(self._frame(), [row_no_flags])
        with sqlite3.connect(db) as conn:
            (s,) = conn.execute(
                "SELECT quality_flags FROM stamp_measurements "
                "WHERE frame_number=10 AND sutr_number=5 AND stamp_id=0"
            ).fetchone()
            assert json.loads(s) == []

    def test_signal_snr_null_round_trip(self, tmp_path: Path):
        # signal_snr=None must round-trip as SQL NULL, not 0.0 or empty.
        from dataclasses import replace

        db = tmp_path / "nullsnr.db"
        with Store.open(db) as st:
            row_null = replace(self._row(), signal_snr=None)
            st.write_frame(self._frame(), [row_null])
        with sqlite3.connect(db) as conn:
            (s,) = conn.execute(
                "SELECT signal_snr FROM stamp_measurements "
                "WHERE frame_number=10 AND sutr_number=5 AND stamp_id=0"
            ).fetchone()
            assert s is None
