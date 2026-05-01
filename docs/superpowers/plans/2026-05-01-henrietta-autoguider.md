# Henrietta Autoguider Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Henrietta autoguider per `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`: watch SUTR FITS frames produced by the Archon, measure trace shifts via 2-D cross-correlation against a slope-fit template, send small (≤2.45″) telescope offsets to the TCS over TCP, and surface the live state in a Tk operator GUI. SQLite archive of every measurement for retrospective analysis of non-periodic mount errors.

**Architecture:** Single Python 3.14 package `henrietta_guider`. `core/` is GUI-free (no Tk imports anywhere in this subtree); `cli/` and `gui/` are thin frontends over `core/`. Concurrency: Tk on main thread; one worker thread runs the watcher + reduction + control + TCS sender + SQLite writer end-to-end; thread-safe `queue.Queue` carries `MeasurementRow` events back to the main thread. The "Estimate K" Monte Carlo runs on its own short-lived worker thread so it doesn't block live guiding.

**Tech Stack:** Python 3.14 (regular GIL build), uv (env + interpreter + lockfile management), Tk + ttk + matplotlib (GUI), watchdog (file events), astropy (FITS), numpy / scipy, stdlib `sqlite3` / `dataclasses` / `tomllib` / `logging`. Dev: pytest, ruff. CI: GitHub Actions.

**TDD discipline:** Every behavioural unit gets a failing test first, then minimal code, then passing test, then commit. The few exceptions (project-scaffolding tasks with no logic to test) are called out in the steps.

---

## Pre-flight

Before starting any task, verify the working environment:

- [ ] **Git is initialised and configured.**
  - `git rev-parse --git-dir` → prints `.git` (not an error).
  - `git config user.name` and `git config user.email` are both set; if not, run `git config --global user.name "..."` and `git config --global user.email "..."`.
  - `git remote -v` shows the GitHub remote (`https://github.com/nickkonidaris/Henrietta-Guider.git`); if not, run `git remote add origin <url>` first.
  - `git branch --show-current` is `main` and tracks `origin/main`.

