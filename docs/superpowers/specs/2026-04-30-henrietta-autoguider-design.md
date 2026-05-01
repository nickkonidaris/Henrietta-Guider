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
- A Tk + ttk + matplotlib operator GUI for stamp / template setup,
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
│   ├── measure.py             stamp prep, local sky subtraction, BPM,
│   │                          2-D xcor against template, sub-pixel peak,
│   │                          FWHM, flux
│   ├── controller.py          P (PI/PID hooks), dead band, max clip
│   ├── tcs_client.py          TCP socket, G-command encoder, pacing
│   ├── store.py               SQLite (frames + stamp_measurements tables)
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
`.fits` paths. Whenever an event arrives for a file, we (re)set a 0.2-s
"settle timer" for that path; once the timer fires with no further events,
we attempt to open the file with `astropy.io.fits` in update-strict mode.
A successful open with `NAXIS1 × NAXIS2 × |BITPIX|/8` matching the data
section size confirms the file is complete; otherwise the file is still
being written and we wait for the next event.

The 0.2-s interval is empirically sufficient — `experiments/watchdog_close_event_test.py`
shows that on macOS the kqueue and fsevents event streams stop arriving
within milliseconds of the actual file-write completion. 0.2 s gives us
~200 ms of margin while still being well below the smallest SUTR sample
interval of 1.3 s. On Linux a future optimization can use the
`on_closed` event directly (inotify exposes `IN_CLOSE_WRITE`); the macOS
backends of `watchdog` do not implement close-event mapping, so the
settle-timer is the cross-platform path for v1.

The real filename pattern (per the sample frames in `test/`) is
`henNNNN_sssr.fits` for the per-SUTR raw reads (e.g. `hen1764_017r.fits`),
plus a final integrated `henNNNN.fits` per integration. The watcher
matches `^hen(\d{4})_(\d{3})r\.fits$` and ignores any other filename
pattern (the bare `henNNNN.fits` carries no new SUTR information beyond
what `_023r` already gave us, so it is logged at DEBUG and dropped). The
parsed `(NNNN, sss)` are pushed into the inbound queue.

### Sequential-order sanity checks

The Archon is expected to write SUTRs in monotonically increasing order
within a frame, and frame numbers in monotonically increasing order
across frames. The watcher enforces this and flags violations:

- **Within a frame.** Track the highest `sutr_number` seen for the
  current `frame_number`. On a new file:
  - Expected case: `sutr == last_sutr + 1` (or `sutr == 1` on a new
    frame). Process normally.
  - **Skipped SUTR** (`sutr > last_sutr + 1`): log a `WARNING`
    ("frame %d: SUTRs %d..%d missing"), discard the rolling-read buffer
    contents that would have used the missing reads, and resume building
    K-window differences from the new SUTR forward. **Quiet** — log only,
    no banner, no audio.
  - **Out-of-order or repeated SUTR** (`sutr ≤ last_sutr`): log a
    `WARNING` ("frame %d: out-of-order SUTR %d after %d"), discard the
    file, do not update the buffer. **Audible**: WARN-level yellow
    banner and the warning sound (`display.audio_alert_sound`). Guiding
    continues. The system is new and these events should be rare; this
    is a commissioning-grade alert so the operator knows the Archon
    reordered something.
