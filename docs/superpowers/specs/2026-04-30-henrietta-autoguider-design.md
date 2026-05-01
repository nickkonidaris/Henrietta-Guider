# Henrietta Autoguider — Design

**Status:** Draft (brainstormed, not yet implemented)
**Date:** 2026-04-30
**Owner:** Nick Konidaris
**Collaborator (TCS / Archon):** William

## 1. Purpose & scope

The Henrietta autoguider keeps the science spectrum on the same detector pixels
during long IR-spectroscopic observations on the Swope telescope. It runs on the
Henrietta instrument computer, watches a directory for SUTR (sample-up-the-
ramp) FITS frames produced by the Archon controller, measures the trace position
relative to a calibrated reference, and sends small (±2.5″) telescope offsets to
the TCS over TCP/IP to bring the trace back on target.

In addition to its closed-loop function, every measurement is archived in
SQLite indexed by HA / Dec so that long-term records can be mined for
non-periodic mount errors.

### In scope

- Reading SUTR FITS frames as they appear on disk and computing per-frame
  trace measurements.
- Closed-loop fine guiding (per-axis P controller, with hooks for PI/PID).
- A Tk + ttk + matplotlib operator GUI for region selection, ridge-line setup,
  live image + plot display, and alerts.
- A SQLite archive of every measurement.
- A "headless" CLI for batch / scripted runs.
- Configuration with deterministic, reproducible Python environment via `uv`.

### Not in scope (v1)

- Target acquisition or large telescope offsets (the wire protocol is too
  narrow; observer acquires manually via TCS).
- Closed-loop fault recovery (raise alerts and pause; do not auto-restart the
  Archon or re-acquire).
- Multi-target scheduling (handled by the separate Henrietta-Target-Picker).
- A dedicated cosmic-ray rejection algorithm (medianing throughout the
  pipeline + the bad-pixel mask are sufficient — see §11).
- Online updates to the bad-pixel mask (loaded once at startup).
- High-refresh-rate live image display (loop runs at ≤ ~0.5 Hz; Tk + matplotlib
  is sized for that).

## 2. System overview

note the directories here are notional:
```
┌────────────────────────┐         ┌────────────────────────┐
│  Archon (separate system) │  FITS   │  Watch directory       │
│  writes henNNNN_sss.fits ─────►  │  /data/<night>/...     │
└────────────────────────┘         └──────────┬─────────────┘
                                              │ atomic rename → on_moved
                                              ▼
                                   ┌────────────────────────┐
                                   │  henrietta_guider/core │
                                   │   watcher → reduce →   │
                                   │   centroid → control → │
                                   │   tcs_client → store   │
                                   └──┬─────────────────┬───┘
                                      │ G xx yy CR      │ rows
                                      ▼                 ▼
                                ┌──────────┐    ┌──────────────────┐
                                │   TCS    │    │  henrietta_guider│
                                │ (TCP)    │    │   .db (SQLite)   │
                                └──────────┘    └──────────────────┘

                              GUI (Tk + ttk + matplotlib)
                                imports core, runs in same
                                process; displays live image,
                                plots, alerts, controls.
```

The guider only applies corrections within one exposure's SUTRs — it
infers a new exposure has begun by noticing a new `NNNN` in the filename
and discards its rolling-read buffer at that point. There is no
out-of-band signal from the Archon or TCS: no "exposure starting / ending"
notification, no pointing-state readback, no acknowledgment of
corrections. The guider's world is "FITS files appear in a directory;
emit corrections and rows."

### Wire-protocol context (recap; full spec in `Wireformat.md`)

The TCS guide port accepts six-byte ASCII commands `G xx yy <CR>`, where each
two-digit field encodes a signed offset in 0.05″ steps (range −2.45″ to
+2.50″). `xx` → RA, `yy` → Dec on the sky — the instrument computer applies
the PA rotation. The link is fire-and-forget; the TCS silently ignores
commands while it is slewing or while its `guider_cmd_processing` flag is
false.

## 3. Architecture

### Package layout

