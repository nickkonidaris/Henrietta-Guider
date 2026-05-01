# Henrietta-Guider — Brainstorm Status

**Last updated:** 2026-05-01 (post-flight resumption)

## Status: ridge → 2-D xcor rewrite COMPLETE in the spec

`grep -i ridge` on the spec is empty. All sections (§1–§13) now describe
the 2-D cross-correlation algorithm consistently. The schema renames
`box_measurements` → `stamp_measurements` with xcor-specific columns
(`xcor_peak_value`, `xcor_curvature_x/y`, `template_frame_number`,
stamp geometry).

**Still to do before declaring the spec ready for the writing-plans
skill:**

- Re-run the **spec-document-reviewer** for round 3 against the now-
  consistent doc.
- Update **`mockups/gui_mockup.py`** so the rendered PNG matches the
  new GUI layout (Stamp/Template panel instead of Ridge controls;
  `xcor_peak_value` time-series row).
- After review passes: ask the user to read the spec, then invoke the
  `writing-plans` skill.

## Findings during the rewrite

- BPM file `bpm_25apr2026.fits` is a 7-HDU MEF. **HDU 0 is the master,
  1=good convention, 0.29 % bad.** HDUs 1–6 are diagnostic categories
  (`COVERAGE`, `DEAD`, `HOT`, `NOISY`, `NOISY_DARK`, `REF_PIX`). Reference
  pixels are flagged in `REF_PIX` and folded into HDU 0; no separate ref-
  pixel correction needed.
- `bpm_25apr2026.fits` is gitignored (~28 MB, calibration not source).
- `experiments/watchdog_close_event_test.py` confirmed: macOS does not
  fire `on_closed` via either watchdog backend (kqueue or fsevents).
  Settle-timer is correct; tightened to 0.2 s in §4.
- `henNNNN.fits` (no SUTR suffix) is the slope-fit final from the Archon,
  highest SNR available. Template should be built from this, not from a
  SUTR difference.

## Original handoff (still valid)

**Last updated:** 2026-04-30 (end of first brainstorm session)

A compact handoff so the next session can resume without re-reading the
whole transcript.

## Where we are in the process

Following the `superpowers:brainstorming` skill. Phase progress:

- [x] Explore project context (repo was greenfield)
- [x] Ask clarifying questions
- [x] Propose 2–3 approaches
- [x] Present design section-by-section (architecture → frame ingestion →
      control loop → data store → GUI → config → testing → futures —
      all 9 sections approved by the user)
- [x] Write design doc → `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`
- [/] **Spec review loop — IN PROGRESS.** Round 1 fixes committed; round 2
      review came back with 5 issues still pending (see "Open" below).
- [ ] User reviews the spec file
- [ ] Transition to `writing-plans` skill to create the implementation plan

## What is decided

### Stack
- Python **3.14** (regular GIL build), pinned via `.python-version`
- **`uv`** for environment + lockfile + interpreter management
- stdlib **`dataclasses` + `tomllib`** for config (no pydantic)
- **Tk + ttk + matplotlib** for the GUI (`aqua` theme on macOS, `clam` on
  Linux)
- **`watchdog`** for file events, **`astropy`** for FITS, **numpy/scipy**
- **SQLite** for the per-frame archive (single file, WAL mode)
- **Threading** (not asyncio): main thread = Tk; worker thread = watcher +
  reduce + control + TCS + store; thread-safe `queue.Queue` between them
- **macOS** first, **Linux** later

### Architecture
- Single Python package `henrietta_guider/` with `core/` (no UI imports),
  `cli/`, `gui/`. CLI and GUI are thin frontends over the same `core`.
- Core modules: `watcher`, `reduce`, `centroid`, `controller`,
  `tcs_client`, `store`, `geometry`, `monte_carlo`, `config`.