- **Across frames.** Track the highest `frame_number` seen.
  - Expected case: `frame_number > last_frame_number`. Process; clear the
    rolling-read buffer (a new detector reset, see §4 "Frame boundary
    handling").
  - **Skipped frame numbers** (gap of >1): log `INFO`
    ("frame %d → %d; %d frame(s) skipped"). Normal (operator aborted
    exposures) — not an alert.
  - **Backwards or repeated frame number** (`frame_number ≤ last`):
    log a `WARNING`, discard the file. **Audible**: same WARN banner +
    warning sound as out-of-order SUTRs. Should never happen in normal
    operation; serious if it does.

Every violation also lands in `quality_flags` on the `stamp_measurements`
row (for non-discarded frames) so retrospective analysis can spot
patterns even when the operator missed the live alert.

### Stale-frame watchdog

A timer in the worker thread tracks elapsed time since the last accepted
guide image. If it exceeds `quality.stale_frame_timeout_s` (default 30 s),
guiding **stops** — not pauses — because a 30-second gap likely means
something operationally significant has happened (target slewed, Archon
restarted, observer paused the run, target swap). Auto-resuming with a
stale template after that long is unsafe.

Concretely:

- An `ERROR`-level alert is raised: "Guiding has stopped — no frames
  received for N s."
- The audio alert (§9) plays.
- The state machine transitions from `GUIDING` (or `ALERTED`) directly
  to `REFERENCE_PENDING`. Stamp geometry is preserved (detector-frame
  and durable), but the in-memory template is discarded, the rolling
  SUTR buffer is cleared, and the running statistics for out-of-family
  detection are reset. The observer must click "Build Template" on a
  fresh `henNNNN.fits` before guiding can re-engage.

The timer is **gated**: it only starts running once at least one guide
image has been accepted, and it is **reset** whenever the watch directory
is changed and on the first accepted guide image after a frame-number
boundary (so the inevitable warm-up delay of ~`2K` reads on a new target
does not falsely trip the alert).

### Target-switch detection

Even if frames keep arriving, the operator may swap targets without
stopping the guider. Two signals are checked on every accepted frame,
with **different severities**:

#### Pointing jump (authoritative)

Read RA, Dec from the FITS header. Compute the on-sky distance from the
previous accepted frame:

```
d = √((ΔRA · cos(Dec))² + ΔDec²)
```

If `d > quality.target_switch_arcsec_threshold` (default 20″), this is a
serious indication of a real target swap. **Full alert**:

- An `ERROR`-level banner: "Target change possible — pointing jumped %.1f″"
- The warning sound + the spoken phrase "target change possible" (§9).
- State machine transitions to `REFERENCE_PENDING`, discarding the
  template and resetting out-of-family running statistics.

#### OBJECT keyword change (advisory)

Read the `OBJECT` keyword (exact keyword TBC with William). If it differs
from the previous frame's value, this *might* be a target switch — but it
might also just be the operator updating a metadata label without
actually slewing. So this is treated as a **soft warning**:

- A `WARN`-level (yellow) banner: "OBJECT changed: %s → %s — verify"
- A brief beep (Tk's `widget.bell()` — the system tink, not the louder
  warning sound). No spoken phrase.
- A log line at `WARNING`.
- **Guiding continues**; no state transition.

The pointing-jump check is independent and will catch a real swap on its
own. The OBJECT check is just informational — useful when the operator
updates the target label *and* slews (you get both alerts), or updates
just the label (you get only the small beep, and guiding rolls on).

If both checks trip on the same frame, the pointing-jump path wins
(full alert; the small beep is suppressed).

The first frame after a fresh `Build Template` is treated as the new
"previous" — no comparison is made, so the very first frame on a new
target never trips either check.

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

Loaded once at startup from `files.bad_pixel_mask`. The Henrietta BPM is
a multi-extension FITS file with seven HDUs:

- **HDU 0** (primary; "Henrietta bad pixel mask") — the master mask, in
  **`1 = good`** convention. Empirically ~0.3 % bad on the current
  detector. **This is the only HDU the autoguider reads.**
- **HDU 1 `COVERAGE`** (1 = illuminated science region; ~85 %).
- **HDU 2 `DEAD`**, **HDU 3 `HOT`**, **HDU 4 `NOISY`**,
  **HDU 5 `NOISY_DARK`**, **HDU 6 `REF_PIX`** — diagnostic categories
  (1 = bad in that category). The H2RG reference pixels are flagged in
  `REF_PIX` and already folded into the master HDU 0, so the autoguider
  does **not** apply a separate reference-pixel correction; respecting
  HDU 0 covers it.

Applied as `numpy.ma.MaskedArray` (mask True where master == 0) so all
downstream measurements ignore masked pixels.

### Stamp geometry

Each frame is processed inside small rectangular **stamps**, not the full
detector. Up to two per session:

- 1 **science stamp** (required) — covers the bright trace.
  Parameterised by `(x_center, x_halfwidth, y_lo, y_hi)`. Typical sizes:
  `x_halfwidth ≈ 25 px` (snug around the trace; ±25 covers a 6 px drift
  with margin) and `y_lo..y_hi` spans the whole illuminated stripe
  (filter cutoff to filter cutoff, e.g. `600..1980`). The wide Y range is
  load-bearing — both Y-axis precision (from filter cutoffs and absorption
  bands) and X-axis precision (from the continuum) come from the full
  vertical extent.
- 1 **comparison stamp** (optional) — a second region on the spectrum,
  measured by the same algorithm but **never** drives the control loop.
  For "is the science stamp positioned well?" diagnostics.

Stamps are drawn or numerically entered in the GUI. Geometries persist
in `session.toml` so they survive across nights once set up.

### Local sky subtraction

Each stamp does its own row-by-row sky subtraction internally — there
are no separate flanking background boxes. For each row Y of the stamp:

```
edge      = stamp_width // 6
sky_row_y = median( pixels in [stamp_x_min..stamp_x_min + edge] ∪
                              [stamp_x_max − edge..stamp_x_max], row Y,
                    masking out BPM-bad pixels )
stamp[Y, :] -= sky_row_y
```

The outer 1/6 of the stamp on each side is treated as sky; their median
defines the sky pedestal for that row. This removes detector pedestal
differences between reads, sky-background gradients along the trace, and
slow per-frame H2RG bias drift — all of which would otherwise bias the
cross-correlation peak away from the structure that carries position
information.

The per-row-per-stamp `sky_row_y` values are summarised as a single
`sky_background_adu` (median across rows) for the per-frame archive and
the time-series plot.

### Template build

The reference (= the "where I want the trace to live") is a 2-D
template image held in memory.

When the user clicks **"Build Template"** in the GUI, the source frame
is the most recent **`henNNNN.fits`** — the slope-fit final image
produced by the Archon at the end of an integration. This is the
highest-S/N representation of the trace available because it combines
all SUTR samples through the slope fit. The autoguider extracts the
science stamp (and optionally the comparison stamp) from that file,
applies the BPM, and runs the local sky subtraction described above.
The resulting bg-subtracted, masked stamp **is** the template `T(x, y)`.

**Sliding (running-average) template — important for long sequences.**
Henrietta's guide sequences run many hours; over that time the trace
shape evolves slowly (changing water vapour, airmass, thermal drift,
flexure) and a fixed template drifts behind those systematics. The
autoguider therefore supports a **running-average sliding template**
controlled by `reduction.sliding_template_n`:

- `sliding_template_n == 0` — **fixed** template. The frame the user
  built from is used unchanged for the rest of the session.
- `sliding_template_n == 1` — **slide to latest**. Each new `henNNNN.fits`
  replaces the active template.
- `sliding_template_n == N > 1` — **running average of the most recent
  N**. The autoguider keeps a FIFO ring buffer of the last N
  bg-subtracted, masked stamps. The active template is their (unweighted)
  mean. As each new `henNNNN.fits` arrives, its stamp is pushed onto
  the ring (oldest dropped); the average is recomputed in place.

A typical setting for many-hour runs is `sliding_template_n = 5–10`:
enough to suppress per-integration noise yet short enough to track real
shape evolution. Memory cost is small (8 MB × N at 2048×2048 BITPIX=16,
even less for the cropped stamp).

When the user clicks **"Build Template"**, the ring buffer is cleared
and re-seeded with the most recent `henNNNN.fits`. The GUI shows the
current fill state ("Template: 3 / 5 averaged") so the observer can
tell when the average has saturated.

The first frame after Build Template (i.e., when only one entry is in
the ring) IS a usable template — guiding starts immediately. Subsequent
arrivals just enrich the average.

If no `henNNNN.fits` is available yet (e.g., the user clicks Build
Template very early in the first integration), the GUI declines and
prompts the user to wait for the slope-fit file to land.

The template (and its ring buffer) are held in memory during a session
and are **not** persisted across restarts — they must be re-built each
time the autoguider starts.

### Per-frame measurement (2-D cross-correlation)

For each new guide image (a K-window SUTR difference, see "SUTR
difference model" above):

1. Apply the BPM master mask.
2. Apply local sky subtraction (per-row outer 1/6 medians) — same
   procedure used when building the template.
3. **2-D cross-correlation** of the bg-subtracted, masked guide image
   `D(x, y)` against the template `T(x, y)`. Brute-force evaluation
   over a ±`reduction.xcor_search_radius_px` window (default 12 px) in
   both axes:

   ```
   C(δx, δy) = Σ_y Σ_x  T(x, y) · D(x + δx, y + δy)
   ```

   Implemented vectorised in NumPy; at our stamp size (~70 k pixels) and
   search size (25×25 = 625 evaluations) this is well under 100 ms per
   frame.
4. **Sub-pixel peak refinement.** Find the integer-shift maximum
   `(δx_peak, δy_peak)` of `C`. Fit a parabola to the three correlation
   values around the peak in each axis independently:

   ```
   For X (at the peak's row):
       a, b, c = C(δx_peak − 1, δy_peak), C(δx_peak, δy_peak),
                 C(δx_peak + 1, δy_peak)
       sub_x   = 0.5·(a − c) / (a − 2b + c)
       dx_px   = δx_peak + sub_x

   For Y (at the peak's column): same thing.
   ```

   The curvature `(a − 2b + c)` in each axis is recorded per frame and
   serves as a proxy for the formal centroid uncertainty (sharper peak
   ↔ better-pinned shift).
5. **Comparison stamp** runs steps 1–4 against its own template and is
   stored alongside, but does **not** drive the control loop.

The result `(dx_px, dy_px)` is the science stamp's drift relative to its
template. Because the template embodies the desired trace position,
there is **no separate "commandable target"** — driving `(dx_px, dy_px)`
toward zero is what the controller does. (If the operator wants to lock
to a different position, they re-build the template after slewing.)

Per-frame quantities recorded for analysis:

- `dx_px`, `dy_px` — the xcor-measured shift in pixel space.
- `xcor_curvature_x`, `xcor_curvature_y` — parabola curvatures (formal
  precision indicator).
- `xcor_peak_value` — the peak correlation value (low = poor SNR /
  template mismatch).
- `trace_fwhm_x_px` — FWHM from a 1-D Gaussian fit to the column-summed
  template-aligned profile (still useful as a seeing diagnostic).
- `trace_flux_adu` — sum of bg-subtracted, mask-applied pixels in the
  stamp.
- `sky_background_adu` — median of per-row sky pedestals.
- `template_frame_number` — which `henNNNN.fits` produced the active
  template, for retrospective traceability.

`(dx_px, dy_px)` are converted to sky offsets (§6).

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
- The GUI raises an `ALERT`-level banner and (subject to
  `display.audio_alerts`) plays the warning sound exactly once on entry
  into ALERTED — see §9 "Audio alert when guiding stops."
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
file event → diff image → 2-D xcor measurement (science + comparison)
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
                  insert frames + stamp_measurements rows
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

CREATE TABLE stamp_measurements (
    frame_number          INTEGER NOT NULL,
    sutr_number           INTEGER NOT NULL,
    stamp_id              INTEGER NOT NULL,   -- 0 = science, 1 = comparison
    stamp_x_center        INTEGER,            -- detector pixel
    stamp_x_halfwidth     INTEGER,
    stamp_y_lo            INTEGER,
    stamp_y_hi            INTEGER,
    template_frame_number INTEGER,            -- which henNNNN.fits built the active template
    dx_px                 REAL,               -- xcor sub-pixel shift
    dy_px                 REAL,
    xcor_peak_value       REAL,               -- C(δx_peak, δy_peak)
    xcor_curvature_x      REAL,               -- (a − 2b + c) along X at peak; precision proxy
    xcor_curvature_y      REAL,
    trace_fwhm_x_px       REAL,
    trace_flux_adu        REAL,
    sky_background_adu    REAL,
    quality_flags         TEXT,               -- JSON
    PRIMARY KEY (frame_number, sutr_number, stamp_id),
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
- `quality.target_switch_arcsec_threshold = 20.0`
- `reduction.K = 1`, `reduction.stride = 1`
- `reduction.stamp_x_halfwidth_px = 25`
- `reduction.stamp_y_lo = 600`, `reduction.stamp_y_hi = 1980`
  (filter cutoff to filter cutoff; tune per detector / filter)
- `reduction.xcor_search_radius_px = 12`
- `reduction.sliding_template_n = 5`
  (number of recent `henNNNN.fits` to average into the active template:
  `0` = fixed; `1` = slide to latest; `N > 1` = running average of last
  N. Default 5 is a reasonable starting point for hours-long sequences;
  retune empirically.)
- `reduction.template_min_peak_value = 0.0`
  (minimum acceptable xcor peak value; below this the frame is flagged
  in `quality_flags`. Default 0 = no threshold; tune empirically.)
- `detector.y_middle_row = 1024` (placeholder)
- `detector.gain_e_per_dn = 4.0` (placeholder)
- `detector.read_noise_e = 12.0` (placeholder)
- `detector.saturation_dn = 40000`
- `display.image_stretch = "zscale"`, `display.theme_macos = "aqua"`,
  `display.theme_linux = "clam"`
- `display.audio_alerts = true`,
  `display.audio_alert_sound = "/System/Library/Sounds/Submarine.aiff"`
  (overridable to any `.wav`/`.aiff` path, or `null` to use Tk's
  `widget.bell()` only)
- `display.audio_speak_alerts = true` — when an event has a spoken
  phrase associated with it (currently only target-switch detection),
  speak it via the OS speech synthesiser (`say`/`espeak`). Set `false`
  to keep only the warning sound.

A `[files] parent_data_dir` field is supported as the default starting
location for the GUI's directory picker.

### `session.toml` (auto-saved per-session)

`~/.config/henrietta_guider/session.toml`

Holds the daily-changing state:

- `archon_watch_dir` — selected via OS folder picker each night.
- Stamp geometries (`science_stamp` and optionally `comparison_stamp`),
  each with `x_center`, `x_halfwidth`, `y_lo`, `y_hi`.

The active **template** itself is held only in memory and is **not**
persisted — it must be re-built each session from a fresh `henNNNN.fits`.

## 9. GUI

Single Tk window, layout:

```
┌─ status bar ─────────────────────────────────────────────────────────┐
│ TCS ●  │ Watcher ●  │ State: GUIDING │ Watch dir: /data/... [Change…]│
├──────────────────────────┬───────────────────────────────────────────┤
│  matplotlib live image   │  Stamps:   [Draw science] [Add comparison]│
│   - science stamp (red)  │            [Reset to defaults]            │
│   - comparison (cyan)    │                                           │
│   - template thumbnail   │  Stamp geometry (science):                │
│     overlay (small)      │    x_center:    __________ px             │
│                          │    x_halfwidth: __________ px             │
│                          │    y_lo:        __________ px             │
│                          │    y_hi:        __________ px             │
│                          │                                           │
│                          │  Template:  built from hen0042.fits       │
│                          │             avg: 3 / 5  N=[__5__] [Apply] │
│                          │             [Build Template]              │
│                          │                                           │
│                          │  Loop:     [START]  [STOP]  [PAUSE]       │
│                          │  Tools:    [Estimate K]  [Settings…]      │
├──────────────────────────┴───────────────────────────────────────────┤
│   Time series (scrollable):                                          │
│     dx_px, dy_px (rejected / paused frames marked)                   │
│     trace_fwhm_x_px                                                  │
│     trace_flux_adu       — science (solid), comparison (dashed)      │
│     sky_background_adu                                               │
│     xcor_peak_value      — drops on cloud / template mismatch        │
│     commands sent (RA, Dec)                                          │
└──────────────────────────────────────────────────────────────────────┘
```

### State machine

```
IDLE ─ user draws stamp(s); no template yet ─►
REFERENCE_PENDING ─ adjust stamp(s); a henNNNN.fits has arrived;
                    click "Build Template" ─►
REFERENCE_SET ─ template in memory; click "Start Guiding" ─►
GUIDING ─ ALERTED auto-entered/exited on out-of-family ─►
(STOP returns to REFERENCE_SET; PAUSE freezes commands without losing state)
```

Across-target operation: STOP → slew TCS manually → wait for the next
`henNNNN.fits` (end of fresh first integration) → click "Build
Template" → REFERENCE_PENDING with the same stamps still drawn.

### Watch directory

Opens an OS-native folder picker (`tkinter.filedialog.askdirectory`) at
startup if the previously-saved `archon_watch_dir` doesn't exist, and on demand
via the **[Change…]** button in the status bar. The button is enabled in
**every** state, including `GUIDING`.

On change, regardless of starting state:

1. Stop the `watchdog` observer.
2. Clear the rolling SUTR buffer (no reads carry across).
3. Discard the in-memory template (it was tied to the previous
   directory's frames).
4. Restart the observer on the new path.
5. Persist `archon_watch_dir` to `session.toml`.
6. Transition the state machine: from any of `REFERENCE_SET`, `GUIDING`,
   `ALERTED`, or `PAUSED`, the state drops to **`REFERENCE_PENDING`**;
   from `IDLE`, it stays in `IDLE`. Stamp geometry is preserved
   (detector-frame and not directory-bound). The user must click "Build
   Template" on a fresh `henNNNN.fits` from the new directory to resume
   guiding.

### Settings dialog

`ttk.Notebook` with tabs: **Loop**, **Quality**, **Reduction**, **Files**,
**TCS**, **Display**. Maps directly onto the config sections in §8.

### "Estimate K" tool

Opens a modal dialog. Runs a Monte Carlo simulator on the current
template:

1. Compute expected photoelectrons per pixel from the template (a
   slope-fit `henNNNN.fits` already in memory) and
   `detector.gain_e_per_dn`.
2. For `K ∈ {1, 2, 3, 4, 5}`:
    - Build 50 noisy realisations of a K-window difference image,
      including Poisson shot noise and read-noise scaled by `√(2/K)`.
    - Run each realisation through the same 2-D xcor + parabolic-peak
      pipeline used for live guiding (against the same template).
3. Display a table `K → RMS(dx_px), RMS(dy_px)` and recommend the
   smallest K with RMS below a configurable threshold.
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
- **ERROR** (red) — file watcher stalled / TCS disconnected / template
  build failed. Requires user action.

### Audio alert when guiding stops

Whenever guiding effectively stops — i.e., the state machine transitions
into one of the conditions below — the GUI plays a short, gentle warning
sound:

- **stale-frame timeout** (no new SUTRs within
  `quality.stale_frame_timeout_s`) — guiding has stopped because the
  Archon is no longer producing frames.
- **target switch — pointing jump** (>20″ on-sky from previous frame).
  Full alert: warning sound + **spoken phrase** ("target change
  possible") via the OS speech synthesiser (`say` on macOS, `espeak` or
  equivalent on Linux). Spoken alerts can be disabled with
  `display.audio_speak_alerts = false` (default `true`) while still
  keeping the warning sound.
- **target switch — OBJECT-keyword-only change** (no pointing jump).
  Soft signal: just a brief system beep (Tk `widget.bell()`), no spoken
  phrase, no warning sound, no state change. See §4 "Target-switch
  detection."
- **out-of-order or repeated SUTR / backwards frame number**. The
  Archon reordered something — rare on a new system, but worth surfacing
  audibly. Plays the warning sound + a yellow `WARN` banner ("frame %d:
  out-of-order SUTR %d after %d"). No spoken phrase. Guiding continues
  (the file is discarded; the next valid file is processed normally).
  *Skipped* SUTRs and *skipped* frame numbers do not trigger audio —
  see §4 "Sequential-order sanity checks."
- **TCS disconnect** — guiding can't proceed because commands are not
  reaching the telescope.
- **template build failure** — Build Template was clicked but the
  source `henNNNN.fits` couldn't produce a usable template (read error,
  too few unmasked pixels in the stamp, or zero variance after sky
  subtraction).
- **out-of-family ALERT entry** — guiding has paused; commands are
  suppressed pending self-recovery. Plays only on *entry* into the
  ALERTED state, not for each rejected frame, and not again on auto-
  resume back to GUIDING.

The sound is intentionally subtle — a short ping or "blip", not a klaxon.
The intent is "hey, look at the screen," not "drop everything." On
macOS the default sound is the system **Submarine** (or **Tink**)
(`/System/Library/Sounds/`); on Linux the default falls back to Tk's
`widget.bell()` until a configured sound file is available. The full
file path can be overridden in config (`display.audio_alert_sound`),
including pointing at a custom `.wav`. Audio alerts can be disabled
entirely with `display.audio_alerts = false` (default `true`).

The audio is played from a non-blocking subprocess (`afplay` on macOS,
`paplay`/`aplay` on Linux) so a slow audio system never stalls the GUI
or the worker thread. The same applies to the speech subprocess (`say`
or `espeak`). If a subprocess fails, we log a `WARNING` and fall back to
`widget.bell()`; the alert banner is unaffected.

No audio is played for routine state changes (operator-driven STOP /
PAUSE / Build Template), to avoid alert fatigue.

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
- `DEBUG`: every diff image, xcor peaks and curvatures, dead-banded
  errors, file events.
- `WARNING`: unexpected filenames, TCS pacing throttles, mid-run mask file
  changes, malformed FITS headers.
- `ERROR`: TCS connection drops, template build failures, FITS read
  errors, watcher death.

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
- `measure.local_sky_subtraction`: synthetic stamp with known per-row
  pedestal — recovers the pedestal to numerical precision; bad-pixel
  mask correctly excludes flagged pixels from the row median.
- `measure.xcor_2d` end-to-end on synthetic data: build a template, shift
  it by a known sub-pixel `(dx, dy)`, add Poisson + read noise, push it
  through the full pipeline (sky-sub + xcor + parabolic peak), assert
  recovered shift within 0.1 px (X) and 0.05 px (Y) of injected truth on
  bright synthetics.
- `measure.xcor_2d` parabolic-peak edge cases: peak at the edge of the
  search window (no neighbour), flat correlation surface (degenerate
  curvature), single-pixel-wide peak.
- `out_of_family` detector: trigger and auto-resume on synthetic series.

### Integration (`tests/integration/`)

A `FakeArchon` writes a sequence of FITS frames into a tempdir,
producing per-SUTR `henNNNN_sssr.fits` files at a configurable cadence
plus a slope-fit `henNNNN.fits` at end of integration (no atomic rename
— the watcher's settle-timer is exercised). The test launches the core
in a thread, lets it process the sequence, and asserts:

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

We rely on the medianing in the pipeline to suppress CR contamination —
the per-row sky median, the broad 2-D xcor peak fit (a single bright
pixel barely moves the parabolic peak across an ~70 k-pixel stamp),
and the running-stats out-of-family check for `flux` and `FWHM`. A CR
has to survive (a) the bad-pixel mask, (b) the broad xcor peak fit,
and (c) the out-of-family check before it can corrupt a guide command.
Stacking those, dedicated CR detection adds little — and is therefore
omitted in v1.

### CI

GitHub Actions on push: `uv sync && make test && make lint`.

## 12. Future features (designed-for, not built in v1)

- **Dithering.** A `dither_pattern` config block + a target-modifier between
  controller and encoder. v1 spec: **uniform random draw** of `(dRA, dDec)`
  on `[-A, +A]` per axis (independent), redrawn each new integration when
  `_001` arrives. Default off.
- **Adaptive `sliding_template_n`.** v1 uses a fixed running-average
  length set in config. Future work: auto-tune N based on the observed
  noise of the per-frame `(dx_px, dy_px)` time series — shorter N when
  the trace is changing fast (acquisition / weather), longer N when
  stable. Off until commissioning shows it's needed.
- **FFT-based xcor.** v1 uses brute-force xcor over a ±12 px window,
  which is plenty fast at our stamp size. If we ever need a much larger
  search radius (e.g., for acquisition-class moves), drop in
  `scipy.signal.fftconvolve`-style FFT xcor.
- **Multiple comparison stamps.** Schema already supports `stamp_id ≥ 2`.
- **PI / PID controllers.** `Ki`, `Kd` already in config; needs the integral
  / derivative state machinery and anti-wind-up.
- **TCS status channel.** If a status / pointing-readback protocol is
  surfaced, sanity-checking against current TCS state can be added.
- **Two-process daemon.** Wrap `core` as a small ZMQ/HTTP daemon and port
  the GUI to a thin client. Useful only if remote operation or surviving
  GUI crashes ever become requirements.
- **"Best K + best Y position" Monte Carlo sweep.** Extend the Estimate-K
  dialog to sweep stamp position along the spectrum and recommend an
  optimum location for the science stamp.

## 13. Open questions

Tracked in `Questions-for-William.md` at the repo root. Summary by area:

- TCS settle time, `guider_cmd_processing` semantics, status / telemetry
  channel availability, ACK behaviour.
- Archon file-write convention (in-place, no atomic rename — preliminary
  William answer, to confirm), output directory, FITS keyword inventory
  (HA, Dec, PA, airmass, temperature, focus, exposure time, UTC, OBJECT
  for target-switch detection), intermediate-read availability (already
  confirmed: not available).
- Detector parameters: gain (placeholder 4 e⁻/DN), read noise, saturation
  (40000 DN), `y_middle_row`.
- Bad-pixel mask source / lifecycle (format already confirmed via
  `bpm_25apr2026.fits`: 7-HDU MEF, primary HDU is 1=good).
- PA convention, plate scale, detector orientation parity.
- Operations: who toggles `guider_cmd_processing`, behaviour around target
  acquisitions.

Each of these is a placeholder in `config.toml` or behind a config-flag so
the autoguider remains buildable and tunable while answers come in.