```
henrietta_guider/
├── core/                      no UI imports anywhere in this subtree
│   ├── watcher.py             watchdog + atomic-rename → image queue
│   ├── reduce.py              SUTR FrameBuffer, masking, bg subtraction
│   ├── centroid.py            ridge fit, dX from per-row centroids,
│   │                          dY from cross-correlation, FWHM, flux
│   ├── controller.py          P (PI/PID hooks), dead band, max clip
│   ├── tcs_client.py          TCP socket, G-command encoder, pacing
│   ├── store.py               SQLite (frames + box_measurements tables)
│   ├── geometry.py            detector → sky transform (plate scale, PA)
│   ├── monte_carlo.py         "Estimate K" simulator
│   └── config.py              dataclass schema, TOML load/save
├── cli/
│   └── __main__.py            python -m henrietta_guider.cli ...
├── gui/
│   └── app.py                 Tk + ttk + matplotlib operator GUI
└── tests/
    ├── unit/
    └── integration/
```

`core/` has zero dependencies on `gui/` or any GUI library. CLI and GUI are
both thin frontends over the same core.

### Concurrency

- **Main thread**: Tk + matplotlib event loop. Drains a thread-safe
  `queue.Queue[Measurement]` every 200 ms via `root.after()` and updates
  widgets / plots.
- **Worker thread** (single): runs the file watcher, reduction, centroid,
  controller, and TCS sender end-to-end. Pushes `Measurement` events onto the
  queue.
- No async, no multiprocessing. At ≤1 Hz with mostly-I/O work, threading
  is sufficient and dramatically simpler.

### Tooling and reproducibility

- Python **3.14** (regular GIL build), pinned in `.python-version` to a
  specific patch.
- **uv** manages the Python interpreter (downloads `python-build-standalone`),
  the venv, and the lockfile. `uv.lock` is committed to git.
- stdlib `dataclasses` + `tomllib` for config (no pydantic).
- `Tk + ttk + matplotlib` for GUI. `aqua` theme on macOS; `clam` on Linux.
- `astropy` (FITS), `numpy`, `scipy`, `watchdog`.
- Dev: `pytest`, `ruff`. CI: GitHub Actions running `uv sync && make test &&
  make lint`.

The choice of uv is deliberate: in five years macOS will not ship Python
3.14, but `uv sync` will still pull the same interpreter from
`python-build-standalone` and the environment will reproduce identically.

## 4. Frame ingestion & reduction

### File watching

A `watchdog.Observer` is launched on the user-selected `archon_watch_dir`.
The Archon writes each FITS file in place (no `.tmp` + rename), so we
have to detect completion ourselves rather than rely on `on_moved` for an
atomic rename.

The watcher subscribes to both `on_created` and `on_modified` events for
`.fits` paths. Whenever an event arrives for a file, we (re)set a 1.0-s
"settle timer" for that path; once the timer fires with no further events,
we attempt to open the file with `astropy.io.fits` in update-strict mode.
A successful open with `NAXIS1 × NAXIS2 × |BITPIX|/8` matching the data
section size confirms the file is complete; otherwise the file is still
being written and we wait for the next event.

The 1.0-s settle is safe at the file-size level (8 MB writes in well under
100 ms on modern disks) and well below the smallest SUTR sample interval
of 1.3 s.

The real filename pattern (per the sample frames in `test/`) is
`henNNNN_sssr.fits` for the per-SUTR raw reads (e.g. `hen1764_017r.fits`),
plus a final integrated `henNNNN.fits` per integration. The watcher
matches `^hen(\d{4})_(\d{3})r\.fits$` and ignores any other filename
pattern (the bare `henNNNN.fits` carries no new SUTR information beyond
what `_023r` already gave us, so it is logged at DEBUG and dropped). The
parsed `(NNNN, sss)` are pushed into the inbound queue.

### Stale-frame watchdog

A timer in the worker thread tracks elapsed time since the last accepted
guide image. If it exceeds `quality.stale_frame_timeout_s` (default 30 s),
an `ERROR`-level alert "Guiding has stopped — no frames received" is
raised and guiding is paused until frames resume.

The timer is **gated**: it only starts running once at least one guide
image has been accepted, and it is **reset** whenever the watch directory
is changed and on the first accepted guide image after a frame-number
boundary (so the inevitable warm-up delay of ~`2K` reads on a new target
does not falsely trip the alert).

### SUTR difference model

Each `_sss.fits` file is a raw non-destructive read. Useful guide images are
**differences of windows** parameterised by `K`:

```
guide_image = mean(reads[N+1 .. N+K]) − mean(reads[N+1−K .. N])
```

- `K = 1`: image = `read[N] − read[N−1]` (default).
- `K > 1`: averages K reads on each side of the boundary.

Windows **overlap** by default — after each new read, the rolling buffer
shifts by `stride` (default 1) and a new guide image is emitted. Overlap is
configurable.