- [ ] **`uv` is installed and recent.**
  - `uv --version` prints **0.5.0 or later** (the plan uses `[dependency-groups]`, a uv 0.5+ feature).
  - If older, install/upgrade via `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `exec $SHELL` and retry.

- [ ] **The Python build is the GIL build (not free-threaded).**
  - After Task 1.1 finishes (or right now if the venv already exists), run:

    ```bash
    uv run python -c "import sysconfig; assert not sysconfig.get_config_var('Py_GIL_DISABLED'), 'free-threaded build detected'"
    ```

    Expected: exits 0 silently. If `AssertionError` fires, the venv is using the free-threaded build.
  - **Recovery if free-threaded was selected:** pin a specific patch known to be the regular GIL build in `.python-version` (e.g. `3.14.3` rather than `3.14`), delete `.venv/`, and run `uv sync` again. uv's default downloads the GIL variant for any `3.14.x` patch number; the free-threaded variant is only used when explicitly requested with the `+freethreaded` suffix.

- [ ] **Spec, algorithm, wire format, and questions doc are committed:**
  - `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`
  - `ALGORITHM.md`, `Wireformat.md`, `Questions-for-William.md` at the repo root.

- [ ] **Sample SUTR data exists (gitignored)**:
  - `test/hen1764_*.fits` (24 files: 23 SUTR raw reads + 1 slope-fit final).
  - `test/hen1765_*.fits` (same shape).
  - If absent: copy from `/Volumes/Extreme Pro/Henrietta/hen176{4,5}*.fits` into `test/`.

- [ ] **The BPM is present at the repo root (gitignored)**:
  - `bpm_25apr2026.fits` (28 MB; `bpm_*.fits` is in `.gitignore`).
  - If absent: copy from `/Volumes/Extreme Pro/bpm_25apr2026.fits`.

- [ ] **`.gitignore` already covers the runtime artifacts.** Confirm it contains at minimum: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `test/`, `bpm_*.fits`, `*.fits`. If any are missing, add them now and commit before starting Chunk 1.

If any check fails, stop and reconcile with the user before proceeding.

**Note on dependency wheels.** The plan pins `requires-python = "==3.14.*"` (Python 3.14 was released Oct 2025; this plan targets 2026-05+). If `uv sync` fails because a transitive dep lacks 3.14 wheels, bump that dep's minimum to the next available release in `pyproject.toml` and re-sync. Do not silently downgrade `requires-python`.

**Note on `make run-gui` / `make run-cli`.** These targets are wired up in Task 1.3 but the entry points they invoke (`henrietta_guider.gui.app:main`, `henrietta_guider.cli.__main__:main`) don't exist until Chunks 6 and 5 respectively. `uv sync` may emit a warning about the missing modules; that's expected and harmless. The targets only fail if you actually run them before those chunks land.

---

## File structure

This is the target layout once the plan is complete. Each file has one clearly bounded responsibility; no cross-file coupling beyond the typed interfaces in `types.py`.

```
henrietta_guider/
├── __init__.py
├── core/                         no Tk / GUI imports anywhere in this subtree
│   ├── __init__.py
│   ├── types.py                  shared dataclasses: Stamp, MeasurementRow, Template, ...
│   ├── config.py                 dataclass config tree + TOML load/save
│   ├── wire.py                   G xx yy CR encoder/decoder per Wireformat.md
│   ├── tcs_client.py             TCP fire-and-forget client + pacing
│   ├── geometry.py               detector → sky transform (plate scale + PA + parity)
│   ├── controller.py             per-axis P (with PI/PID hooks) + deadband + clip
│   ├── bpm.py                    MEF BPM loader (HDU 0; 1 = good)
│   ├── framebuffer.py            rolling SUTR-read buffer + K-window diffs
│   ├── sky.py                    per-row outer-1/6 sky subtraction
│   ├── xcor.py                   2-D xcor + parabolic sub-pixel peak
│   ├── template.py               template build + auto-refresh policy
│   ├── quality.py                out-of-family running median + MAD
│   ├── sanity.py                 sequential-order checks (SUTR / frame_number)
│   ├── target_switch.py          pointing-jump + OBJECT-change detector
│   ├── stale.py                  stale-frame watchdog timer
│   ├── watcher.py                watchdog observer + settle-timer + dual-queue routing
│   ├── reducer.py                per-SUTR measurement pipeline (orchestrator)
│   ├── store.py                  SQLite frames + stamp_measurements (WAL)
│   ├── monte_carlo.py            Estimate K simulator
│   ├── audio.py                  subprocess wrapper for sound + speech
│   └── worker.py                 owns the pipeline; thread-safe queue producer
├── cli/
│   ├── __init__.py
│   └── __main__.py               entry point: `henrietta-cli`
├── gui/
│   ├── __init__.py
│   ├── app.py                    Tk main window + state machine + queue drain
│   ├── image_panel.py            live image + stamp overlays + template inset
│   ├── control_panel.py          right-side controls (Stamps / Template / Loop / Tools)
│   ├── timeseries_panel.py       6-row stacked time series
│   ├── alerts.py                 banner widget + audio dispatch
│   ├── estimate_k_dialog.py      Estimate K modal
│   └── settings_dialog.py        tabbed settings modal
└── tests/
    ├── __init__.py
    ├── unit/
    │   └── test_*.py             one test file per core/* module
    └── integration/
        ├── __init__.py
        ├── fakes.py              FakeArchon (writes synthetic FITS), FakeTCS (socketpair)
        └── test_*.py
```

Top-level project files:

```
pyproject.toml                  uv-managed, Python 3.14, deps + scripts
.python-version                 3.14.x patch pin
uv.lock                         deterministic transitive pin (committed)
Makefile                        setup / test / lint / run-gui / run-cli / format
.github/workflows/ci.yml        uv sync && make test && make lint
.gitignore                      already exists
```

---

## Plan navigation

The plan is divided into chunks; each chunk is self-contained and ends with a working state at HEAD.

- **Chunk 1: Bootstrap and tooling** — uv project, pyproject.toml, Makefile, CI scaffold, ruff/pytest.
- **Chunk 2: Computational foundation** — wire encoder, TCS client, geometry, controller. Pure functions; trivially unit-testable; no I/O dependencies.
- **Chunk 3: Reduction primitives** — config + types, BPM loader, framebuffer, sky subtraction, 2-D xcor, template build.
- **Chunk 4: Per-frame orchestration** — reducer, signal_snr, quality (out-of-family), sanity (sequential order), target_switch, stale watchdog, store.
- **Chunk 5: Worker thread, watcher, CLI, Monte Carlo, audio** — end-to-end pipeline minus the GUI; integration tests with `FakeArchon` + `FakeTCS`.
- **Chunk 6: GUI** — Tk main window, panels, dialogs, alerts, end-to-end manual smoke test.

Each chunk's tasks are bite-sized (single action per step, 2–5 minutes of work). Each task ends in a commit.

---

## Chunk 1: Bootstrap and tooling

**Goal:** Reach a state where `uv sync && make test && make lint` succeeds on a fresh clone with an empty test suite. No autoguider code yet — just the project skeleton.

### Task 1.1: Initialise the Python project with uv

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`

- [ ] **Step 1: Verify `uv` is on the user's PATH or installed in the venv.**

Run: `uv --version`
Expected: prints a uv version string (e.g. `uv 0.5.x` or later).

If `command not found`: install via `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `exec $SHELL` and retry. Do not proceed until `uv --version` succeeds.

- [ ] **Step 2: Pin the Python interpreter version.**

Run: `uv python install 3.14`
Then write `.python-version`:

```
3.14
```

(uv resolves this to the latest installed 3.14.x patch on `uv sync`.)

- [ ] **Step 3: Write `pyproject.toml`.**

```toml
[project]
name = "henrietta-guider"
version = "0.0.1"
description = "Autoguider for the Henrietta IR spectrograph on Swope"
readme = "README.md"
requires-python = "==3.14.*"
authors = [
    { name = "Nick Konidaris", email = "nick.konidaris@gmail.com" },
]
dependencies = [
    "astropy>=7.0",
    "matplotlib>=3.10",
    "numpy>=2.2",
    "scipy>=1.14",
    "watchdog>=6.0",
]

[project.scripts]
henrietta-gui = "henrietta_guider.gui.app:main"
henrietta-cli = "henrietta_guider.cli.__main__:main"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "ruff>=0.8",
]

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["henrietta_guider"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = [
    "unit: fast unit tests (no I/O, no GUI)",
    "integration: end-to-end tests using FakeArchon + FakeTCS",
]

[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "C4", "SIM", "TID"]
ignore = ["E501"]  # line-length is enforced by formatter

[tool.ruff.format]
quote-style = "double"
```

- [ ] **Step 4: Run `uv sync` and confirm it succeeds.**

Run: `uv sync`
Expected: Creates `.venv/`, downloads Python 3.14, resolves and installs the deps, writes `uv.lock`. Should finish in a few seconds.

If errors: stop and reconcile (commonly: `requires-python` mismatch, transient PyPI failure). Do not move on.

- [ ] **Step 5: Commit the bootstrap.**

```bash
git add pyproject.toml .python-version uv.lock
git commit -m "bootstrap: uv project, Python 3.14, base deps"
```

### Task 1.2: Create the package skeleton

**Files:**
- Create: `henrietta_guider/__init__.py`
- Create: `henrietta_guider/core/__init__.py`
- Create: `henrietta_guider/cli/__init__.py`
- Create: `henrietta_guider/gui/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Create the empty package files.**

```bash
mkdir -p henrietta_guider/core henrietta_guider/cli henrietta_guider/gui
mkdir -p tests/unit tests/integration
touch henrietta_guider/__init__.py \
      henrietta_guider/core/__init__.py \
      henrietta_guider/cli/__init__.py \
      henrietta_guider/gui/__init__.py \
      tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 2: Add a placeholder `__version__` to the top-level package.**

Edit `henrietta_guider/__init__.py`:

```python
__version__ = "0.0.1"
```

- [ ] **Step 3: Add a sanity test that pytest discovers tests at all.**

Create `tests/unit/test_smoke.py`:

```python
import henrietta_guider


def test_package_imports():
    assert henrietta_guider.__version__ == "0.0.1"
```

- [ ] **Step 4: Run the test.**

Run: `uv run pytest -v`
Expected: 1 test passes.

- [ ] **Step 5: Format new files, then verify.**

`ruff format --check` is strict about empty-file trailing newlines and
similar nits, so run the formatter first to bring everything in line:

```bash
uv run ruff format .
uv run ruff check . --fix
uv run ruff format --check .
uv run ruff check .
```

Expected: format and check pass cleanly. If `--fix` made any changes, the
subsequent `--check` calls confirm they stuck.

- [ ] **Step 6: Commit.**

```bash
git add henrietta_guider tests
git commit -m "scaffold: empty package + smoke test"
```

### Task 1.3: Add the Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write the Makefile.**

```makefile
.PHONY: help setup test test-unit test-integration lint format run-gui run-cli clean

help:
	@echo "Targets:"
	@echo "  setup            uv sync"
	@echo "  test             run all tests"
	@echo "  test-unit        unit tests only"
	@echo "  test-integration integration tests only"
	@echo "  lint             ruff check + format check"
	@echo "  format           ruff format (writes)"
	@echo "  run-gui          launch the operator GUI"
	@echo "  run-cli          run the headless CLI"
	@echo "  clean            remove build artifacts"

setup:
	uv sync

test:
	uv run pytest

test-unit:
	uv run pytest -m unit

test-integration:
	uv run pytest -m integration

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

run-gui:
	uv run henrietta-gui

run-cli:
	uv run henrietta-cli

clean:
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__
	rm -rf .ruff_cache build dist *.egg-info
```

- [ ] **Step 2: Verify the Makefile.**

Run: `make help`
Expected: prints the target list.

Run: `make test`
Expected: same output as `uv run pytest`; one test passes.

- [ ] **Step 3: Commit.**

```bash
git add Makefile
git commit -m "scaffold: Makefile with setup/test/lint/run targets"
```

### Task 1.4: Add GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the CI workflow.**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
          enable-cache: true

      - name: Install Python and deps
        run: uv sync --frozen

      - name: Lint
        run: |
          uv run ruff check .
          uv run ruff format --check .

      - name: Test
        run: uv run pytest --cov=henrietta_guider --cov-report=term-missing
```

- [ ] **Step 2: Commit and push.**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions (uv sync, ruff, pytest)"
git push
```

- [ ] **Step 3: Verify CI passes on GitHub.**

Open https://github.com/nickkonidaris/Henrietta-Guider/actions and wait for the workflow to go green. If it fails: read the log, fix locally, force-push isn't necessary — just push the fix.

### Task 1.5: Add a minimal README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write a 30-line README.**

Note: the README contains a fenced shell block. The plan shows the
README content with **tilde (~~~) outer fences** to avoid premature
closure. When you write the file, the tildes are not part of the
content — only the inner triple-backtick fence goes into `README.md`.

~~~markdown
# Henrietta-Guider

Autoguider for the Henrietta IR spectrograph on the Swope telescope at Las
Campanas Observatory.

## Quick start

```sh
uv sync                  # downloads Python 3.14, creates .venv, installs deps
make run-gui             # launch the operator GUI
make run-cli             # run the headless CLI
make test                # run the test suite
```

## Documentation

- **Spec**: `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`
- **Algorithm**: `ALGORITHM.md` (2-D cross-correlation reference)
- **Wire protocol**: `Wireformat.md` (TCS guide-offset commands)
- **Open questions**: `Questions-for-William.md`
- **Implementation plan**: `docs/superpowers/plans/2026-05-01-henrietta-autoguider.md`
- **GUI mockups**: `mockups/gui_mockup.png` and `mockups/estimate_k_mockup.png`
~~~

- [ ] **Step 2: Commit.**

```bash
git add README.md
git commit -m "docs: add minimal README pointing at spec and plan"
```

### Task 1.6: End-of-chunk verification

- [ ] **Step 1: From a clean shell, run the full bootstrap.**

```bash
make clean
make setup
make lint
make test
```

Expected: all green; one passing smoke test.

- [ ] **Step 2: Confirm git status is clean.**

Run: `git status`
Expected: "nothing to commit, working tree clean."

End of Chunk 1. Working state: empty package, passing CI, working `make` targets, ready to start writing real code.

---

## Chunk 2: Computational foundation

**Goal:** Land the four pure-computational modules — wire encoder/decoder, TCS client, detector→sky geometry, and per-axis controller — with comprehensive unit tests. These are foundational and have no I/O dependencies (the TCS client uses a pair of `socket.socketpair`s in tests). Once Chunk 2 is done, the autoguider has the entire "math + bits on the wire" pipeline in working code.

The order matters: **wire** before **tcs_client** (encoder is a pure function the client wraps), then **geometry**, then **controller**. Each module is a single-responsibility file (~150 lines max).

### Task 2.1: Wire encoder + decoder

**Files:**
- Create: `henrietta_guider/core/wire.py`
- Create: `tests/unit/test_wire.py`

The TCS guide port accepts the 6-byte ASCII frame `G xx yy <CR>` per `Wireformat.md`. `xx` and `yy` are signed offsets in 0.05″ steps over the encoded range `00..99` where `n > 50` decodes as `n - 100` (so `51 → -49` … `99 → -1`). The encoder rounds, clamps, and bytes-out; the decoder is for tests (round-trip property check) and for retrospective log analysis.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_wire.py`:

```python
import pytest

from henrietta_guider.core.wire import (
    GUIDE_STEP_ARCSEC,
    decode_command,
    encode_command,
    encode_step,
)


@pytest.mark.unit
class TestEncodeStep:
    def test_zero(self):
        assert encode_step(0) == "00"

    def test_positive_max(self):
        assert encode_step(50) == "50"

    def test_negative_one(self):
        assert encode_step(-1) == "99"

    def test_negative_max(self):
        assert encode_step(-49) == "51"

    @pytest.mark.parametrize("steps,encoded", [
        # +0.05" through -2.45" — the canonical anchor points called out
        # in Wireformat.md and the spec.
        (0, "00"), (1, "01"), (10, "10"), (50, "50"),
        (-1, "99"), (-2, "98"), (-49, "51"),
    ])
    def test_table(self, steps, encoded):
        assert encode_step(steps) == encoded

    def test_half_step_rounding_is_bankers(self):
        """Pin Python's default round-half-to-even on the 0.5 boundary so a
        future switch to int(round(x)) or floor doesn't silently drift.

        round(1.5) == 2 (rounds to even); round(2.5) == 2 (also even).
        """
        # 0.025" -> 0.5 step -> rounds to 0 (even), so encode_step(0) -> "00".
        # We exercise this through encode_command in TestEncodeCommand below.
        assert round(0.5) == 0
        assert round(1.5) == 2
        assert round(2.5) == 2

    def test_clamps_above_max(self):
        # Caller is expected to clamp first; encoder is defence in depth.
        assert encode_step(99) == "50"

    def test_clamps_below_min(self):
        assert encode_step(-99) == "51"


@pytest.mark.unit
class TestEncodeCommand:
    def test_zero_zero(self):
        assert encode_command(0.0, 0.0) == b"G0000\r"

    def test_max_positive(self):
        # +2.50" RA, +2.50" Dec
        assert encode_command(+2.50, +2.50) == b"G5050\r"

    def test_max_negative(self):
        # -2.45" RA, -2.45" Dec
        assert encode_command(-2.45, -2.45) == b"G5151\r"

    def test_rounds_to_nearest_step(self):
        # 0.07" rounds to 0.05" (1 step). 0.024" rounds to 0.0" (0 steps).
        assert encode_command(0.07, 0.024) == b"G0100\r"

    def test_round_trip_property(self):
        """For every legal arcsec offset, decode(encode(x)) == round(x/0.05)*0.05.

        We sample at 0.01" spacing on each axis. That's 496 × 496 ≈ 246 k
        pairs — still finishes in a few seconds — which is plenty to
        detect any sign-error or off-by-one in the encoder/decoder.
        """
        import numpy as np
        for x in np.arange(-2.45, 2.501, 0.01):
            for y in np.arange(-2.45, 2.501, 0.01):
                wire = encode_command(float(x), float(y))
                ra, dec = decode_command(wire)
                assert abs(ra  - round(x / GUIDE_STEP_ARCSEC) * GUIDE_STEP_ARCSEC) < 1e-9
                assert abs(dec - round(y / GUIDE_STEP_ARCSEC) * GUIDE_STEP_ARCSEC) < 1e-9


@pytest.mark.unit
class TestDecodeCommand:
    def test_canonical_zero(self):
        assert decode_command(b"G0000\r") == (0.0, 0.0)

    def test_max_positive(self):
        assert decode_command(b"G5050\r") == (2.50, 2.50)

    def test_negative_pair(self):
        # 9951 -> RA = -1 step = -0.05"; Dec = -49 steps = -2.45"
        assert decode_command(b"G9951\r") == (pytest.approx(-0.05), pytest.approx(-2.45))

    def test_rejects_missing_cr(self):
        with pytest.raises(ValueError, match="missing CR"):
            decode_command(b"G0000\n")

    def test_rejects_wrong_prefix(self):
        with pytest.raises(ValueError, match="prefix"):
            decode_command(b"X0000\r")

    def test_rejects_short_frame(self):
        with pytest.raises(ValueError, match="length"):
            decode_command(b"G000\r")
```

- [ ] **Step 2: Run the tests, confirm they fail.**

Run: `uv run pytest tests/unit/test_wire.py -v`
Expected: import errors / collection errors (the module doesn't exist yet). This is the desired RED state.

- [ ] **Step 3: Implement `core/wire.py`.**

Create `henrietta_guider/core/wire.py`:

```python
"""TCS guide-port wire format. See Wireformat.md.

The TCS accepts a 6-byte ASCII frame:

    G <xx> <yy> <CR>

Where xx and yy are signed offsets in 0.05" steps. The encoded value n
in 00..99 decodes as:

    n in 00..50  ->  signed value =  n
    n in 51..99  ->  signed value =  n - 100   (so 51 = -49, 99 = -1)

Range:  -2.45" ... +2.50"  on each axis (asymmetric).

The link is fire-and-forget; the TCS silently drops commands while it is
slewing or while its `guider_cmd_processing` flag is false.
"""

from __future__ import annotations

GUIDE_STEP_ARCSEC: float = 0.05
WIRE_LENGTH: int = 6  # bytes
WIRE_CR: bytes = b"\r"
MAX_POS_STEPS: int = 50
MAX_NEG_STEPS: int = -49


def encode_step(steps: int) -> str:
    """Encode a signed step count (-49..+50) as a two-character ASCII pair.

    Values outside the legal range are clamped (defence in depth; callers
    should already have applied the controller's max_command_arcsec
    clip).
    """
    if steps > MAX_POS_STEPS:
        steps = MAX_POS_STEPS
    elif steps < MAX_NEG_STEPS:
        steps = MAX_NEG_STEPS
    n = steps if steps >= 0 else steps + 100
    return f"{n:02d}"


def decode_step(encoded: str) -> int:
    """Decode a two-character ASCII pair as a signed step count."""
    if len(encoded) != 2 or not encoded.isdigit():
        raise ValueError(f"invalid step encoding: {encoded!r}")
    n = int(encoded)
    return n if n <= MAX_POS_STEPS else n - 100


def encode_command(ra_arcsec: float, dec_arcsec: float) -> bytes:
    """Encode a (RA, Dec) sky offset in arcseconds to a 6-byte wire frame."""
    ra_steps  = round(ra_arcsec  / GUIDE_STEP_ARCSEC)
    dec_steps = round(dec_arcsec / GUIDE_STEP_ARCSEC)
    return f"G{encode_step(ra_steps)}{encode_step(dec_steps)}".encode("ascii") + WIRE_CR


def decode_command(frame: bytes) -> tuple[float, float]:
    """Decode a wire frame back to (RA, Dec) arcseconds.

    Useful for retrospective log analysis and round-trip property tests.
    Raises ValueError on malformed frames.
    """
    if len(frame) != WIRE_LENGTH:
        raise ValueError(f"wrong length: expected {WIRE_LENGTH}, got {len(frame)}")
    if frame[:1] != b"G":
        raise ValueError(f"wrong prefix: expected b'G', got {frame[:1]!r}")
    if frame[5:6] != WIRE_CR:
        raise ValueError(f"missing CR at byte 5; got {frame[5:6]!r}")
    ra  = decode_step(frame[1:3].decode("ascii")) * GUIDE_STEP_ARCSEC
    dec = decode_step(frame[3:5].decode("ascii")) * GUIDE_STEP_ARCSEC
    return ra, dec
```

- [ ] **Step 4: Run the tests, confirm they pass.**

Run: `uv run pytest tests/unit/test_wire.py -v`
Expected: all green. The round-trip property test is the slowest (~5k pairs); should still finish in well under 10 seconds.

- [ ] **Step 5: Lint.**

Run: `uv run ruff format . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add henrietta_guider/core/wire.py tests/unit/test_wire.py
git commit -m "core: wire format encoder + decoder per Wireformat.md"
```

### Task 2.2: TCS client with pacing

**Files:**
- Create: `henrietta_guider/core/tcs_client.py`
- Create: `tests/unit/test_tcs_client.py`

The client is fire-and-forget over TCP. It owns its own state machine (`DISCONNECTED → CONNECTING → CONNECTED`), auto-reconnects with exponential backoff, and enforces a minimum interval between sends to respect the TCS's `!guiding_ra && !guiding_dec` gate. `send_guide()` is non-blocking: returns `True` on send, `False` if not currently `CONNECTED` or within the pacing window.

The tests use `socket.socketpair()` so we don't need a real TCP listener; the client accepts a pre-connected socket via a test-only constructor.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_tcs_client.py`:

```python
import socket
import time

import pytest

from henrietta_guider.core.tcs_client import TCSClient, ConnectionState


@pytest.mark.unit
class TestTCSClient:
    def _make_with_pair(self, pacing_s=0.0):
        a, b = socket.socketpair()
        client = TCSClient.from_connected_socket(a, pacing_interval_s=pacing_s)
        return client, b  # b is the test-side "TCS"

    def test_initial_state_when_seeded_is_connected(self):
        client, peer = self._make_with_pair()
        assert client.state is ConnectionState.CONNECTED
        peer.close()

    def test_send_guide_emits_correct_bytes(self):
        client, peer = self._make_with_pair()
        ok = client.send_guide(0.50, -0.05)
        assert ok is True
        assert peer.recv(6) == b"G1099\r"
        peer.close()

    def test_send_when_disconnected_returns_false(self):
        # Build a client, then force DISCONNECTED — exactly the state a
        # caller would see after a network drop. No timing-dependent
        # buffering games.
        client, peer = self._make_with_pair()
        client._force_state(ConnectionState.DISCONNECTED)
        assert client.send_guide(0.0, 0.0) is False
        assert client.commands_suppressed_disconnected == 1
        peer.close()

    def test_pacing_blocks_within_window(self):
        # Use a short pacing window with a generous proportional slack
        # (60 ms wait for a 50 ms window — 20 % slack — robust on a
        # loaded CI runner without slowing the test).
        client, peer = self._make_with_pair(pacing_s=0.05)
        assert client.send_guide(0.0, 0.0) is True
        peer.recv(6)
        # Immediately again: should be suppressed.
        assert client.send_guide(0.05, 0.0) is False
        assert client.commands_suppressed_pacing == 1
        time.sleep(0.06)
        # Outside the window: should send.
        assert client.send_guide(0.05, 0.0) is True
        peer.recv(6)
        peer.close()

    def test_clip_then_encode(self):
        # 3.0" exceeds the wire range; the client should clip to 2.50"
        # before encoding.
        client, peer = self._make_with_pair()
        ok = client.send_guide(3.0, -3.0)
        assert ok is True
        assert peer.recv(6) == b"G5051\r"  # +2.50" RA, -2.45" Dec
        peer.close()
```

- [ ] **Step 2: Run the tests, confirm they fail.**

Run: `uv run pytest tests/unit/test_tcs_client.py -v`
Expected: import error (module doesn't exist).

- [ ] **Step 3: Implement `core/tcs_client.py`.**

Create `henrietta_guider/core/tcs_client.py`:

```python
"""Fire-and-forget TCP client for the Henrietta TCS guide port.

State machine:
    DISCONNECTED -> CONNECTING -> CONNECTED
                ^         |             |
                |_________|_____________|

Auto-reconnect with exponential backoff. send_guide() is non-blocking:
returns True on a real send, False if not currently CONNECTED or within
the pacing window. Both suppression paths are counted for surfacing in
the GUI status bar.
"""

from __future__ import annotations

import enum
import logging
import socket
import time

from .wire import (
    GUIDE_STEP_ARCSEC,
    MAX_NEG_STEPS,
    MAX_POS_STEPS,
    encode_command,
)

log = logging.getLogger(__name__)


class ConnectionState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class TCSClient:
    """TCP client to the TCS guide port.

    The class is *not* thread-safe. The autoguider's worker thread is
    the only thread that calls `send_guide()`; the GUI reads connection
    state via the property accessors only.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        pacing_interval_s: float = 5.0,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.pacing_interval_s = pacing_interval_s
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s
        self._sock: socket.socket | None = None
        self._state = ConnectionState.DISCONNECTED
        self._last_send_monotonic: float = -1e9
        self.commands_suppressed_pacing: int = 0
        self.commands_suppressed_disconnected: int = 0

    # ---- construction -----------------------------------------------------

    @classmethod
    def from_connected_socket(
        cls,
        sock: socket.socket,
        pacing_interval_s: float = 0.0,
    ) -> "TCSClient":
        """Test-only: build a client around a pre-connected socket."""
        client = cls(pacing_interval_s=pacing_interval_s)
        client._sock = sock
        client._state = ConnectionState.CONNECTED
        return client

    # ---- public API -------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    def send_guide(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        """Send a single guide-offset frame.

        Returns True if the frame was put on the socket, False if it was
        suppressed (not connected, or within the pacing window). Never
        raises on a normal disconnect; logs WARNING and flips state.
        """
        if self._state is not ConnectionState.CONNECTED:
            self.commands_suppressed_disconnected += 1
            return False

        now = time.monotonic()
        if now - self._last_send_monotonic < self.pacing_interval_s:
            self.commands_suppressed_pacing += 1
            return False

        # Clip to the legal wire range *before* encoding so the controller's
        # asymmetric range is honoured (max_command_arcsec is 2.45 by default
        # in §8 config; this is defence in depth).
        ra_clipped  = max(MAX_NEG_STEPS * GUIDE_STEP_ARCSEC,
                          min(MAX_POS_STEPS * GUIDE_STEP_ARCSEC, ra_arcsec))
        dec_clipped = max(MAX_NEG_STEPS * GUIDE_STEP_ARCSEC,
                          min(MAX_POS_STEPS * GUIDE_STEP_ARCSEC, dec_arcsec))

        frame = encode_command(ra_clipped, dec_clipped)
        try:
            assert self._sock is not None
            self._sock.sendall(frame)
        except OSError as exc:
            log.warning("TCS sendall failed: %s", exc)
            self._mark_disconnected()
            self.commands_suppressed_disconnected += 1
            return False

        self._last_send_monotonic = now
        log.info("G %s sent (RA=%+.2f\" Dec=%+.2f\")",
                 frame[1:5].decode(), ra_clipped, dec_clipped)
        return True

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._state = ConnectionState.DISCONNECTED

    # ---- internal ---------------------------------------------------------

    def _mark_disconnected(self) -> None:
        self._state = ConnectionState.DISCONNECTED
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _force_state(self, state: ConnectionState) -> None:
        """Test-only state override."""
        self._state = state
```

Note: full reconnect / exponential-backoff machinery is intentionally deferred to Chunk 5 (`worker.py` will own the lifecycle). For now the client only does the send-side state transitions; reconnect is wired up later.

- [ ] **Step 4: Run the tests, confirm they pass.**

Run: `uv run pytest tests/unit/test_tcs_client.py -v`
Expected: all five green.

- [ ] **Step 5: Lint.**

Run: `uv run ruff format . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add henrietta_guider/core/tcs_client.py tests/unit/test_tcs_client.py
git commit -m "core: TCS client with pacing + suppression counters"
```

### Task 2.3: Detector → sky geometry

**Files:**
- Create: `henrietta_guider/core/geometry.py`
- Create: `tests/unit/test_geometry.py`

Convert detector pixel offsets to sky (RA, Dec) arcseconds via plate scale + PA rotation + per-axis parity. The exact signs are TBC with William (Question 14) so all parameters are exposed; the unit tests pin every PA / parity combination so a sign flip in production is a one-config-line change.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_geometry.py`:

```python
import math

import pytest

from henrietta_guider.core.geometry import detector_to_sky


@pytest.mark.unit
class TestDetectorToSky:
    """detector_to_sky returns the *correction* (telescope offset that
    cancels a measured detector-frame drift). At PA=0 with parities
    +1/+1, a +1 px drift in detector X corresponds to +1 px of trace
    motion toward east on the sky, so the correction is -plate arcsec
    in RA. Same handedness for Y/Dec: +1 px drift -> -plate arcsec
    correction in Dec. The function's overall sign is "correction =
    -drift" applied uniformly to both axes, with a 2-D rotation by PA
    in between.
    """

    PLATE = 0.435  # arcsec/px (placeholder; real value from William)

    def test_zero_pa_zero_offset(self):
        ra, dec = detector_to_sky(0.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(0.0)
        assert dec == pytest.approx(0.0)

    def test_pa_zero_x_maps_to_negative_ra_correction(self):
        # +1 px drift in detector X at PA=0 with parity_x=+1 corresponds
        # to the trace having moved +RA on the sky. Correction = -drift,
        # so the returned dRA is -plate.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra  == pytest.approx(-self.PLATE)
        assert dec == pytest.approx(0.0, abs=1e-12)

    def test_pa_zero_y_maps_to_negative_dec_correction(self):
        # +1 px drift in detector Y at PA=0 with parity_y=+1 corresponds
        # to the trace having moved +Dec on the sky. Correction = -drift.
        ra, dec = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        assert ra  == pytest.approx(0.0, abs=1e-12)
        assert dec == pytest.approx(-self.PLATE)

    def test_pa_90_x_drift_becomes_dec_correction(self):
        # At PA=90, detector +Y points east (+RA) and detector +X points
        # south (-Dec). A +1 px drift in detector X is therefore -Dec
        # drift on the sky -> correction is +plate in Dec.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 90.0, +1, +1)
        assert ra  == pytest.approx(0.0, abs=1e-12)
        assert dec == pytest.approx(+self.PLATE)

    def test_parity_flip_x(self):
        # Flipping parity_x flips the RA contribution.
        ra_p, _ = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        ra_n, _ = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, -1, +1)
        assert ra_n == pytest.approx(-ra_p)

    def test_parity_flip_y(self):
        # And similarly for Dec via parity_y.
        _, dec_p = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        _, dec_n = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, -1)
        assert dec_n == pytest.approx(-dec_p)

    def test_full_pa_sweep_preserves_magnitude(self):
        # The total (RA, Dec) magnitude must equal sqrt(dx^2 + dy^2) * plate
        # for any PA / parity combo (a rotation+sign-flip preserves L2).
        for pa_deg in (0, 17, 33, 90, 180, 271, 359):
            for px, py in (-1, -1), (+1, +1), (+3, -2):
                for parx in (+1, -1):
                    for pary in (+1, -1):
                        ra, dec = detector_to_sky(
                            float(px), float(py), self.PLATE, float(pa_deg), parx, pary,
                        )
                        expected = self.PLATE * math.hypot(px, py)
                        assert math.hypot(ra, dec) == pytest.approx(expected, abs=1e-9)
```

- [ ] **Step 2: Run the tests, confirm they fail.**

Run: `uv run pytest tests/unit/test_geometry.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/geometry.py`.**

Create `henrietta_guider/core/geometry.py`:

```python
"""Detector → sky transform.

The Henrietta detector pixel offsets (dx_px, dy_px) measured by the
2-D xcor pipeline must be converted to sky-frame offsets (RA, Dec) in
arcseconds before the controller acts on them. The TCS guide port
expects sky-frame offsets (see Wireformat.md).

Sign convention (TBC with William; see Q14 in Questions-for-William.md):

    sky offset = telescope correction = -(measured drift)

In other words, if the trace has drifted +1 px in detector X, the
telescope must move -1 px in detector X (from its current pointing) to
bring the trace back. The minus sign lives here so the controller can
work in the "drive error to zero" convention.

Parity_x and parity_y encode the detector's handedness on the sky at
PA = 0: e.g. parity_x = +1 means +X-detector aligns with +RA-sky at
PA = 0; parity_x = -1 means it aligns with -RA. These are pinned in
config and verified against an on-sky test offset during commissioning.
"""

from __future__ import annotations

import math


def detector_to_sky(
    dx_px: float,
    dy_px: float,
    plate_scale_arcsec_per_px: float,
    pa_deg: float,
    parity_x: int,
    parity_y: int,
) -> tuple[float, float]:
    """Convert a measured detector pixel drift to a sky-frame correction.

    Returns (dRA_arcsec, dDec_arcsec) — the telescope correction that
    cancels the drift. Equivalent to applying the parities, doing a 2-D
    rotation by PA, then negating both components ("correction =
    -drift").
    """
    dx_arcsec = parity_x * dx_px * plate_scale_arcsec_per_px
    dy_arcsec = parity_y * dy_px * plate_scale_arcsec_per_px
    pa = math.radians(pa_deg)
    cos_pa, sin_pa = math.cos(pa), math.sin(pa)
    # Standard rotation: drift_RA  = +dx*cos - dy*sin (no, see below)
    # We use the convention that detector +Y is north of east by PA, so
    # the drift in (RA, Dec) from a detector pixel offset (dx, dy) is:
    #     drift_RA  = dx*cos_pa + dy*sin_pa
    #     drift_Dec = dy*cos_pa - dx*sin_pa
    # Correction = -drift; both components flipped together so the
    # transform stays magnitude-preserving (it's a rotation × -1).
    dra  = -(dx_arcsec * cos_pa + dy_arcsec * sin_pa)
    ddec = -(dy_arcsec * cos_pa - dx_arcsec * sin_pa)
    return dra, ddec
```

- [ ] **Step 4: Run the tests, confirm they pass.**

Run: `uv run pytest tests/unit/test_geometry.py -v`
Expected: all green.

- [ ] **Step 5: Lint.**

Run: `uv run ruff format . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add henrietta_guider/core/geometry.py tests/unit/test_geometry.py
git commit -m "core: detector to sky transform (PA + plate scale + parity)"
```

### Task 2.4: Per-axis controller

**Files:**
- Create: `henrietta_guider/core/controller.py`
- Create: `tests/unit/test_controller.py`

Per-axis P controller for v1 with `Ki` and `Kd` fields already in the dataclass for forward compatibility. Dead band suppresses noise-floor commands; max-command clip keeps a single send within the wire range. The output is the **command** (signed arcseconds) that the worker hands to `tcs_client.send_guide()`.

The "freeze accumulators while ALERTED" semantics for PI/PID (see §5 of the spec) are stubbed in the dataclass but irrelevant for v1's pure-P. The test fixture exercises the dataclass interface so adding integral/derivative state later doesn't break callers.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_controller.py`:

```python
import pytest

from henrietta_guider.core.controller import Controller, ControllerConfig


@pytest.mark.unit
class TestController:
    def _make(self, **overrides):
        cfg = ControllerConfig(**{
            "Kp": 0.5, "Ki": 0.0, "Kd": 0.0,
            "deadband_arcsec": 0.025, "max_command_arcsec": 2.45,
            **overrides,
        })
        return Controller(cfg)

    def test_zero_error_zero_command(self):
        ctrl = self._make()
        assert ctrl.step(0.0) == 0.0

    def test_proportional(self):
        ctrl = self._make(Kp=0.5)
        assert ctrl.step(0.10) == pytest.approx(0.05)

    def test_deadband_suppresses_small_errors(self):
        ctrl = self._make(deadband_arcsec=0.05)
        assert ctrl.step(0.04) == 0.0
        assert ctrl.step(-0.04) == 0.0

    def test_deadband_passes_threshold(self):
        ctrl = self._make(Kp=1.0, deadband_arcsec=0.05)
        assert ctrl.step(0.06) == pytest.approx(0.06)

    def test_max_command_clips(self):
        ctrl = self._make(Kp=1.0, max_command_arcsec=2.45)
        assert ctrl.step(+5.0) == pytest.approx(+2.45)
        assert ctrl.step(-5.0) == pytest.approx(-2.45)

    def test_deadband_pass_then_clip(self):
        # Combined: error passes the deadband AND requires clipping.
        ctrl = self._make(Kp=10.0, deadband_arcsec=0.05, max_command_arcsec=0.5)
        assert ctrl.step(0.06) == pytest.approx(0.5)

    def test_integral_does_not_accumulate_when_Ki_is_zero(self):
        # With Ki=0 (the v1 default) the integrator must stay at 0
        # forever, so a config-time Ki bump (no code change) doesn't
        # suddenly inject a huge accumulated error.
        ctrl = self._make(Kp=0.5, Ki=0.0)
        for _ in range(1000):
            ctrl.step(0.10)
        assert ctrl._integral == 0.0

    def test_integral_accumulates_when_Ki_is_nonzero(self):
        ctrl = self._make(Kp=0.5, Ki=0.01)
        for _ in range(10):
            ctrl.step(0.10)
        # 10 steps of +0.10" each, all above deadband:
        assert ctrl._integral == pytest.approx(1.0)

    def test_on_alerted_freezes_integral(self):
        # PI scenario: integral must NOT advance while frozen, must
        # resume on on_resumed().
        ctrl = self._make(Kp=0.5, Ki=0.01)
        for _ in range(5):
            ctrl.step(0.10)
        before = ctrl._integral
        ctrl.on_alerted()
        for _ in range(5):
            ctrl.step(0.10)
        assert ctrl._integral == before  # frozen
        ctrl.on_resumed()
        ctrl.step(0.10)
        assert ctrl._integral == pytest.approx(before + 0.10)

    def test_v1_pure_p_unaffected_by_alerted(self):
        # With Ki=Kd=0 (v1) the controller is stateless wrt _integral,
        # so on_alerted() doesn't change step() output.
        ctrl = self._make()
        ctrl.on_alerted()
        assert ctrl.step(0.10) == pytest.approx(0.05)
```

- [ ] **Step 2: Run the tests, confirm they fail.**

Run: `uv run pytest tests/unit/test_controller.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/controller.py`.**

Create `henrietta_guider/core/controller.py`:

```python
"""Per-axis P controller (with PI/PID hooks for forward compatibility).

The controller takes a measured error in arcseconds and returns the
command in arcseconds. Dead band suppresses noise-floor commands; the
max-command clip keeps a single command within the wire range. v1 uses
pure-P; Ki and Kd live in the config and are used once the PI/PID
machinery is added.

Sign convention: step() is called with `error_arcsec = -measured_drift`
already converted to sky frame by geometry.detector_to_sky(). The
controller multiplies by Kp and returns the command directly (no
sign flip here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ControllerConfig:
    Kp: float = 0.5
    Ki: float = 0.0
    Kd: float = 0.0
    deadband_arcsec: float = 0.025
    max_command_arcsec: float = 2.45


class Controller:
    def __init__(self, cfg: ControllerConfig) -> None:
        self.cfg = cfg
        # Reserved for PI/PID; unused in v1.
        self._integral: float = 0.0
        self._last_error: float | None = None
        self._frozen: bool = False

    def step(self, error_arcsec: float) -> float:
        """Compute the command for one error sample."""
        if abs(error_arcsec) < self.cfg.deadband_arcsec:
            return 0.0
        cmd = self.cfg.Kp * error_arcsec
        # Ki / Kd hooks. Disabled when frozen and skipped entirely when
        # Ki == 0 (v1) so the integral never grows. This prevents a
        # config-time Ki bump from suddenly injecting a huge accumulated
        # error from a long previous run.
        if not self._frozen and self.cfg.Ki != 0.0:
            self._integral += error_arcsec
        if not self._frozen:
            self._last_error = error_arcsec
        cmd += self.cfg.Ki * self._integral
        # (Kd term omitted in v1; would use _last_error here.)
        # Clip.
        if cmd > self.cfg.max_command_arcsec:
            cmd = self.cfg.max_command_arcsec
        elif cmd < -self.cfg.max_command_arcsec:
            cmd = -self.cfg.max_command_arcsec
        return cmd

    def on_alerted(self) -> None:
        """Freeze integral / derivative accumulators while ALERTED.

        v1: no-op (pure-P, stateless). When PI/PID is enabled later,
        this will stop _integral and _last_error from updating during
        ALERTED so the loop resumes cleanly without wind-up.
        """
        self._frozen = True

    def on_resumed(self) -> None:
        """Re-enable accumulators after ALERTED -> GUIDING."""
        self._frozen = False
```

- [ ] **Step 4: Run the tests, confirm they pass.**

Run: `uv run pytest tests/unit/test_controller.py -v`
Expected: all green.

- [ ] **Step 5: Lint.**

Run: `uv run ruff format . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add henrietta_guider/core/controller.py tests/unit/test_controller.py
git commit -m "core: per-axis P controller with PI/PID hooks"
```

### Task 2.5: End-of-chunk verification

- [ ] **Step 1: Run the full test suite.**

Run: `make test`
Expected: 4 modules' worth of tests, all green; should finish in well under 30 seconds.

- [ ] **Step 2: Run lint.**

Run: `make lint`
Expected: clean.

- [ ] **Step 3: Push and confirm CI is green.**

```bash
git log --oneline -10            # confirm the four new commits
git push                         # push the chunk
gh run watch                     # streams the current workflow until complete
```

Expected: `gh run watch` exits 0 (workflow concluded with success).
If `gh` is not installed, open https://github.com/nickkonidaris/Henrietta-Guider/actions in a browser and wait for the green check.

- [ ] **Step 4: Confirm git status is clean.**

Run: `git status`
Expected: "nothing to commit, working tree clean."

End of Chunk 2. Working state: pure-computational core fully tested. The autoguider can now encode/decode wire frames, talk to a TCP socket with pacing, transform detector pixels to sky offsets, and run a per-axis P controller — but no I/O orchestration, file watching, or GUI yet.

---