### TCS wire protocol
- Reverse-engineered from the C++ parser into `Wireformat.md`.
- 6-byte ASCII: `G xx yy <CR>` (CR only — no LF).
- `xx` → RA, `yy` → Dec arcsec offset, in 0.05″ steps.
- Wire range −2.45″…+2.50″; we set `loop.max_command_arcsec = 2.45` for
  symmetry.
- Fire-and-forget; TCS silently drops commands while slewing or while its
  `guider_cmd_processing` flag is false. We pace with a min-interval gate.
- `xx` and `yy` are sky-frame, so the instrument computer applies the PA
  rotation before encoding.

### Frame model
- Archon writes `henNNNN_sss.fits`. `NNNN` = integration number, `sss` =
  SUTR sample within that integration. Many `_sss` files per integration.
- Each `_sss.fits` is a raw non-destructive read.
- Guide image = `mean(reads[N+1..N+K]) − mean(reads[N+1−K..N])`.
- Default **K = 1**, **overlapping windows** (stride = 1). Both settable.
- Buffer **clears at frame-number boundaries** (a new `_001` is a detector
  reset; never mix reads across resets).
- "Estimate K" Monte Carlo tool: simulates 50 noisy realisations per K to
  recommend a value.

### Region geometry
- Up to 5 boxes per session: 1 science + 2 science bg (independently
  positioned/resizable) + 1 optional comparison + 2 comparison bg.
- The comparison box is purely diagnostic — never drives control.
- Default bg placement is flanking the science box at the same size; the
  user may resize/reposition each independently around neighbour stars.
- Background subtraction = pooled median over both bg boxes' unmasked
  pixels.
- Bad-pixel mask loaded once at startup, applied via `numpy.ma.MaskedArray`.

### Measurement (currently spec'd as ridge-relative, but see "Open")
- Linear ridge: `X_ridge(Y) = ridge_x_center + tan(angle) · (Y − Y_DET_MID)`.
  `ridge_x_center` is anchored at the detector's middle row.
- One-time calibration on a high-SNR frame fits the ridge and captures a
  reference 1-D flux profile along it. Manual override is supported (drag
  handles, type coefficients, click two points).
- Per-frame: `dX_px` from per-row centroids relative to the ridge (sigma-
  clipped robust mean across rows); `dY_px` from cross-correlation of the
  current 1-D profile against the reference profile.
- Refit ridge each new target. Both `(ridge_x_center, ridge_angle_deg)` are
  stored in `box_measurements` per frame for retrospective analysis.

### Control loop
- Per-axis P controller (Ki / Kd hooks already in the dataclass). Default
  `Kp = 0.5`, `deadband = 0.025″`.
- **Clip-don't-split**: errors > 2.45″ get clipped; the residual is picked
  up on the next frame (the next measurement is always more authoritative
  than our model).
- Pacing: min interval between G commands enforced in `tcs_client`; loop
  may want to send within the window — the client returns False and we
  defer to the next tick.
- **Out-of-family** quality control: running median + MAD over last 20
  frames on `flux`, `FWHM`, `sky_bg`, `dx_px`, `dy_px`. If any metric is
  >5σ off, state → `ALERTED`, no command issued. Auto-resume after 3 in-
  family frames. Warm-up of 10 in-family frames before checks engage.

### State machine
`IDLE → REFERENCE_PENDING → REFERENCE_SET → GUIDING`.
`ALERTED` and `PAUSED` are sub-states reached from `GUIDING`. Watch-dir
change collapses to `REFERENCE_PENDING` from any non-IDLE state.