The guide image represents flux deposited during a known interval; its
magnitude is bounded and does not grow with integration time.

### Frame boundary handling

When a `_001` arrives for a frame number different from the buffer's, the
buffer is **cleared** (a new detector reset has occurred). No guide image is
produced until at least `2K` reads have accumulated on the new frame. Reads
are never mixed across a reset.

### Bad-pixel mask

Loaded once at startup from `files.bad_pixel_mask` (a FITS image, same shape
as the science detector, 0 = good, 1 = bad — exact convention to be confirmed
with William). Applied as a `numpy.ma.MaskedArray` so all downstream centroid
and statistics steps naturally ignore masked pixels.

### Region geometry

Each frame is processed in user-defined rectangular regions. There are up to
**five boxes** per session:

- 1 **science box** (required) — contains the trace.
- 2 **science background boxes** — flank the science box; each independently
  positioned and resizable. Default: same size as science, immediate flanking
  with a small gap.
- 1 **comparison box** (optional) — a second region on the spectrum;
  measured the same way as the science box but does **not** drive the control
  loop. For "is the science box in the right place?" diagnostics.
- 2 **comparison background boxes** — flank the comparison box, same rules.

Background subtraction is critical and may need to dodge neighbouring stars.
Hence the boxes are independent and freely placed.

### Background subtraction

For each (science | comparison) region:

```
sky_level = pooled_median( unmasked pixels in both bg boxes )
science_box_pixels -= sky_level
```

If the user disables bg subtraction, `sky_level = NaN` is recorded and no
subtraction is applied.

### Ridge geometry

The dispersed trace is approximately linear over a box-sized region but is
generally tilted (and slightly curved over the full detector). The ridge is
parameterised by **two coefficients**:

```
X_ridge(Y) = ridge_x_center + tan(ridge_angle_deg · π/180) · (Y − Y_DETECTOR_MIDDLE)
```

- `ridge_x_center`: X-pixel where the ridge crosses the detector's
  middle row (`detector.y_middle_row` in config). Anchored to a fixed global
  reference so the parameter has stable physical meaning regardless of where
  the science box is placed.
- `ridge_angle_deg`: tilt from vertical, in degrees.

Ridge degree is fixed at 1 (linear) for v1; the config field
`reduction.ridge_degree` is reserved for future quadratic support.

The ridge is fit fresh on each new target (it shouldn't change much between
adjacent targets but might, e.g., with focus or rotator-state changes; we
record it per-frame in the database for retrospective study).

### Reference / calibration step

After the user draws boxes on a high-SNR integrated frame (typically the
final `_sss` of the first integration on a target):

1. Background-subtract the science box.
2. For each row Y in the box, compute the column centroid of the
   cross-section (1-D Gaussian fit or weighted COM).
3. Fit `(ridge_x_center, ridge_angle_deg)` linearly to those row centroids.
4. Extract the 1-D **reference flux profile** along the ridge:
   `f_ref(Y)`. This carries any spectral structure useful for Y locking.
5. Store all of the above as the **reference** for this target; persist
   `(ridge_x_center, ridge_angle_deg)` in `session.toml`.

#### Auto-fit acceptance criterion and failure handling

A ridge fit is **accepted** if all of the following hold:

- The number of rows with a successful per-row centroid (SNR above a
  configurable threshold, default 5σ over sky noise) is at least
  `reduction.min_ridge_rows` (default 20).
- The median absolute residual `|X_centroid(Y) − X_ridge(Y)|` over those
  rows is below `reduction.max_ridge_residual_px` (default 0.5 px).
- The fit's matrix is non-singular and the recovered angle is within
  `reduction.max_ridge_angle_deg` of vertical (default ±10°).

