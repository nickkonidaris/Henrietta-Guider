"""Configuration tree for the autoguider. Mirrors §8 of the design spec.

config.toml lives at ~/.config/henrietta_guider/config.toml; load_config
fills in defaults for any missing sections so a partial / older file
just works.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

import tomli_w


@dataclass
class LoopConfig:
    Kp_ra: float = 0.5
    Kp_dec: float = 0.5
    Ki_ra: float = 0.0
    Ki_dec: float = 0.0
    Kd_ra: float = 0.0
    Kd_dec: float = 0.0
    deadband_arcsec: float = 0.025
    max_command_arcsec: float = 2.45
    pacing_interval_s: float = 5.0


@dataclass
class QualityConfig:
    out_of_family_window: int = 20
    out_of_family_warmup_n: int = 10
    out_of_family_sigma: float = 5.0
    auto_resume_in_family: int = 3
    stale_frame_timeout_s: float = 30.0
    target_switch_arcsec_threshold: float = 20.0


@dataclass
class ReductionConfig:
    K: int = 1
    stride: int = 1
    stamp_x_halfwidth_px: int = 25
    stamp_y_lo: int = 600
    stamp_y_hi: int = 1980
    xcor_search_radius_px: int = 3
    auto_refresh_template: bool = False
    template_min_peak_value: float = 0.0


@dataclass
class FilesConfig:
    parent_data_dir: str = "/data/henrietta/raw"
    bad_pixel_mask: str = "bpm_25apr2026.fits"
    sqlite_db: str = "~/.henrietta_guider/henrietta_guider.db"
    log_dir: str = "~/.henrietta_guider/logs"


@dataclass
class TCSConfig:
    bind_host: str = "0.0.0.0"
    listen_port: int = 5400
    plate_scale_arcsec_per_px: float = 0.435
    parity_x: int = +1
    parity_y: int = +1
    pa_convention_offset_deg: float = 0.0


@dataclass
class DetectorConfig:
    y_middle_row: int = 1024
    gain_e_per_dn: float = 4.0
    read_noise_e: float = 12.0
    saturation_dn: int = 40000


@dataclass
class DisplayConfig:
    image_stretch: str = "zscale"
    cmap: str = "viridis"
    theme_macos: str = "aqua"
    theme_linux: str = "clam"
    audio_alerts: bool = True
    audio_alert_sound: str = "/System/Library/Sounds/Submarine.aiff"
    audio_speak_alerts: bool = True


@dataclass
class Config:
    loop: LoopConfig = field(default_factory=LoopConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    reduction: ReductionConfig = field(default_factory=ReductionConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    tcs: TCSConfig = field(default_factory=TCSConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)


def load_config(path: str | Path) -> Config:
    """Load config from TOML; missing file or sections fall back to defaults."""
    p = Path(path).expanduser()
    if not p.exists():
        return Config()
    with p.open("rb") as f:
        data = tomllib.load(f)
    cfg = Config()
    for fld in fields(cfg):
        section = data.get(fld.name)
        if not section:
            continue
        sub = getattr(cfg, fld.name)
        for k, v in section.items():
            if hasattr(sub, k):
                setattr(sub, k, v)
    return cfg


def save_config(cfg: Config, path: str | Path) -> None:
    """Write config as TOML, creating parent directories as needed."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        tomli_w.dump(_to_toml_dict(cfg), f)


def _to_toml_dict(cfg: Config) -> dict:
    """Convert the nested dataclass to a TOML-friendly dict.

    asdict() is sufficient since our types are TOML-native (str/int/
    float/bool); tomli_w handles the rest.
    """
    assert is_dataclass(cfg)
    return asdict(cfg)