### Data store
SQLite, two tables: `frames` (one row per `(frame_number, sutr_number)`,
common per-frame data + the *science* box's command/error) and
`box_measurements` (one row per box per frame, with ridge + measurements).
Single DB file, WAL, indexed on time and `(ha, dec)`.

### Persistence
- `~/.config/henrietta_guider/config.toml` — settings.
- `~/.config/henrietta_guider/session.toml` — daily state (boxes, ridge,
  targets, watch dir).
- `~/.henrietta_guider/henrietta_guider.db` — measurements.
- `~/.henrietta_guider/logs/` — rotating logs.

### GUI
- See `mockups/gui_mockup.png` (and `mockups/gui_mockup.py` for the source).
- One window: status bar / live image with overlays / control panel /
  alert banner / four stacked time-series.
- OS-native folder picker (`tkinter.filedialog.askdirectory`) accessible
  from the status bar.

### Future / non-goals
- **Future:** dithering (uniform random draw, off by default), absorption-
  feature Y-locking, quadratic ridge, multiple comparison boxes, PI/PID,
  TCS status channel, two-process daemon, K + Y-position MC sweep.
- **Non-goals:** target acquisition, fault recovery, multi-target
  scheduling, dedicated cosmic-ray detection (pipeline medianing handles
  it), online mask updates, high-Hz live image.

## What is open

### Spec review — round 2 issues to fix

The reviewer signed off on the round-1 fixes for issues 1, 3, 4, 5, 7, 8.
**Still pending:**

1. **§6 Loop wiring diagram** still says `LOCKED`; should be `GUIDING`.
2. **§8** still mentions "brainstorm transcript"; remove that phrase, point
   only at `core/config.py`.
3. **§7 schema** — add `'paused'` to the `cmd_suppressed_by` enum, OR
   document that PAUSED writes no row.
4. **§4 wording inversion** — "auto-accepted fit transitions to
   `REFERENCE_PENDING`" is backwards; per §9, `REFERENCE_PENDING` is
   *before* "Save Reference," `REFERENCE_SET` is *after*. Re-word so
   auto-accept lands in `REFERENCE_PENDING` (still awaiting Save) — that
   IS the desired behaviour, just wording.
5. **§9 state diagram** — add `PAUSED` as a node (it's referenced but not
   drawn).

After applying these, re-dispatch the reviewer for round 3. If approved,
hand off to the user for review of the spec file.

### Algorithm decision: 2D cross-correlation vs ridge-relative

User mentioned that 2D xcor "can work to find the pixel shifts." Options:

- (a) **Replace** — drop ridge fitting from the active pipeline; use a 2D
      xcor of `(current bg-subtracted box image)` against `(reference box
      image)` to get `(dX_px, dY_px)`. Still record the calibrated ridge
      angle once per target as a diagnostic. Simpler spec and code.
- (b) **Both, selectable** via `reduction.measurement_method`.
- (c) **Note for v2** — stay with ridge-relative for v1.

**Awaiting user's choice — first thing to ask tomorrow.**

### Tracked external dependencies (William, the SWE)

`Questions-for-William.md` at the repo root. Categories: TCS protocol
(settle time, ACK semantics, status channel), Archon delivery (atomic-rename
confirmation, output dir, FITS keyword inventory, intermediate reads),
detector parameters (gain, RN, saturation, `y_middle_row`), bad-pixel mask
(source, format, lifecycle), instrument geometry (PA convention, plate
scale, parity), operations (`guider_cmd_processing` toggling, behaviour at
acquisitions). Until answers come in, defaults are placeholders in
`config.toml`.

## Immediate next steps when resuming

1. **Decide on the xcor question** — (a)/(b)/(c) above.
2. **Apply the 5 round-2 review fixes** to the design doc.
3. **Re-dispatch the spec-document-reviewer** — round 3.
4. If approved, **ask the user to read the spec** before we move on.
5. **Invoke the `writing-plans` skill** to turn the spec into an
   implementation plan.

## File map

```
Henrietta-Guider/
├── BRAINSTORM-STATUS.md                                ← you are here
├── Wireformat.md                                       TCS wire protocol
├── Questions-for-William.md                            tracked external Qs
├── docs/superpowers/specs/
│   └── 2026-04-30-henrietta-autoguider-design.md       the spec (in review)
└── mockups/
    ├── gui_mockup.py                                   renders the PNG
    └── gui_mockup.png                                  static GUI mockup
```

GitHub: https://github.com/nickkonidaris/Henrietta-Guider