If any check fails, the GUI emits an `ERROR`-level banner ("Ridge auto-fit
failed: <reason>") and remains in **IDLE**. The user can then either
redraw the box on a cleaner region, manually enter `(ridge_x_center,
ridge_angle_deg)`, or click two trace points and re-fit. The state machine
transitions to `REFERENCE_PENDING` only when a fit is either auto-accepted
or manually saved.

Manual override: the GUI lets the user adjust `ridge_x_center` and
`ridge_angle_deg` by direct numeric entry, by dragging two handles on the
ridge overlay, or by clicking two points along the visible trace and
re-fitting. After any adjustment, "Save Reference" locks the result.

### Per-frame measurement

For each guide image:

1. Apply the bad-pixel mask.
2. Subtract the sky level from each region's bg-box median.
3. **dX** (cross-dispersion shift, science box):
   - For each row Y, centroid the cross-section in a narrow window around
     `X_ridge(Y)` (window width configurable, default ≈ 5 × FWHM).
   - `δx(Y) = X_centroid(Y) − X_ridge(Y)`.
   - Robust mean (sigma-clipped, weighted by row SNR) over Y → `dX_px`.
4. **dY** (dispersion-direction shift):
   - Extract current 1-D profile along the (translated) ridge.
   - Cross-correlate with `f_ref`; sub-pixel peak via parabolic fit
     → `dY_px`.
5. **Trace FWHM** (along the spatial X axis): collapse the box along Y,
   1-D Gaussian fit → FWHM in pixels. Stored as `trace_fwhm_x_px`.
6. **Trace flux**: sum of bg-subtracted, mask-applied pixels in the science
   box. Stored as `trace_flux_adu`.
7. `sky_background_adu`: pooled median from above.
8. **Comparison box** runs the same pipeline against its own reference;
   results are stored but **never** drive the control loop.

The user enters two **commandable targets**:

- `desired_ridge_x_px` — where the ridge should cross the middle row.
- `desired_ridge_y_px` — where the reference flux profile's Y anchor
  should land.

Per-frame error in pixel space:

```
err_x_px = desired_ridge_x_px − (current_ridge_x_center + dX_px)
err_y_px = desired_ridge_y_px − (current_ridge_y_anchor + dY_px)
```

These are converted to sky offsets (§6).

## 5. Quality control & alerts

For the science box, running statistics over the last
`quality.out_of_family_window` accepted frames (default 20) — median + MAD
of:

- `trace_flux_adu`
- `trace_fwhm_x_px`
- `sky_background_adu`
- `dx_px`, `dy_px`

**Warm-up.** Out-of-family checks are gated on having collected at least
`quality.out_of_family_warmup_n` in-family frames (default 10). Before warm-
up completes, frames are recorded normally and never trigger `ALERTED`,
even on extreme values; the running statistics are simply being seeded.

If any new measurement (post-warm-up) deviates from its running median by
more than `quality.out_of_family_sigma` MAD-sigma (default 5):

- `quality_flags["out_of_family"]` lists the offending metrics.
- `guiding_state` becomes `ALERTED`.
- **No `G` command is issued for that frame.**
- The GUI raises an `ALERT`-level banner and (optionally) an audible bell.
- After `quality.auto_resume_in_family` consecutive in-family frames
  (default 3), state returns to `GUIDING` automatically. Otherwise the user
  must intervene.

**Controller state during ALERTED.** v1 uses a pure-P controller, which is
stateless, so there is nothing to freeze. When PI/PID is added later, the
integral and derivative accumulators **freeze** while `ALERTED` (do not
update with the rejected error, do not zero) and resume on the first
in-family frame. This avoids both wind-up and a discontinuity on resume.

The comparison box runs identical statistics independently — purely
diagnostic.

The **stale-frame watchdog** (§4) is a separate alert path with its own
threshold.

## 6. Control loop

### Detector → sky transform

```
def detector_to_sky(dx_px, dy_px,
                    plate_scale_arcsec_per_px,
                    pa_deg, parity_x, parity_y):
    dx_arcsec = parity_x * dx_px * plate_scale_arcsec_per_px
    dy_arcsec = parity_y * dy_px * plate_scale_arcsec_per_px
    pa = radians(pa_deg)
    dra  = -dx_arcsec * cos(pa) - dy_arcsec * sin(pa)
    ddec = -dx_arcsec * sin(pa) + dy_arcsec * cos(pa)
    return dra, ddec
```

The exact signs and parity are calibrated from William's geometry answers
(PA convention, plate scale, detector orientation parity); the parameters
are exposed in config so we can tune without code changes.

PA is provided by the observer at the start of each session (as a config
field) and will eventually be readable from the instrument.

### Per-axis controller

P now, PI / PID hooks already in the dataclass:

```python
@dataclass
class ControllerConfig:
    Kp: float = 0.5
    Ki: float = 0.0
    Kd: float = 0.0
    deadband_arcsec: float = 0.025
    max_command_arcsec: float = 2.50

class Controller:
    def step(self, error_arcsec: float) -> float:
        if abs(error_arcsec) < self.cfg.deadband_arcsec:
            return 0.0
        cmd = self.cfg.Kp * error_arcsec  # + Ki·∫err + Kd·dErr/dt when nonzero
        return max(-self.cfg.max_command_arcsec,
                   min(self.cfg.max_command_arcsec, cmd))
```

**Clip-don't-split.** When `|error| > max_command_arcsec`, the command is
clipped to the cap and the residual is left for the next frame to pick up.
Splitting a large correction into a multi-command sequence would block the
loop while a fresher measurement is on the way; the next diff image is
always more authoritative than our model of how much we've moved.

### TCS pacing

The TCS silently drops commands while slewing or while its
`guider_cmd_processing` flag is false. We mitigate by enforcing
`tcs.pacing_interval_s` (placeholder default 5 s; final value from William)
between consecutive sends. If the loop wants to send within that window, the
client returns False, the controller skips this frame's send, and the
controller picks up again next frame. The loop is allowed to run faster than
the TCS can absorb; we naturally throttle.

### Wire encoding (matches `Wireformat.md`)

```python
def encode_command(ra_arcsec, dec_arcsec) -> bytes:
    steps_ra  = round(ra_arcsec  / 0.05)
    steps_dec = round(dec_arcsec / 0.05)
    steps_ra  = max(-49, min(50, steps_ra))
    steps_dec = max(-49, min(50, steps_dec))
    xx = steps_ra  if steps_ra  >= 0 else steps_ra  + 100
    yy = steps_dec if steps_dec >= 0 else steps_dec + 100
    return f"G{xx:02d}{yy:02d}\r".encode("ascii")
```

**Asymmetry note.** The wire range is `−2.45″ .. +2.50″` (49 negative
steps vs. 50 positive). To keep the controller's behaviour symmetric in
sign, `loop.max_command_arcsec` is set to **`2.45`** by default — values
beyond `±2.45″` are clipped *before* encoding, so a `−2.50″` controller
output cannot silently lose 0.05″. The encoder still clamps to the wire
range as a defence in depth.

### Connection lifecycle

`TCSClient` runs its own state machine: `DISCONNECTED → CONNECTING →
CONNECTED`, with exponential-backoff auto-reconnect on socket drop.
`send_guide()` is non-blocking; returns False if not currently `CONNECTED` or
within the pacing window. The GUI surfaces the link state and a "commands
suppressed" counter.

### Loop wiring

```
file event → diff image → ridge measurement (science + comparison)
                                │
                                ▼
                  out-of-family check (science)
                                │
                          ┌─────┴─────┐
                          ▼           ▼
                      LOCKED      ALERTED → no command, alert
                          │
              PA rotation, plate scale → (dRA, dDec)
                          │
                  per-axis controller
                          │
                  dead band, clip
                          │
                  TCS pacing gate
                          │
                  send G command (or defer)
                          │
                  insert frames + box_measurements rows
                          │
                  push event to GUI queue
```

## 7. Data store

Single SQLite database (`files.sqlite_db`, default
`~/.henrietta_guider/henrietta_guider.db`), WAL mode, two tables. Writes
are issued synchronously from the worker thread but use SQLite's default
WAL durability (no per-row `fsync`); a crash may lose the last few rows,
which is acceptable — the `frames` table is for retrospective analysis,
not as a transactional system of record for control.

```sql
CREATE TABLE frames (
    frame_number       INTEGER NOT NULL,
    sutr_number        INTEGER NOT NULL,
    timestamp_utc      TEXT,
    frame_path         TEXT,
    ramp_complete      INTEGER,             -- boolean
    ha_hours           REAL,
    dec_deg            REAL,
    pa_deg             REAL,
    airmass            REAL,
    temperature_c      REAL,
    focus_position     REAL,
    cmd_ra_arcsec      REAL,         -- value sent (0 if dead-banded), NULL if not sent
    cmd_dec_arcsec     REAL,         -- value sent (0 if dead-banded), NULL if not sent
    cmd_suppressed_by  TEXT,         -- NULL = sent, else 'pacing'|'deadband'|'alerted'|'tcs_disconnected'
    err_ra_arcsec      REAL,
    err_dec_arcsec     REAL,
    guiding_state      TEXT,
    PRIMARY KEY (frame_number, sutr_number)
);

CREATE TABLE box_measurements (
    frame_number       INTEGER NOT NULL,
    sutr_number        INTEGER NOT NULL,
    box_id             INTEGER NOT NULL,    -- 0 = science, 1 = comparison
    box_xmin           INTEGER,
    box_xmax           INTEGER,
    box_ymin           INTEGER,
    box_ymax           INTEGER,
    ridge_x_center_px  REAL,
    ridge_angle_deg    REAL,
    dx_px              REAL,
    dy_px              REAL,
    trace_fwhm_x_px    REAL,
    trace_flux_adu     REAL,
    sky_background_adu REAL,
    quality_flags      TEXT,                -- JSON
    PRIMARY KEY (frame_number, sutr_number, box_id),
    FOREIGN KEY (frame_number, sutr_number)
        REFERENCES frames(frame_number, sutr_number)
);

CREATE INDEX idx_frames_time   ON frames(timestamp_utc);
CREATE INDEX idx_frames_hadec  ON frames(ha_hours, dec_deg);
```

HA, Dec, PA, airmass, temperature, and focus position come from the FITS
header (exact keywords TBC with William).

## 8. Configuration

Two layered TOML files; reference `Section 5` of the brainstorm transcript
or the `core/config.py` schema for the canonical default values.

### `config.toml` (rarely changes; user settings)

`~/.config/henrietta_guider/config.toml`

The canonical schema lives in `core/config.py` as a tree of dataclasses.
Sections: `[loop]`, `[quality]`, `[reduction]`, `[files]`, `[tcs]`,
`[detector]`, `[display]`. Notable defaults:

- `loop.Kp_ra = loop.Kp_dec = 0.5`
- `loop.deadband_arcsec = 0.025`
- `loop.max_command_arcsec = 2.45`  (symmetric within the wire range
  −2.45″..+2.50″; see §6 encoder note)
- `loop.pacing_interval_s = 5.0` (placeholder; final from William)
- `quality.out_of_family_window = 20`
- `quality.out_of_family_warmup_n = 10`
- `quality.out_of_family_sigma = 5.0`
- `quality.auto_resume_in_family = 3`
- `quality.stale_frame_timeout_s = 30.0`
- `reduction.K = 1`, `reduction.stride = 1`, `reduction.ridge_degree = 1`
- `reduction.min_ridge_rows = 20`
- `reduction.max_ridge_residual_px = 0.5`
- `reduction.max_ridge_angle_deg = 10.0`
- `detector.y_middle_row = 1024` (placeholder)
- `detector.gain_e_per_dn = 4.0` (placeholder)
- `detector.read_noise_e = 12.0` (placeholder)
- `detector.saturation_dn = 40000`
- `display.image_stretch = "zscale"`, `display.theme_macos = "aqua"`,
  `display.theme_linux = "clam"`

A `[files] parent_data_dir` field is supported as the default starting
location for the GUI's directory picker.

### `session.toml` (auto-saved per-session)

`~/.config/henrietta_guider/session.toml`

Holds the daily-changing state:

- `archon_watch_dir` — selected via OS folder picker each night.
- Box geometries (science, science_bg_left, science_bg_right, comparison,
  comparison_bg_left, comparison_bg_right).
- Ridge state (`angle_deg`, `x_center_px`).
- Targets (`desired_ridge_x_px`, `desired_ridge_y_px`).

Reference image and reference 1-D profile are **not** persisted — they must
be re-captured each session from a fresh high-SNR frame.

## 9. GUI

Single Tk window, layout:

```
┌─ status bar ─────────────────────────────────────────────────────────┐
│ TCS ●  │ Watcher ●  │ State: GUIDING │ Watch dir: /data/... [Change…]│
├──────────────────────────┬───────────────────────────────────────────┤
│  matplotlib live image   │  Boxes:    [Draw science] [Add comparison]│
│   - science box (red)    │            [Reset bg boxes]               │
│   - science bg ×2 (orng) │                                           │
│   - comparison (cyan)    │  Ridge:    angle = ___°   x_center = ___ │
│   - ridge line + handles │            [auto-fit] [edit] [Save ref]   │
│                          │                                           │
│                          │  Targets (commandable):                   │
│                          │    desired ridge_x: __________ px         │
│                          │    desired ridge_y: __________ px         │
│                          │                                           │
│                          │  Loop:     [START]  [STOP]  [PAUSE]       │
│                          │  Tools:    [Estimate K]  [Settings…]      │
├──────────────────────────┴───────────────────────────────────────────┤
│   Time series (scrollable):                                          │
│     dx_px, dy_px (rejected / paused frames marked)                   │
│     trace_fwhm_x_px                                                  │
│     trace_flux_adu       — science (solid), comparison (dashed)      │
│     sky_background_adu                                               │
│     commands sent (RA, Dec)                                          │
└──────────────────────────────────────────────────────────────────────┘
```

### State machine

```
IDLE ─ user draws boxes; ridge auto-fit shown ─►
REFERENCE_PENDING ─ adjust ridge / bg boxes; click "Save Reference" ─►
REFERENCE_SET ─ enter targets; click "Start Guiding" ─►
GUIDING ─ ALERTED auto-entered/exited on out-of-family ─►
(STOP returns to REFERENCE_SET; PAUSE freezes commands without losing state)
```

Across-target operation: STOP → slew TCS manually → take fresh first
integration → click "Re-fit ridge" → REFERENCE_PENDING with the same boxes
still drawn.

### Watch directory

Opens an OS-native folder picker (`tkinter.filedialog.askdirectory`) at
startup if the previously-saved `archon_watch_dir` doesn't exist, and on demand
via the **[Change…]** button in the status bar. The button is enabled in
**every** state, including `GUIDING`.

On change, regardless of starting state:

1. Stop the `watchdog` observer.
2. Clear the rolling SUTR buffer (no reads carry across).
3. Discard the in-memory reference image and reference 1-D profile (they
   were tied to the previous directory's frames).
4. Restart the observer on the new path.
5. Persist `archon_watch_dir` to `session.toml`.
6. Transition the state machine: from any of `REFERENCE_SET`, `GUIDING`,
   `ALERTED`, or `PAUSED`, the state drops to **`REFERENCE_PENDING`**;
   from `IDLE`, it stays in `IDLE`. Box geometry, ridge coefficients, and
   commandable targets are preserved (they are detector-frame quantities,
   not directory-bound). The user must click "Save Reference" on a fresh
   high-SNR frame from the new directory to resume guiding.

### Settings dialog

`ttk.Notebook` with tabs: **Loop**, **Quality**, **Reduction**, **Files**,
**TCS**, **Display**. Maps directly onto the config sections in §8.

### "Estimate K" tool

Opens a modal dialog. Runs a Monte Carlo simulator on the current reference
guide image:

1. Compute expected photoelectrons per pixel from the current diff image and
   `detector.gain_e_per_dn`.
2. For `K ∈ {1, 2, 3, 4, 5}`:
    - Build 50 noisy realisations of a K-window difference image, including
      Poisson shot noise and read-noise scaled by `√(2/K)`.
    - Run each realisation through the same ridge-relative measurement
      pipeline used for guiding.
3. Display a table `K → RMS(dx_px), RMS(dy_px)` and recommend the smallest
   K with RMS below a configurable threshold.
4. Click **Apply** to update the running K (writes `reduction.K` in
   `config.toml`).

### Threading rules

- All Tk widget mutation happens on the main thread, called from
  `root.after(200, drain_queue)`.
- The worker thread only puts events on the queue; never touches Tk objects.

### Alerts banner

Three severity levels surfacing just below the status bar:

- **WARN** (yellow) — degraded but loop continues (e.g. TCS pacing throttle).
- **ALERT** (orange) — out-of-family detected; commands suppressed; loop
  self-recovering.
- **ERROR** (red) — file watcher stalled / TCS disconnected / ridge fit
  failed. Requires user action.

## 10. Logging

stdlib `logging`, two handlers:

- **stderr** at `INFO`.
- **Rotating file** at `DEBUG` in `~/.henrietta_guider/logs/`, daily,
  30-day retention.

Format: `2026-04-30T08:14:22.137Z INFO core.tcs_client: G50,99 sent
(RA=+2.50″, Dec=-0.05″)`

What gets logged:

- `INFO`: lifecycle events (startup, shutdown, watch-dir change, reference
  saved, guiding started/stopped/paused/alerted), every G command actually
  sent, every alert condition triggered.
- `DEBUG`: every diff image, ridge fits, COM measurements, dead-banded
  errors, file events.
- `WARNING`: unexpected filenames, TCS pacing throttles, mid-run mask file
  changes, malformed FITS headers.
- `ERROR`: TCS connection drops, ridge fit failures, FITS read errors,
  watcher death.

The log is the second source of truth after SQLite for debugging.

## 11. Testing

### Unit (`tests/unit/`, fast, no I/O, no GUI)

- `tcs_client.encode_command`: tabular tests including ±2.50″, just-out-of-
  range, sub-step rounding. Round-trip against a Python re-implementation of
  the C++ parser to be sure the two agree on every value. Property test:
  `decode(encode(x)) == round(x / 0.05) * 0.05` for every `x` in
  `[-2.45″, +2.50″]` at 0.001″ steps.
- `geometry.detector_to_sky`: PA sweep 0–360°, identity at PA=0 and 90°,
  parity-flip checks.
- `controller.step`: dead band, gain math, max-command clipping.
- `reduce.framebuffer`: frame-boundary clearing, K=1/2/3, overlapping vs
  non-overlapping stride.
- `centroid` on synthetic Gaussian-trace images with noise + bad pixels:
  centroid within 0.05 px of injected truth; ridge fit recovers angle within
  0.05°.
- `out_of_family` detector: trigger and auto-resume on synthetic series.

### Integration (`tests/integration/`)

A `FakeArchon` writes a sequence of FITS frames into a tempdir using the
atomic-rename pattern. The test launches the core in a thread, lets it
process the sequence, and asserts:

- expected SQLite rows exist;
- expected G commands arrive at a `FakeTCS` (a `socket.socketpair`-based
  recorder);
- out-of-family alerts trigger when the fake Archon injects a bad frame;
- stale-frame timeout fires when the fake Archon stops writing;
- a synthesised telescope-offset round trip — inject a known shift on the
  fake Archon side, assert the inverse correction is sent.

### Manual / hardware-in-loop

- The Estimate-K tool doubles as a synthetic-data harness on real frames.
- Real on-sky tuning (gains, K, alert thresholds) happens at the telescope.

### Coverage target

≥ 80 % on `core/`. GUI exercised manually; its callbacks should be thin
enough that the heavy logic in `core/` is already covered.

### Cosmic rays (note rather than test)

We rely on the multiple medianing operations in the pipeline to suppress
CR contamination — sky median in two strips, robust sigma-clipped mean
across rows for `dX`, the cross-correlation's broad peak fit for `dY`, and
the running-stats out-of-family check for `flux` and `FWHM`. A CR has to
survive (a) the bad-pixel mask, (b) per-row sigma-clipping, (c) the broad
CC peak fit, and (d) the out-of-family check. Stacking those, dedicated CR
detection adds little — and is therefore omitted in v1.

### CI

GitHub Actions on push: `uv sync && make test && make lint`.

## 12. Future features (designed-for, not built in v1)

- **Dithering.** A `dither_pattern` config block + a target-modifier between
  controller and encoder. v1 spec: **uniform random draw** of `(dRA, dDec)`
  on `[-A, +A]` per axis (independent), redrawn each new integration when
  `_001` arrives. Default off.
- **Absorption-feature Y-locking.** If cross-correlation Y is too noisy on
  featureless continua, swap in a "lock to a chosen line" measurement: user
  clicks a feature, system fits a Gaussian to it each frame. Slot in as an
  alternative measurement strategy.
- **Quadratic ridge.** `reduction.ridge_degree = 2` adds a `c2` coefficient
  to the fit and to the SQLite schema.
- **Multiple comparison boxes.** Schema already supports `box_id ≥ 2`.
- **PI / PID controllers.** `Ki`, `Kd` already in config; needs the integral
  / derivative state machinery and anti-wind-up.
- **TCS status channel.** If a status / pointing-readback protocol is
  surfaced, sanity-checking against current TCS state can be added.
- **Two-process daemon.** Wrap `core` as a small ZMQ/HTTP daemon and port
  the GUI to a thin client. Useful only if remote operation or surviving
  GUI crashes ever become requirements.
- **"Best K + best Y position" Monte Carlo sweep.** Extend the Estimate-K
  dialog to sweep box position along the spectrum and recommend an optimum
  location for the science box.

## 13. Open questions

Tracked in `Questions-for-William.md` at the repo root. Summary by area:

- TCS settle time, `guider_cmd_processing` semantics, status / telemetry
  channel availability, ACK behaviour.
- Archon atomic-rename convention confirmation, output directory, FITS keyword
  inventory (HA, Dec, PA, airmass, temperature, focus, exposure time, UTC),
  intermediate-read availability (already confirmed: not available).
- Detector parameters: gain (placeholder 4 e⁻/DN), read noise, saturation
  (40000 DN), `y_middle_row`.
- Bad-pixel mask source, format, lifecycle.
- PA convention, plate scale, detector orientation parity.
- Operations: who toggles `guider_cmd_processing`, behaviour around target
  acquisitions.

Each of these is a placeholder in `config.toml` or behind a config-flag so
the autoguider remains buildable and tunable while answers come in.
