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
Expected: all green. The round-trip property test is the slowest (~246 k pairs at 0.01" spacing); should finish in a few seconds.

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

    def test_pa_45_diagonal(self):
        # PA=45 with dx=1, dy=0: drift = (cos45, -sin45) * PLATE,
        # correction = -drift = (-cos45, +sin45) * PLATE.
        # Pin the rotation direction explicitly so a future formula
        # tweak that swaps cos/sin gets caught.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 45.0, +1, +1)
        s = math.sqrt(0.5)
        assert ra  == pytest.approx(-self.PLATE * s)
        assert dec == pytest.approx(+self.PLATE * s)

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
    # Convention: detector +Y is east of north by PA, so the drift in
    # (RA, Dec) from a detector pixel offset (dx, dy) is:
    #     drift_RA  = dx*cos_pa + dy*sin_pa
    #     drift_Dec = dy*cos_pa - dx*sin_pa
    # Correction = -drift; both components flipped together so the
    # transform stays magnitude-preserving (rotation × -1).
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

## Chunk 3: Reduction primitives

**Goal:** Land the seven modules that make up the core reduction pipeline:
shared types, the config dataclass tree, BPM loader, SUTR framebuffer +
K-window difference, per-row sky subtraction, 2-D xcor + sub-pixel peak,
and template build. Each module is independently testable; together
they implement the algorithm in `ALGORITHM.md` end-to-end.

Order matters because `template.py` depends on `bpm.py` + `sky.py`, and
`reducer.py` (Chunk 4) depends on everything in this chunk. Keep this
sequence: types → config → bpm → framebuffer → sky → xcor → template.

### Task 3.1: Shared types

**Files:**
- Create: `henrietta_guider/core/types.py`
- Create: `tests/unit/test_types.py`

Shared dataclasses used throughout `core/`. Frozen where they represent
immutable measurements; mutable where they're long-lived state.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_types.py`:

```python
import pytest

from henrietta_guider.core.types import GuidingState, Stamp


@pytest.mark.unit
class TestStamp:
    def test_constructor_and_attributes(self):
        s = Stamp(x_center=512, x_halfwidth=25, y_lo=600, y_hi=1980)
        assert s.x_center == 512
        assert s.x_halfwidth == 25
        assert s.y_lo == 600
        assert s.y_hi == 1980

    def test_xmin_xmax_helpers(self):
        # ALGORITHM.md uses [x_center - halfw : x_center + halfw + 1]
        # -> width = 2*halfw + 1, inclusive of x_center+halfw.
        s = Stamp(x_center=100, x_halfwidth=10, y_lo=0, y_hi=100)
        assert s.x_min == 90
        assert s.x_max == 111  # half-open: [90, 111) -> 21 columns

    def test_shape(self):
        s = Stamp(x_center=100, x_halfwidth=10, y_lo=200, y_hi=300)
        assert s.shape == (100, 21)  # (ny, 2*halfw + 1)

    def test_frozen(self):
        s = Stamp(x_center=0, x_halfwidth=1, y_lo=0, y_hi=1)
        with pytest.raises(Exception):
            s.x_center = 99  # frozen dataclass


@pytest.mark.unit
class TestGuidingState:
    def test_canonical_states_exist(self):
        # Pin the names that the GUI / state machine refer to.
        assert GuidingState.IDLE.name == "IDLE"
        assert GuidingState.REFERENCE_PENDING.name == "REFERENCE_PENDING"
        assert GuidingState.REFERENCE_SET.name == "REFERENCE_SET"
        assert GuidingState.GUIDING.name == "GUIDING"
        assert GuidingState.ALERTED.name == "ALERTED"
        assert GuidingState.PAUSED.name == "PAUSED"
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_types.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/types.py`.**

```python
"""Shared dataclasses and enums used throughout core/.

Frozen dataclasses where the value is immutable per-frame data; plain
dataclasses where the object owns long-lived mutable state (e.g.
running-stat buffers).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class GuidingState(enum.Enum):
    IDLE = "idle"
    REFERENCE_PENDING = "reference_pending"
    REFERENCE_SET = "reference_set"
    GUIDING = "guiding"
    ALERTED = "alerted"
    PAUSED = "paused"


@dataclass(frozen=True)
class Stamp:
    """Rectangular window on the science detector.

    Coordinates are 0-based detector pixels. x_min and x_max are
    half-open: pixels in [x_min, x_max). Same for y_lo / y_hi.
    """

    x_center: int
    x_halfwidth: int
    y_lo: int
    y_hi: int

    @property
    def x_min(self) -> int:
        return self.x_center - self.x_halfwidth

    @property
    def x_max(self) -> int:
        # ALGORITHM.md uses [x_center - halfw : x_center + halfw + 1] —
        # the +1 gives a 2*halfw+1-wide window inclusive of x_center+halfw.
        return self.x_center + self.x_halfwidth + 1

    @property
    def shape(self) -> tuple[int, int]:
        """Returns (ny, nx) where nx = 2*x_halfwidth + 1."""
        return (self.y_hi - self.y_lo, self.x_max - self.x_min)
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_types.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/types.py tests/unit/test_types.py
git commit -m "core: shared types (Stamp, GuidingState)"
```

### Task 3.2: Config dataclass tree

**Files:**
- Create: `henrietta_guider/core/config.py`
- Create: `tests/unit/test_config.py`

A nested dataclass tree mirroring the §8 config sections in the spec.
Loaded from / saved to TOML via stdlib `tomllib` and `tomli_w`. (We use
the third-party writer because stdlib's tomllib is read-only — but
`tomli_w` is a tiny dependency and avoids hand-rolling a writer.)

Add `tomli_w` to `pyproject.toml` deps before starting.

- [ ] **Step 1: Add `tomli_w` to `pyproject.toml`.**

In `pyproject.toml`, append to the `dependencies` array:

```toml
    "tomli-w>=1.0",
```

Run: `uv sync` and confirm it succeeds.

- [ ] **Step 2: Write the failing tests.**

Create `tests/unit/test_config.py`:

```python
from pathlib import Path

import pytest

from henrietta_guider.core.config import Config, load_config, save_config


@pytest.mark.unit
class TestConfigDefaults:
    def test_loop_defaults(self):
        c = Config()
        assert c.loop.Kp_ra == pytest.approx(0.5)
        assert c.loop.Kp_dec == pytest.approx(0.5)
        assert c.loop.deadband_arcsec == pytest.approx(0.025)
        assert c.loop.max_command_arcsec == pytest.approx(2.45)
        assert c.loop.pacing_interval_s == pytest.approx(5.0)

    def test_quality_defaults(self):
        c = Config()
        assert c.quality.out_of_family_window == 20
        assert c.quality.out_of_family_warmup_n == 10
        assert c.quality.out_of_family_sigma == pytest.approx(5.0)
        assert c.quality.auto_resume_in_family == 3
        assert c.quality.stale_frame_timeout_s == pytest.approx(30.0)
        assert c.quality.target_switch_arcsec_threshold == pytest.approx(20.0)

    def test_reduction_defaults(self):
        c = Config()
        assert c.reduction.K == 1
        assert c.reduction.stride == 1
        assert c.reduction.stamp_x_halfwidth_px == 25
        assert c.reduction.stamp_y_lo == 600
        assert c.reduction.stamp_y_hi == 1980
        assert c.reduction.xcor_search_radius_px == 12
        assert c.reduction.auto_refresh_template is False

    def test_detector_defaults(self):
        c = Config()
        assert c.detector.gain_e_per_dn == pytest.approx(4.0)
        assert c.detector.read_noise_e == pytest.approx(12.0)
        assert c.detector.saturation_dn == 40000
        assert c.detector.y_middle_row == 1024


@pytest.mark.unit
class TestConfigRoundTrip:
    def test_save_then_load_returns_equal_config(self, tmp_path: Path):
        c = Config()
        c.loop.Kp_ra = 0.42                       # mutate one value
        c.tcs.host = "tcs.lco.test"               # ... and another
        out = tmp_path / "config.toml"
        save_config(c, out)
        c2 = load_config(out)
        assert c2 == c

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        c = load_config(tmp_path / "does-not-exist.toml")
        assert c == Config()  # defaults

    def test_load_partial_toml_fills_in_defaults(self, tmp_path: Path):
        # Only [loop] section in the file; everything else should
        # default.
        f = tmp_path / "partial.toml"
        f.write_text("[loop]\nKp_ra = 0.99\n")
        c = load_config(f)
        assert c.loop.Kp_ra == pytest.approx(0.99)
        assert c.loop.Kp_dec == pytest.approx(0.5)  # default
        assert c.quality.out_of_family_sigma == pytest.approx(5.0)
```

- [ ] **Step 3: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: import error.

- [ ] **Step 4: Implement `core/config.py`.**

```python
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
    xcor_search_radius_px: int = 12
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
    host: str = "tcs.lco"
    port: int = 5400
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
    loop:      LoopConfig      = field(default_factory=LoopConfig)
    quality:   QualityConfig   = field(default_factory=QualityConfig)
    reduction: ReductionConfig = field(default_factory=ReductionConfig)
    files:     FilesConfig     = field(default_factory=FilesConfig)
    tcs:       TCSConfig       = field(default_factory=TCSConfig)
    detector:  DetectorConfig  = field(default_factory=DetectorConfig)
    display:   DisplayConfig   = field(default_factory=DisplayConfig)


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
```

- [ ] **Step 5: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: all green.

- [ ] **Step 6: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/config.py tests/unit/test_config.py pyproject.toml uv.lock
git commit -m "core: config dataclass tree with TOML round-trip"
```

### Task 3.3: BPM loader

**Files:**
- Create: `henrietta_guider/core/bpm.py`
- Create: `tests/unit/test_bpm.py`

Reads HDU 0 of a multi-extension FITS BPM (the real `bpm_25apr2026.fits`
has 7 HDUs; we only need the master mask). Convention: 1 = good, 0 =
bad. Returns a boolean numpy array where `True` = good. The HDUs 1–6
(`COVERAGE`, `DEAD`, `HOT`, `NOISY`, `NOISY_DARK`, `REF_PIX`) are
ignored — the master HDU 0 already folds in REF_PIX.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_bpm.py`:

```python
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from henrietta_guider.core.bpm import load_bpm


def _write_synthetic_bpm(path: Path, ny: int = 32, nx: int = 32,
                         n_bad: int = 3) -> np.ndarray:
    """Write a synthetic 7-HDU BPM (HDU 0 master, others diagnostic-ish)."""
    master = np.ones((ny, nx), dtype=np.uint8)
    rng = np.random.default_rng(0)
    bad_idx = rng.choice(ny * nx, size=n_bad, replace=False)
    master.flat[bad_idx] = 0
    extensions = [
        ("COVERAGE",   np.ones((ny, nx), dtype=np.uint8)),
        ("DEAD",       np.zeros((ny, nx), dtype=np.uint8)),
        ("HOT",        np.zeros((ny, nx), dtype=np.uint8)),
        ("NOISY",      np.zeros((ny, nx), dtype=np.uint8)),
        ("NOISY_DARK", np.zeros((ny, nx), dtype=np.uint8)),
        ("REF_PIX",    np.zeros((ny, nx), dtype=np.uint8)),
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
        dead   = np.ones((4, 4), dtype=np.uint8)  # "every pixel dead"
        hdul = fits.HDUList([
            fits.PrimaryHDU(master),
            fits.ImageHDU(np.zeros((4, 4), dtype=np.uint8)),  # COVERAGE
            fits.ImageHDU(dead),                              # DEAD
        ])
        hdul[1].header["EXTNAME"] = "COVERAGE"
        hdul[2].header["EXTNAME"] = "DEAD"
        hdul.writeto(bpm_path, overwrite=True)
        good = load_bpm(bpm_path)
        # If load_bpm(only HDU 0) is correct: every pixel good.
        assert good.all()

    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_bpm(tmp_path / "no-such-file.fits")
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_bpm.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/bpm.py`.**

```python
"""Bad-pixel mask loader.

The Henrietta BPM (bpm_25apr2026.fits) is a 7-HDU MEF:

    HDU 0 (primary)  master good-pixel map  (1 = good, 0 = bad)
    HDU 1 COVERAGE   1 = illuminated science region
    HDU 2 DEAD       1 = dead pixel
    HDU 3 HOT        1 = hot pixel
    HDU 4 NOISY      1 = noisy in light
    HDU 5 NOISY_DARK 1 = noisy in dark
    HDU 6 REF_PIX    1 = H2RG reference pixel

The autoguider only reads HDU 0. The other HDUs are diagnostic
categories that are already folded into the master.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits


def load_bpm(path: str | Path) -> np.ndarray:
    """Load the master good-pixel mask as a boolean numpy array.

    Returns an array with the same shape as the science detector,
    where True == good (master HDU 0 == 1) and False == bad (== 0).
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(p)
    with fits.open(p) as hdul:
        master = hdul[0].data
    return master.astype(bool)
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_bpm.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/bpm.py tests/unit/test_bpm.py
git commit -m "core: BPM loader (master HDU 0; 1=good)"
```

### Task 3.4: SUTR framebuffer + K-window difference

**Files:**
- Create: `henrietta_guider/core/framebuffer.py`
- Create: `tests/unit/test_framebuffer.py`

Maintains a rolling buffer of SUTR reads for the **current** frame
number. On a new frame_number boundary the buffer clears (a new detector
reset). Emits a guide image when enough reads have accumulated and the
stride condition is met:

    image = mean(reads[i+1 .. i+K]) − mean(reads[i+1−K .. i])

With `K=1, stride=1` (defaults) you get one guide image per SUTR after
the first.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_framebuffer.py`:

```python
import numpy as np
import pytest

from henrietta_guider.core.framebuffer import FrameBuffer


def _read(value: float, shape: tuple[int, int] = (4, 4)) -> np.ndarray:
    return np.full(shape, value, dtype=np.float32)


@pytest.mark.unit
class TestFrameBufferKEqualsOne:
    def test_first_read_does_not_emit(self):
        fb = FrameBuffer(K=1, stride=1)
        out = fb.add(frame_number=42, sutr_number=1, read=_read(100.0))
        assert out is None

    def test_second_read_emits_difference(self):
        fb = FrameBuffer(K=1, stride=1)
        fb.add(42, 1, _read(100.0))
        out = fb.add(42, 2, _read(150.0))
        assert out is not None
        np.testing.assert_array_almost_equal(out, _read(50.0))

    def test_frame_boundary_clears_buffer(self):
        fb = FrameBuffer(K=1, stride=1)
        fb.add(42, 1, _read(100.0))
        fb.add(42, 2, _read(150.0))
        # New frame: buffer must clear; this read is _001 of frame 43,
        # so no guide image yet.
        out = fb.add(43, 1, _read(200.0))
        assert out is None
        # Next read on frame 43 differences against frame 43's _001,
        # NOT frame 42's last read.
        out = fb.add(43, 2, _read(220.0))
        np.testing.assert_array_almost_equal(out, _read(20.0))


@pytest.mark.unit
class TestFrameBufferKAndStride:
    def test_K2_emits_after_4_reads(self):
        # K=2, stride=1: needs 2K=4 reads in the buffer; window-difference is
        # mean(reads[3..4]) - mean(reads[1..2]).
        fb = FrameBuffer(K=2, stride=1)
        for sutr, val in enumerate([10, 20, 30, 40], start=1):
            out = fb.add(99, sutr, _read(float(val)))
            if sutr < 4:
                assert out is None
        # mean(30, 40) - mean(10, 20) = 35 - 15 = 20
        np.testing.assert_array_almost_equal(out, _read(20.0))

    def test_K2_stride_2_skips_every_other(self):
        # K=2, stride=2: emits every 2 reads after warm-up, not every 1.
        fb = FrameBuffer(K=2, stride=2)
        emits = []
        for sutr, val in enumerate([10, 20, 30, 40, 50, 60], start=1):
            out = fb.add(99, sutr, _read(float(val)))
            if out is not None:
                emits.append(out.mean())
        # After read 4: mean(30,40)-mean(10,20)=20.
        # After read 5: stride=2 not yet -> skip.
        # After read 6: mean(50,60)-mean(30,40)=20.
        assert emits == pytest.approx([20.0, 20.0])

    def test_buffer_size_is_2K(self):
        fb = FrameBuffer(K=3, stride=1)
        for sutr in range(1, 8):
            fb.add(1, sutr, _read(float(sutr)))
        # Buffer must hold the most recent 2*K=6 reads.
        assert len(fb._buf) == 6  # implementation detail; test pins it
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_framebuffer.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/framebuffer.py`.**

```python
"""Rolling buffer of SUTR reads + K-window difference.

For frame number N, the autoguider receives reads N_001, N_002, ...
For each new read, this module either:
  - clears the buffer (new frame_number = detector reset);
  - or appends to the buffer (within the same frame);
and emits a guide image once the buffer holds 2*K reads, advancing by
`stride` reads between emissions.

guide_image = mean(reads[K+1..2K]) − mean(reads[1..K])

where indexing here is "newest at the right". K=1 / stride=1 is the
ALGORITHM.md default: image = read[i] − read[i-1] every read.
"""

from __future__ import annotations

import collections

import numpy as np


class FrameBuffer:
    def __init__(self, K: int = 1, stride: int = 1) -> None:
        if K < 1:
            raise ValueError(f"K must be >= 1, got {K}")
        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride}")
        self.K = K
        self.stride = stride
        self._buf: collections.deque[np.ndarray] = collections.deque(maxlen=2 * K)
        self._current_frame: int | None = None
        self._reads_since_emit: int = 0

    def add(
        self,
        frame_number: int,
        sutr_number: int,
        read: np.ndarray,
    ) -> np.ndarray | None:
        """Add one SUTR read; return a guide image if one is emitted, else None.

        Stride semantics: once the buffer holds 2*K reads, an emit is
        produced every `stride` reads (not every `stride` newest reads).
        With K=1 / stride=1 — the default — every SUTR after the first
        emits a difference. With K=2 / stride=2, emits happen on reads
        4, 6, 8, … (4 = first warm-up, then every-other).
        """
        if frame_number != self._current_frame:
            # New integration -> reset.
            self._buf.clear()
            self._current_frame = frame_number
            self._reads_since_emit = 0

        self._buf.append(read)
        self._reads_since_emit += 1

        if len(self._buf) < 2 * self.K:
            return None
        if self._reads_since_emit < self.stride:
            return None

        self._reads_since_emit = 0
        # Buffer is full (2K reads, oldest first).
        older = np.mean(np.stack(list(self._buf)[: self.K]), axis=0)
        newer = np.mean(np.stack(list(self._buf)[self.K :]), axis=0)
        return newer - older
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_framebuffer.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/framebuffer.py tests/unit/test_framebuffer.py
git commit -m "core: SUTR framebuffer with K-window difference"
```

### Task 3.5: Per-row local sky subtraction

**Files:**
- Create: `henrietta_guider/core/sky.py`
- Create: `tests/unit/test_sky.py`

For each row of a stamp, the per-row sky pedestal is the median of the
outer 1/6 of pixels on each side (combined). Mask-aware: bad pixels
(`good == False`) are excluded from the median. Returns the
sky-subtracted stamp **and** the per-row sky values (so the caller can
also compute the single `sky_background_adu` summary).

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_sky.py`:

```python
import numpy as np
import pytest

from henrietta_guider.core.sky import subtract_local_sky


def _stamp_with_constant_sky_and_trace(ny: int, nx: int,
                                       sky_level: float = 50.0,
                                       trace_amplitude: float = 1000.0
                                       ) -> np.ndarray:
    img = np.full((ny, nx), sky_level, dtype=np.float32)
    img[:, nx // 2] += trace_amplitude  # narrow trace down the middle
    return img


@pytest.mark.unit
class TestSubtractLocalSky:
    def test_uniform_sky_removed_to_zero(self):
        ny, nx = 100, 60
        img = _stamp_with_constant_sky_and_trace(ny, nx, sky_level=42.0)
        good = np.ones((ny, nx), dtype=bool)
        sub, per_row = subtract_local_sky(img, good)
        # Outside the trace column, pixels should now be ~0.
        flat_offrow = np.delete(sub, nx // 2, axis=1)
        np.testing.assert_allclose(flat_offrow.mean(), 0.0, atol=1e-6)
        # per-row sky is the constant 42 for every row.
        np.testing.assert_allclose(per_row, 42.0)

    def test_per_row_gradient_followed(self):
        # Sky has a row-dependent pedestal: row 0 -> 10, row N-1 -> 100.
        ny, nx = 50, 60
        sky_per_row = np.linspace(10.0, 100.0, ny, dtype=np.float32)
        img = np.repeat(sky_per_row[:, None], nx, axis=1)
        good = np.ones_like(img, dtype=bool)
        sub, per_row = subtract_local_sky(img, good)
        np.testing.assert_allclose(sub, 0.0, atol=1e-6)
        np.testing.assert_allclose(per_row, sky_per_row, atol=1e-6)

    def test_bad_pixels_excluded_from_sky(self):
        ny, nx = 10, 60
        img = np.full((ny, nx), 50.0, dtype=np.float32)
        # Drop a wild outlier into the left sky band: would skew the
        # median if it were included.
        img[5, 2] = 99999.0
        good = np.ones_like(img, dtype=bool)
        good[5, 2] = False
        sub, per_row = subtract_local_sky(img, good)
        # Median of all-50 outer-1/6 (after masking the wild pixel) -> 50.
        assert per_row[5] == pytest.approx(50.0)

    def test_outer_one_sixth_is_used(self):
        # Width 60 -> outer 1/6 = 10 pixels each side. Put a poison
        # pixel JUST INSIDE the boundary (column 10), which should NOT
        # affect the row median.
        ny, nx = 5, 60
        img = np.full((ny, nx), 50.0, dtype=np.float32)
        img[:, 10] = 9999.0  # column 10 is OUTSIDE the outer 1/6 (which
                             # spans cols 0..9 and 50..59)
        good = np.ones_like(img, dtype=bool)
        _, per_row = subtract_local_sky(img, good)
        np.testing.assert_allclose(per_row, 50.0)
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_sky.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/sky.py`.**

```python
"""Per-row local sky subtraction for stamps.

For each row of the stamp, the sky pedestal is the median of the outer
1/6 of pixels on **each** side (so 1/6 left + 1/6 right = 1/3 total
sampled per row), pooled into one value. Bad pixels (good == False in
the mask) are excluded from the median. The pedestal is subtracted
from every column in that row.

This matches ALGORITHM.md's sky step (`edge = sub.shape[1] // 6` then
both bands). It removes detector pedestal differences between reads,
sky-background gradients along the trace, and slow per-frame H2RG bias
drift — all of which would otherwise bias the cross-correlation peak
away from the structure that carries position information.
"""

from __future__ import annotations

import numpy as np


def subtract_local_sky(
    stamp: np.ndarray,
    good: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (sky-subtracted stamp, per-row sky values).

    `stamp` and `good` must have the same shape (ny, nx). `good` is the
    bad-pixel mask (True = good).
    """
    if stamp.shape != good.shape:
        raise ValueError(f"shape mismatch: {stamp.shape} vs {good.shape}")
    ny, nx = stamp.shape
    edge = max(1, nx // 6)
    # Build a boolean column-mask: True for the outer-1/6 columns on each side.
    edge_cols = np.zeros(nx, dtype=bool)
    edge_cols[:edge] = True
    edge_cols[-edge:] = True
    # Apply both column-mask and good-pixel mask for each row.
    masked = np.where(good & edge_cols[None, :], stamp, np.nan)
    per_row = np.nanmedian(masked, axis=1).astype(stamp.dtype)
    return stamp - per_row[:, None], per_row
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_sky.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/sky.py tests/unit/test_sky.py
git commit -m "core: per-row local sky subtraction (outer 1/6, mask-aware)"
```

### Task 3.6: 2-D xcor + parabolic sub-pixel peak

**Files:**
- Create: `henrietta_guider/core/xcor.py`
- Create: `tests/unit/test_xcor.py`

The heart of the measurement, per `ALGORITHM.md`. Brute-force 2-D cross-
correlation of the bg-subtracted, masked guide image against a fixed
template, over a ±`search` pixel search window. Parabolic peak fit in
each axis independently to recover sub-pixel shifts. Returns
`(dx_px, dy_px, peak_value, curvature_x, curvature_y)`.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_xcor.py`:

```python
import numpy as np
import pytest

from henrietta_guider.core.xcor import xcor_2d


def _gaussian_trace(ny: int = 200, nx: int = 50,
                    x_center: float = 25.0,
                    fwhm_px: float = 3.5) -> np.ndarray:
    """Synthetic stamp: a Gaussian trace running down Y."""
    sigma = fwhm_px / 2.355
    x = np.arange(nx)[None, :]
    profile = np.exp(-((x - x_center) ** 2) / (2 * sigma**2))
    # Y modulation: a slow continuum + a couple of "absorption" dips.
    cont = 1.0 + 0.10 * np.sin(np.linspace(0, 6.0, ny))
    cont -= 0.40 * np.exp(-((np.arange(ny) - 60) ** 2) / 8.0)
    cont -= 0.30 * np.exp(-((np.arange(ny) - 140) ** 2) / 12.0)
    return (profile * cont[:, None] * 1000.0).astype(np.float32)


def _shift_image(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Integer-shift (no interpolation; used only for integer-truth tests)."""
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


@pytest.mark.unit
class TestXcor2D:
    def test_zero_shift_returns_zero(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.05)

    def test_integer_x_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image(template, dx=3, dy=0)
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(3.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.05)

    def test_integer_y_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image(template, dx=0, dy=-5)
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.0, abs=0.05)
        assert result.dy_px == pytest.approx(-5.0, abs=0.05)

    def test_subpixel_x_shift_recovered(self):
        # 0.4 px X-shift via cubic spline. Tolerance is 0.10 px to
        # accommodate the combined bias of (cubic interpolation ~ a few
        # 0.01 px) + (parabolic-peak fit on a ~Gaussian xcor surface ~
        # a few 0.01 px). A real on-sky test will tighten this once we
        # know the actual point-spread function.
        template = _gaussian_trace()
        from scipy.ndimage import shift as scipy_shift
        data = scipy_shift(template, (0.0, 0.4), order=3, mode="reflect")
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.4, abs=0.10)
        assert result.dy_px == pytest.approx(0.0, abs=0.10)

    def test_subpixel_y_shift_recovered(self):
        from scipy.ndimage import shift as scipy_shift
        template = _gaussian_trace()
        data = scipy_shift(template, (0.25, 0.0), order=3, mode="reflect")
        result = xcor_2d(data, template, search=12)
        assert result.dy_px == pytest.approx(0.25, abs=0.10)

    def test_curvature_positive_at_peak(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=8)
        # Parabolic curvature at the peak is (a - 2b + c) where b is the
        # max. For a Gaussian-like correlation surface this is negative
        # (concave down) — we record the negative-magnitude value as a
        # precision proxy. Magnitude > 0 is what the GUI displays.
        assert result.curvature_x < 0.0
        assert result.curvature_y < 0.0

    def test_search_window_too_small_clips_peak(self):
        # If true shift exceeds the search radius, the integer peak
        # lands at the edge — peak_value still positive, but the
        # parabolic fit may be unreliable. The function should not
        # crash; it should return a peak at the edge.
        from scipy.ndimage import shift as scipy_shift
        template = _gaussian_trace()
        data = scipy_shift(template, (0.0, 15.0), order=3, mode="reflect")
        result = xcor_2d(data, template, search=5)
        # Just verify no crash; the recovered shift will be roughly +5
        # (clipped) or wraparound — implementation-defined.
        assert result.peak_value > 0.0
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_xcor.py -v`
Expected: import error / collection error.

- [ ] **Step 3: Implement `core/xcor.py`.**

```python
"""2-D cross-correlation with parabolic sub-pixel peak (ALGORITHM.md).

For each candidate (dx, dy) in a ±`search` window, compute:
    C(dx, dy) = sum_y sum_x  T(x, y) * D(x + dx, y + dy)
The integer peak is at argmax(C). A parabolic fit to the three
correlation values around the peak in each axis independently gives
sub-pixel refinement:
    sub = 0.5 * (a - c) / (a - 2b + c)
The curvature (a - 2b + c) is recorded as a precision proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class XcorResult:
    dx_px: float
    dy_px: float
    peak_value: float
    curvature_x: float
    curvature_y: float


def xcor_2d(
    data: np.ndarray,
    template: np.ndarray,
    search: int = 12,
) -> XcorResult:
    """Brute-force 2-D xcor with parabolic sub-pixel peak.

    Sign convention: returns the (dx, dy) such that
    ``data ≈ np.roll(template, (dy, dx))``. So a positive dx means the
    data is shifted to the +X direction relative to the template, and
    the integer-shift unit test ``data = np.roll(template, dx=+3, axis=1)``
    recovers ``dx_px ≈ +3``. Downstream geometry.py negates this to
    produce the telescope correction.
    """
    if data.shape != template.shape:
        raise ValueError(f"shape mismatch: {data.shape} vs {template.shape}")

    ny, nx = template.shape
    n_dx = 2 * search + 1
    n_dy = 2 * search + 1
    C = np.zeros((n_dy, n_dx), dtype=np.float64)

    # For each candidate (dx, dy), align the overlapping region of D
    # against T. Rolling D and dropping the wrap is simpler and within a
    # search window of ±12 on a ~70k-pixel stamp finishes in well under
    # 100 ms.
    for iy, dy in enumerate(range(-search, search + 1)):
        for ix, dx in enumerate(range(-search, search + 1)):
            y_lo_t = max(0, -dy)
            y_hi_t = ny - max(0, dy)
            x_lo_t = max(0, -dx)
            x_hi_t = nx - max(0, dx)
            t_view = template[y_lo_t:y_hi_t, x_lo_t:x_hi_t]
            d_view = data[
                y_lo_t + dy : y_hi_t + dy,
                x_lo_t + dx : x_hi_t + dx,
            ]
            C[iy, ix] = float(np.sum(t_view * d_view))

    iy_peak, ix_peak = np.unravel_index(int(np.argmax(C)), C.shape)
    peak_value = float(C[iy_peak, ix_peak])

    sub_x, curv_x = _parabolic_sub(C, iy_peak, ix_peak, axis="x")
    sub_y, curv_y = _parabolic_sub(C, iy_peak, ix_peak, axis="y")

    dx = (ix_peak - search) + sub_x
    dy = (iy_peak - search) + sub_y
    return XcorResult(
        dx_px=dx, dy_px=dy,
        peak_value=peak_value,
        curvature_x=curv_x, curvature_y=curv_y,
    )


def _parabolic_sub(
    C: np.ndarray,
    iy: int,
    ix: int,
    axis: str,
) -> tuple[float, float]:
    """Sub-pixel refinement via parabolic fit on the 3 values around the peak.

    Returns (sub-pixel offset, curvature). When the peak sits on the
    edge of the search window, returns (0.0, 0.0) — the integer peak
    is the best we can do.
    """
    ny, nx = C.shape
    if axis == "x":
        if ix == 0 or ix == nx - 1:
            return 0.0, 0.0
        a, b, c = C[iy, ix - 1], C[iy, ix], C[iy, ix + 1]
    else:
        if iy == 0 or iy == ny - 1:
            return 0.0, 0.0
        a, b, c = C[iy - 1, ix], C[iy, ix], C[iy + 1, ix]
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0, 0.0
    return 0.5 * (a - c) / denom, denom
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_xcor.py -v`
Expected: all green. The sub-pixel recovery tests are the most
informative: they should land within 0.05 px of injected truth.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/xcor.py tests/unit/test_xcor.py
git commit -m "core: 2-D xcor with parabolic sub-pixel peak (per ALGORITHM.md)"
```

### Task 3.7: Template build

**Files:**
- Create: `henrietta_guider/core/template.py`
- Create: `tests/unit/test_template.py`

Reads a `henNNNN.fits` (the Archon's slope-fit final), extracts the
science stamp, applies the BPM, runs `subtract_local_sky`, and validates
the result. Failure modes: missing file, FITS read error, too few
unmasked pixels, zero variance after sky subtraction. Returns a
`Template` value object on success.

- [ ] **Step 1: Write the failing tests.**

Create `tests/unit/test_template.py`:

```python
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from henrietta_guider.core.template import (
    TemplateBuildError,
    build_template,
)
from henrietta_guider.core.types import Stamp


def _write_synthetic_henNNNN(path: Path, ny: int = 2048, nx: int = 2048,
                             trace_x: int = 110) -> None:
    img = np.full((ny, nx), 50.0, dtype=np.float32)
    # A bright trace in the science stamp region.
    img[600:1980, trace_x - 2 : trace_x + 3] += 2000.0
    fits.PrimaryHDU(img.astype(np.int16)).writeto(path, overwrite=True)


@pytest.mark.unit
class TestBuildTemplate:
    def _stamp(self) -> Stamp:
        return Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)

    def test_happy_path(self, tmp_path: Path):
        p = tmp_path / "hen0042.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        tmpl = build_template(p, self._stamp(), good)
        assert tmpl.frame_number == 42
        # (y_hi-y_lo, 2*halfwidth + 1) per ALGORITHM.md.
        assert tmpl.image.shape == (1380, 51)
        # Sky should be subtracted: median of off-trace pixels ~ 0.
        offtrace = tmpl.image[:, :15]  # leftmost 15 cols (sky band)
        assert abs(np.median(offtrace)) < 5.0

    def test_filename_parsed(self, tmp_path: Path):
        p = tmp_path / "hen1764.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        tmpl = build_template(p, self._stamp(), good)
        assert tmpl.frame_number == 1764

    def test_missing_file_raises(self, tmp_path: Path):
        # Use a filename that PASSES the henNNNN.fits regex so the open
        # is what fails (not the regex check).
        with pytest.raises(TemplateBuildError, match="open"):
            build_template(tmp_path / "hen9999.fits", self._stamp(),
                           np.ones((2048, 2048), dtype=bool))

    def test_too_few_unmasked_raises(self, tmp_path: Path):
        p = tmp_path / "hen0001.fits"
        _write_synthetic_henNNNN(p)
        # Mark almost every pixel bad.
        good = np.zeros((2048, 2048), dtype=bool)
        good[1000, 110] = True   # one good pixel
        with pytest.raises(TemplateBuildError, match="unmasked"):
            build_template(p, self._stamp(), good)

    def test_zero_variance_raises(self, tmp_path: Path):
        p = tmp_path / "hen0002.fits"
        # Flat image -> after sky subtraction the stamp is zero -> no variance.
        fits.PrimaryHDU(
            np.full((2048, 2048), 50.0, dtype=np.int16)
        ).writeto(p, overwrite=True)
        good = np.ones((2048, 2048), dtype=bool)
        with pytest.raises(TemplateBuildError, match="variance"):
            build_template(p, self._stamp(), good)

    def test_unparseable_filename_raises(self, tmp_path: Path):
        p = tmp_path / "weird.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        with pytest.raises(TemplateBuildError, match="filename"):
            build_template(p, self._stamp(), good)
```

- [ ] **Step 2: Run tests; confirm they fail.**

Run: `uv run pytest tests/unit/test_template.py -v`
Expected: import error.

- [ ] **Step 3: Implement `core/template.py`.**

```python
"""Template build from a slope-fit henNNNN.fits.

Steps:
  1. Open the FITS, read primary HDU as a 2-D float array.
  2. Extract the stamp [y_lo:y_hi, x_min:x_max).
  3. Apply the BPM (slice the master good-pixel mask).
  4. Subtract per-row local sky (sky.subtract_local_sky).
  5. Validate: enough unmasked pixels, non-zero variance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from .sky import subtract_local_sky
from .types import Stamp

_FNAME_RE = re.compile(r"^hen(\d{4})\.fits$")


class TemplateBuildError(Exception):
    """Raised by build_template on any failure mode."""


@dataclass(frozen=True)
class Template:
    """A built template: bg-subtracted, masked stamp + provenance."""

    image: np.ndarray
    good: np.ndarray
    frame_number: int
    stamp: Stamp


def build_template(
    path: str | Path,
    stamp: Stamp,
    good_full: np.ndarray,
    min_unmasked_fraction: float = 0.50,
    min_variance: float = 1e-6,
) -> Template:
    """Build a template from a henNNNN.fits slope-fit file."""
    p = Path(path).expanduser()
    m = _FNAME_RE.match(p.name)
    if m is None:
        raise TemplateBuildError(
            f"unparseable filename: {p.name!r} (expected henNNNN.fits)"
        )
    frame_number = int(m.group(1))

    try:
        with fits.open(p) as hdul:
            full = np.asarray(hdul[0].data, dtype=np.float32)
    except FileNotFoundError as e:
        raise TemplateBuildError(f"failed to open {p}") from e
    except Exception as e:
        raise TemplateBuildError(f"failed to open {p}: {e}") from e

    stamp_img = full[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max].copy()
    good_stamp = good_full[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max].copy()

    n_unmasked = int(good_stamp.sum())
    n_total    = good_stamp.size
    if n_unmasked < min_unmasked_fraction * n_total:
        raise TemplateBuildError(
            f"too few unmasked pixels: {n_unmasked} / {n_total}"
        )

    sub, _ = subtract_local_sky(stamp_img, good_stamp)
    # Mask out bad pixels in the returned image (set to 0 so they don't
    # contribute to xcor sums).
    sub = np.where(good_stamp, sub, 0.0)
    if float(np.var(sub[good_stamp])) < min_variance:
        raise TemplateBuildError("zero variance after sky subtraction")

    return Template(image=sub, good=good_stamp, frame_number=frame_number, stamp=stamp)
```

- [ ] **Step 4: Run tests; confirm they pass.**

Run: `uv run pytest tests/unit/test_template.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/template.py tests/unit/test_template.py
git commit -m "core: template build from slope-fit henNNNN.fits"
```

### Task 3.8: End-of-chunk verification

- [ ] **Step 1: Run the full test suite.**

Run: `make test`
Expected: all unit tests across `tests/unit/` (wire, tcs_client, geometry, controller, types, config, bpm, framebuffer, sky, xcor, template) pass.

- [ ] **Step 2: Run lint.**

Run: `make lint`
Expected: clean.

- [ ] **Step 3: Push and confirm CI passes.**

```bash
git log --oneline -10
git push
gh run watch
```

Expected: green check.

End of Chunk 3. Working state: every reduction primitive implemented and
tested in isolation. The autoguider can now load a BPM, accumulate SUTR
reads into K-window differences, sky-subtract a stamp, build a template
from a `henNNNN.fits`, and run a 2-D xcor against it — but no
orchestration yet.

---

## Chunk 4: Run-time monitors

**Goal:** Land the four run-time-monitoring modules: out-of-family
quality stats, sequential-order sanity checks, target-switch detection,
and the stale-frame watchdog. These all consume `MeasurementRow`-like
events or per-frame metadata and emit alert events; they do not touch
files or sockets.

### Task 4.1: Out-of-family quality stats

**Files:**
- Create: `henrietta_guider/core/quality.py`
- Create: `tests/unit/test_quality.py`

Maintains running median + MAD over the last `out_of_family_window`
in-family frames per metric (`trace_flux_adu`, `trace_fwhm_x_px`,
`sky_background_adu`, `xcor_peak_value`, `dx_px`, `dy_px`). Has a
warmup phase (`out_of_family_warmup_n` in-family frames) before any
ALERTED can fire. Auto-resume after `auto_resume_in_family` consecutive
in-family frames.

- [ ] **Step 1: Failing tests in `tests/unit/test_quality.py`.**

```python
import pytest

from henrietta_guider.core.quality import OutOfFamilyDetector


@pytest.mark.unit
class TestOutOfFamilyDetector:
    def test_no_alert_during_warmup(self):
        det = OutOfFamilyDetector(window=20, warmup=10, sigma_threshold=5.0)
        # Push one obvious outlier on the very first frame: must not alert.
        verdict = det.update({"trace_flux_adu": 1e9})
        assert verdict.alerted is False
        assert verdict.warming_up is True

    def test_alerts_after_warmup_on_outlier(self):
        det = OutOfFamilyDetector(window=20, warmup=10, sigma_threshold=5.0)
        # Seed 10 in-family frames with flux ~ 1e5, FWHM ~ 3.0.
        for _ in range(10):
            det.update({"trace_flux_adu": 1.0e5, "trace_fwhm_x_px": 3.0})
        v = det.update({"trace_flux_adu": 1.0e3, "trace_fwhm_x_px": 3.0})
        assert v.alerted is True
        assert "trace_flux_adu" in v.offenders

    def test_auto_resume_after_n_in_family(self):
        det = OutOfFamilyDetector(window=20, warmup=10, sigma_threshold=5.0,
                                  auto_resume_in_family=3)
        for _ in range(10):
            det.update({"trace_flux_adu": 1.0e5})
        # An outlier alerts.
        v = det.update({"trace_flux_adu": 1.0e3})
        assert v.alerted is True
        # Three in-family frames — the third resumes.
        for i in range(2):
            v = det.update({"trace_flux_adu": 1.0e5})
            assert v.alerted is False
            assert v.guiding is False  # still in alerted-pending-resume
        v = det.update({"trace_flux_adu": 1.0e5})
        assert v.alerted is False
        assert v.guiding is True

    def test_multiple_offenders_listed(self):
        det = OutOfFamilyDetector(window=20, warmup=5, sigma_threshold=5.0)
        for _ in range(5):
            det.update({"trace_flux_adu": 1e5, "trace_fwhm_x_px": 3.0,
                        "sky_background_adu": 60.0})
        v = det.update({"trace_flux_adu": 1e2,
                        "trace_fwhm_x_px": 30.0,        # also outlier
                        "sky_background_adu": 60.0})
        assert "trace_flux_adu" in v.offenders
        assert "trace_fwhm_x_px" in v.offenders
        assert "sky_background_adu" not in v.offenders

    def test_window_evicts_oldest(self):
        det = OutOfFamilyDetector(window=5, warmup=3, sigma_threshold=5.0)
        # Push a low-flux baseline.
        for _ in range(5):
            det.update({"trace_flux_adu": 100.0})
        # Then 5 new high-flux frames push out the lows; baseline shifts.
        for _ in range(5):
            det.update({"trace_flux_adu": 10000.0})
        # Now 100.0 is the outlier.
        v = det.update({"trace_flux_adu": 100.0})
        assert v.alerted is True
```

- [ ] **Step 2: Run tests; confirm import-error fail.**

Run: `uv run pytest tests/unit/test_quality.py -v`

- [ ] **Step 3: Implement `core/quality.py`.**

```python
"""Out-of-family detector: running median + MAD with warmup and auto-resume.

Per spec §5: maintain a rolling window of in-family measurements per
metric. Once warmup completes, any new measurement deviating from the
metric's running median by > sigma_threshold * (1.4826 * MAD) flags
the frame as ALERTED. After auto_resume_in_family consecutive
in-family frames, state returns to GUIDING.

The detector is metric-agnostic: callers pass any dict of {name: value}.
Only the metrics present in the dict are checked.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class OutOfFamilyVerdict:
    alerted: bool          # True if THIS frame is an outlier
    warming_up: bool       # True until warmup is satisfied
    guiding: bool          # True == clean GUIDING state; False == ALERTED or pending-resume
    offenders: tuple[str, ...]  # which metrics tripped


@dataclass
class OutOfFamilyDetector:
    window: int = 20
    warmup: int = 10
    sigma_threshold: float = 5.0
    auto_resume_in_family: int = 3
    _buffers: dict[str, collections.deque[float]] = field(default_factory=dict)
    _in_family_warmup_count: int = 0
    _alerted: bool = False
    _consec_in_family_after_alert: int = 0

    MAD_SCALE: float = 1.4826  # sigma equivalent

    def update(self, metrics: dict[str, float]) -> OutOfFamilyVerdict:
        warming_up = self._in_family_warmup_count < self.warmup
        offenders: list[str] = []
        if not warming_up:
            for name, value in metrics.items():
                buf = self._buffers.get(name)
                if buf is None or len(buf) == 0:
                    continue
                med = float(np.median(buf))
                mad = float(np.median(np.abs(np.array(buf) - med)))
                sigma = self.MAD_SCALE * mad
                if sigma == 0.0:
                    # Degenerate (all buffered values identical): any
                    # deviation IS an outlier. Real data has scatter
                    # so this only matters for synthetic tests, but we
                    # define it cleanly here.
                    if value != med:
                        offenders.append(name)
                    continue
                if abs(value - med) > self.sigma_threshold * sigma:
                    offenders.append(name)

        is_in_family = not offenders

        # Update buffers with in-family values only (so an outlier does
        # not poison future medians). Note: during warmup `is_in_family`
        # is True for any value (the outlier check is gated by
        # warming_up=False), so warmup is "simply being seeded" per
        # spec §5 — extreme first-frame values WILL enter the buffer.
        # Acceptable: real data has scatter, and this is the same
        # behaviour as initialising a Kalman filter from whatever
        # arrives first.
        if is_in_family:
            for name, value in metrics.items():
                buf = self._buffers.setdefault(name, collections.deque(maxlen=self.window))
                buf.append(value)
            if warming_up:
                self._in_family_warmup_count += 1

        # Alert / resume state machine.
        alerted_now = bool(offenders)
        if alerted_now:
            self._alerted = True
            self._consec_in_family_after_alert = 0
            guiding = False
        elif self._alerted:
            if is_in_family:
                self._consec_in_family_after_alert += 1
                if self._consec_in_family_after_alert >= self.auto_resume_in_family:
                    self._alerted = False
                    self._consec_in_family_after_alert = 0
                    guiding = True
                else:
                    guiding = False
            else:
                guiding = False
        else:
            guiding = not warming_up  # in clean GUIDING after warmup; PRE-warmup is "not alerted, not guiding-confirmed"

        return OutOfFamilyVerdict(
            alerted=alerted_now,
            warming_up=warming_up,
            guiding=guiding,
            offenders=tuple(offenders),
        )
```

- [ ] **Step 4: Run tests; confirm green.**

Run: `uv run pytest tests/unit/test_quality.py -v`

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/quality.py tests/unit/test_quality.py
git commit -m "core: out-of-family detector (median + MAD, warmup, auto-resume)"
```

### Task 4.2: Sequential-order sanity checks

**Files:**
- Create: `henrietta_guider/core/sanity.py`
- Create: `tests/unit/test_sanity.py`

Per spec §4 "Sequential-order sanity checks". Three tiers:

- **Skipped SUTR within frame** (sutr > last+1): WARN log, lose one
  K-window diff, continue.
- **Out-of-order or repeated SUTR** (sutr ≤ last): WARN + audible +
  discard the file.
- **Skipped frame numbers** (gap > 1): INFO log, normal operation.
- **Backwards or repeated frame_number** (≤ last): WARN + audible +
  discard.

The module returns a `SanityVerdict` describing the action; the worker
acts on it (drops the file, emits an alert event, etc.).

- [ ] **Step 1: Failing tests.**

```python
import pytest

from henrietta_guider.core.sanity import (
    SanityChecker,
    SanityAction,
)


@pytest.mark.unit
class TestSanityChecker:
    def test_first_ever_file_accepted(self):
        ck = SanityChecker()
        v = ck.check(frame_number=1, sutr_number=1)
        assert v.action is SanityAction.ACCEPT

    def test_normal_sequence_accepted(self):
        ck = SanityChecker()
        for sutr in range(1, 5):
            v = ck.check(frame_number=10, sutr_number=sutr)
            assert v.action is SanityAction.ACCEPT

    def test_skipped_sutr_within_frame_warns_but_accepts(self):
        ck = SanityChecker()
        ck.check(10, 1); ck.check(10, 2); ck.check(10, 3)
        v = ck.check(10, 5)  # skipped 4
        assert v.action is SanityAction.WARN_ACCEPT
        assert "sutr_skip" in v.tags
        assert v.audible is False

    def test_out_of_order_sutr_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(10, 1); ck.check(10, 2); ck.check(10, 5)
        v = ck.check(10, 3)
        assert v.action is SanityAction.WARN_DISCARD
        assert "sutr_out_of_order" in v.tags
        assert v.audible is True

    def test_repeated_sutr_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(10, 1); ck.check(10, 2)
        v = ck.check(10, 2)
        assert v.action is SanityAction.WARN_DISCARD
        assert "sutr_out_of_order" in v.tags
        assert v.audible is True

    def test_skipped_frame_numbers_logged_at_info_level(self):
        ck = SanityChecker()
        ck.check(10, 1)
        v = ck.check(15, 1)  # 5 frames skipped — normal operation
        assert v.action is SanityAction.ACCEPT
        assert "frame_skip" in v.tags
        assert v.audible is False

    def test_backwards_frame_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(15, 1)
        v = ck.check(10, 1)
        assert v.action is SanityAction.WARN_DISCARD
        assert "frame_backwards" in v.tags
        assert v.audible is True
```

- [ ] **Step 2: Run; confirm fail.**

- [ ] **Step 3: Implement `core/sanity.py`.**

```python
"""Sequential-order sanity checks for incoming SUTR / slope-frame events.

See spec §4 "Sequential-order sanity checks". Returns a SanityVerdict;
the worker acts on it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class SanityAction(enum.Enum):
    ACCEPT       = "accept"
    WARN_ACCEPT  = "warn_accept"      # log WARN, accept the file
    WARN_DISCARD = "warn_discard"     # log WARN + audible alert, discard


@dataclass(frozen=True)
class SanityVerdict:
    action: SanityAction
    audible: bool          # True if the GUI should play the warning sound
    tags: tuple[str, ...]  # for log message + quality_flags


@dataclass
class SanityChecker:
    last_frame: int | None = None
    last_sutr: int | None = None

    def check(self, frame_number: int, sutr_number: int) -> SanityVerdict:
        # Across frames first, since a new frame resets per-frame state.
        if self.last_frame is not None and frame_number <= self.last_frame:
            return SanityVerdict(SanityAction.WARN_DISCARD, True, ("frame_backwards",))

        tags: list[str] = []
        if self.last_frame is not None and frame_number > self.last_frame + 1:
            # Skipped frame numbers — normal operationally (operator
            # aborted exposures); log at INFO via a tag the caller maps.
            tags.append("frame_skip")

        new_frame = self.last_frame is None or frame_number != self.last_frame
        if new_frame:
            # Reset per-frame SUTR tracker; sutr should be 1 normally
            # but we're tolerant of any value at frame boundary.
            self.last_frame = frame_number
            self.last_sutr  = sutr_number
            return SanityVerdict(SanityAction.ACCEPT, False, tuple(tags))

        # Within the same frame: enforce monotonicity.
        assert self.last_sutr is not None
        if sutr_number <= self.last_sutr:
            return SanityVerdict(
                SanityAction.WARN_DISCARD, True, (*tags, "sutr_out_of_order"),
            )
        if sutr_number > self.last_sutr + 1:
            self.last_sutr = sutr_number
            return SanityVerdict(
                SanityAction.WARN_ACCEPT, False, (*tags, "sutr_skip"),
            )
        self.last_sutr = sutr_number
        return SanityVerdict(SanityAction.ACCEPT, False, tuple(tags))
```

- [ ] **Step 4: Run; confirm green.**

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/sanity.py tests/unit/test_sanity.py
git commit -m "core: sequential-order sanity checks (SUTR + frame_number)"
```

### Task 4.3: Target-switch detection

**Files:**
- Create: `henrietta_guider/core/target_switch.py`
- Create: `tests/unit/test_target_switch.py`

Two-tier severity per spec §4 "Target-switch detection":

- **Pointing jump** (`sky distance > target_switch_arcsec_threshold`):
  ERROR + spoken phrase + state → REFERENCE_PENDING.
- **OBJECT-only change** (no pointing jump): WARN + small beep, no
  state change.
- **Both** → pointing-jump path wins.

- [ ] **Step 1: Failing tests.**

```python
import math

import pytest

from henrietta_guider.core.target_switch import (
    TargetSwitchDetector,
    TargetSwitchVerdict,
)


@pytest.mark.unit
class TestTargetSwitchDetector:
    def test_first_call_no_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        v = det.update(ra_deg=10.0, dec_deg=-30.0, object_name="STAR_A")
        assert v.severity == "none"

    def test_small_drift_no_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        # Move by 5" in RA: well under threshold.
        v = det.update(ra_deg=10.0 + 5.0/3600.0/math.cos(math.radians(-30.0)),
                       dec_deg=-30.0, object_name="A")
        assert v.severity == "none"

    def test_pointing_jump_full_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        # Move by 30" in Dec.
        v = det.update(ra_deg=10.0, dec_deg=-30.0 + 30.0/3600.0,
                       object_name="A")
        assert v.severity == "pointing"
        assert v.audible is True
        # Spec §4 specifies the exact spoken text.
        assert v.spoken_phrase == "target change possible"
        assert v.distance_arcsec == pytest.approx(30.0, abs=0.5)

    def test_object_only_change_soft_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        v = det.update(ra_deg=10.0, dec_deg=-30.0, object_name="B")
        assert v.severity == "object_only"
        assert v.audible is False             # tiny beep handled outside
        assert v.spoken_phrase is None

    def test_both_signals_pointing_wins(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        v = det.update(ra_deg=10.0, dec_deg=-30.0 + 30.0/3600.0,
                       object_name="B")
        assert v.severity == "pointing"

    def test_reset_clears_previous(self):
        # After reset() the next call should NOT compare against the old
        # frame (e.g., used after Save Reference clears running state).
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        det.reset()
        v = det.update(ra_deg=20.0, dec_deg=+45.0, object_name="Z")
        assert v.severity == "none"

    def test_costheta_correction(self):
        # At Dec=-60°, dRA in arcsec = dRA_deg * cos(-60°) * 3600.
        # 30" RA-on-sky at Dec=-60 corresponds to dRA_deg = 30/(0.5*3600).
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=0.0, dec_deg=-60.0, object_name="A")
        v = det.update(ra_deg=30.0/3600.0/0.5, dec_deg=-60.0,
                       object_name="A")
        assert v.severity == "pointing"
        assert v.distance_arcsec == pytest.approx(30.0, abs=0.5)
```

- [ ] **Step 2: Run; confirm fail.**

- [ ] **Step 3: Implement `core/target_switch.py`.**

```python
"""Target-switch detection. Two signals, two severities (spec §4).

Pointing jump (>= threshold arcsec on-sky) -> full alert + spoken phrase
+ caller transitions to REFERENCE_PENDING. OBJECT-only change ->
soft signal + caller does a small beep. Pointing-jump wins on ties.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetSwitchVerdict:
    severity: str                   # "none" | "object_only" | "pointing"
    audible: bool                   # play warning sound (pointing only)
    spoken_phrase: str | None       # speech text (pointing only)
    distance_arcsec: float          # 0.0 if no previous frame
    object_changed: bool


@dataclass
class TargetSwitchDetector:
    threshold_arcsec: float = 20.0
    _last_ra_deg: float | None = None
    _last_dec_deg: float | None = None
    _last_object: str | None = None

    def reset(self) -> None:
        self._last_ra_deg = None
        self._last_dec_deg = None
        self._last_object = None

    def update(
        self,
        ra_deg: float,
        dec_deg: float,
        object_name: str,
    ) -> TargetSwitchVerdict:
        if self._last_ra_deg is None:
            self._last_ra_deg = ra_deg
            self._last_dec_deg = dec_deg
            self._last_object = object_name
            return TargetSwitchVerdict("none", False, None, 0.0, False)

        # Compute on-sky distance with cos(Dec) correction on RA.
        cos_dec = math.cos(math.radians((dec_deg + self._last_dec_deg) / 2.0))
        d_ra_arc  = (ra_deg - self._last_ra_deg) * cos_dec * 3600.0
        d_dec_arc = (dec_deg - self._last_dec_deg) * 3600.0
        dist = math.hypot(d_ra_arc, d_dec_arc)

        object_changed = (object_name != self._last_object)

        # Update before returning (so a subsequent call sees the new state).
        self._last_ra_deg = ra_deg
        self._last_dec_deg = dec_deg
        self._last_object = object_name

        if dist > self.threshold_arcsec:
            return TargetSwitchVerdict(
                "pointing", True, "target change possible", dist, object_changed,
            )
        if object_changed:
            return TargetSwitchVerdict(
                "object_only", False, None, dist, True,
            )
        return TargetSwitchVerdict("none", False, None, dist, False)
```

- [ ] **Step 4: Run; confirm green.**

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/target_switch.py tests/unit/test_target_switch.py
git commit -m "core: two-tier target-switch detector (pointing-jump + OBJECT)"
```

### Task 4.4: Stale-frame watchdog timer

**Files:**
- Create: `henrietta_guider/core/stale.py`
- Create: `tests/unit/test_stale.py`

Tracks elapsed seconds since the last accepted guide image. If the
timeout (default 30 s) is exceeded, emits a "stale" event. Gated:
doesn't tick until at least one guide image has been accepted; resets
on watch-dir change and on first accepted guide image after a frame
boundary.

The module exposes `arm()`, `note_accepted()`, `note_frame_boundary()`,
`note_watch_dir_changed()`, and `is_stale(now: float) -> bool`. Time
comes from the caller (so tests don't need real sleeps).

- [ ] **Step 1: Failing tests.**

```python
import pytest

from henrietta_guider.core.stale import StaleFrameWatchdog


@pytest.mark.unit
class TestStaleFrameWatchdog:
    def test_not_stale_before_first_accept(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        # No guide image accepted yet -> never stale, no matter how
        # long has passed.
        assert wd.is_stale(t_now=120.0) is False

    def test_becomes_stale_after_timeout(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        assert wd.is_stale(t_now=39.0) is False
        assert wd.is_stale(t_now=41.0) is True

    def test_accept_resets_timer(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_accepted(t_now=35.0)
        assert wd.is_stale(t_now=60.0) is False
        assert wd.is_stale(t_now=66.0) is True  # 35 + 31

    def test_frame_boundary_resets(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_frame_boundary(t_now=29.0)
        # Boundary doesn't itself count as an accept, but resets the
        # timer so we don't false-trip during the 2K warmup.
        assert wd.is_stale(t_now=58.0) is False
        assert wd.is_stale(t_now=60.0) is True

    def test_watch_dir_change_disarms(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_watch_dir_changed(t_now=20.0)
        # After dir change: must wait for a new accept before being stale.
        assert wd.is_stale(t_now=120.0) is False
        wd.note_accepted(t_now=130.0)
        assert wd.is_stale(t_now=161.0) is True
```

- [ ] **Step 2: Run; confirm fail.**

- [ ] **Step 3: Implement `core/stale.py`.**

```python
"""Stale-frame watchdog timer. Time is injected by the caller for
testability; in production use time.monotonic().

Per spec §4 "Stale-frame watchdog": tick is gated on having ever
accepted a guide image, and resets on watch-dir change and on each
new frame_number boundary so the inevitable warm-up delay of ~2K reads
on a new target does not falsely trip the alert.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StaleFrameWatchdog:
    timeout_s: float = 30.0
    _ever_accepted: bool = False
    _last_tick: float | None = None

    def note_accepted(self, t_now: float) -> None:
        self._ever_accepted = True
        self._last_tick = t_now

    def note_frame_boundary(self, t_now: float) -> None:
        # Don't change _ever_accepted; just reset the timer.
        self._last_tick = t_now

    def note_watch_dir_changed(self, t_now: float) -> None:
        self._ever_accepted = False
        self._last_tick = t_now

    def is_stale(self, t_now: float) -> bool:
        if not self._ever_accepted:
            return False
        if self._last_tick is None:
            return False
        return (t_now - self._last_tick) > self.timeout_s
```

- [ ] **Step 4: Run; confirm green.**

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/stale.py tests/unit/test_stale.py
git commit -m "core: stale-frame watchdog with injected clock"
```

### Task 4.5: End-of-chunk verification

- [ ] **Step 1: Run full suite.**

`make test` — all unit tests across the eight existing test files plus the four new ones (quality, sanity, target_switch, stale) pass.

- [ ] **Step 2: Lint.**

`make lint` — clean.

- [ ] **Step 3: Push and confirm CI.**

```bash
git log --oneline -10
git push
gh run watch
```

End of Chunk 4. Working state: every run-time monitor implemented and
tested in isolation. The autoguider can now detect outliers, sequence
violations, target switches, and stale-frame conditions — but not yet
glued into a `MeasurementRow` pipeline.

---

## Chunk 5: Reducer and persistence

**Goal:** Land the per-SUTR orchestrator (`reducer.py`) that wires
together the framebuffer + sky + xcor + signal_snr pipeline, plus the
SQLite store (`store.py`). After this chunk, every SUTR can produce a
`MeasurementRow` and persist it to disk; the worker thread (Chunk 6) is
the only thing missing for end-to-end operation.

### Task 5.1: MeasurementRow + Reducer

**Files:**
- Modify: `henrietta_guider/core/types.py` (add `MeasurementRow` dataclass)
- Create: `henrietta_guider/core/reducer.py`
- Create: `tests/unit/test_reducer.py`

`MeasurementRow` is a frozen dataclass holding everything one SUTR
contributes for one stamp. Because not every SUTR produces a guide
image (warmup of the K-window buffer takes 2K reads on a fresh frame),
the xcor-related fields are `Optional[float]` and `signal_snr` is
always present.

`Reducer` owns `FrameBuffer`, `SanityChecker`, the reset-read for the
current frame, and detector parameters (gain). Given a new
`(frame_number, sutr_number, raw_read)` plus a `Stamp` + `Template`,
it returns a `MeasurementRow` (or signals discard via the SanityVerdict).

- [ ] **Step 1: Append `MeasurementRow` to `core/types.py`.**

```python
@dataclass(frozen=True)
class MeasurementRow:
    """One row per (frame_number, sutr_number, stamp_id) in stamp_measurements.

    xcor_* and trace_* fields are None when the K-window framebuffer
    has not yet warmed up on the current frame (no guide image). The
    signal_snr is always computed (it only needs the cumulative
    raw_read - reset_read per pixel).
    """

    frame_number: int
    sutr_number: int
    stamp_id: int                       # 0 = science, 1+ = comparison

    # Always populated:
    signal_snr: float | None            # None if zero unmasked pixels

    # Populated only when the framebuffer emits a guide image:
    dx_px: float | None
    dy_px: float | None
    xcor_peak_value: float | None
    xcor_curvature_x: float | None
    xcor_curvature_y: float | None
    trace_fwhm_x_px: float | None
    trace_flux_adu: float | None
    sky_background_adu: float | None

    # Provenance:
    stamp_x_center: int
    stamp_x_halfwidth: int
    stamp_y_lo: int
    stamp_y_hi: int
    template_frame_number: int | None   # which henNNNN.fits built the template
    quality_flags: tuple[str, ...] = ()
```

- [ ] **Step 2: Failing tests in `tests/unit/test_reducer.py`.**

```python
import numpy as np
import pytest

from henrietta_guider.core.reducer import Reducer
from henrietta_guider.core.template import Template
from henrietta_guider.core.types import Stamp


def _full_frame(value: float, ny: int = 2048, nx: int = 2048) -> np.ndarray:
    return np.full((ny, nx), value, dtype=np.float32)


def _stamp() -> Stamp:
    return Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)


def _template(stamp: Stamp, frame_number: int = 1) -> Template:
    img = np.zeros(stamp.shape, dtype=np.float32)
    # Add a Gaussian trace down the middle so xcor has something to find.
    sigma = 1.5
    x_c = stamp.shape[1] // 2
    x = np.arange(stamp.shape[1])[None, :]
    img += 1000.0 * np.exp(-((x - x_c) ** 2) / (2 * sigma**2))
    good = np.ones(stamp.shape, dtype=bool)
    return Template(image=img, good=good, frame_number=frame_number, stamp=stamp)


@pytest.mark.unit
class TestReducer:
    def _make(self, K: int = 1, stride: int = 1) -> Reducer:
        good_full = np.ones((2048, 2048), dtype=bool)
        return Reducer(K=K, stride=stride, gain_e_per_dn=4.0, bpm_good=good_full)

    def test_first_sutr_no_guide_image_signal_snr_is_none(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # Frame 10, SUTR 1 (the reset read itself): no guide image yet,
        # and signal_DN = read - reset = 0 -> total_e <= 0 -> NULL per
        # spec §4.
        rows = red.reduce_sutr(
            frame_number=10, sutr_number=1, raw_read=_full_frame(50.0),
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.dx_px is None  # no guide image yet
        assert row.signal_snr is None

    def test_second_sutr_emits_guide_image_and_xcor(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # SUTR 1: reset.
        red.reduce_sutr(
            frame_number=10, sutr_number=1, raw_read=_full_frame(50.0),
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        # SUTR 2: a slightly different read; FrameBuffer (K=1) emits.
        rows = red.reduce_sutr(
            frame_number=10, sutr_number=2,
            raw_read=_full_frame(50.0) + 1.0,    # +1 DN added everywhere
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        row = rows[0]
        assert row.dx_px is not None
        assert row.dy_px is not None
        assert row.xcor_peak_value is not None
        # Signal snr: signal_DN = (50+1) - 50 = 1 per pixel; in unmasked
        # stamp (51 × 1380 = 70380 px), total e- = 70380 * 1 * 4 = 281520;
        # snr = sqrt(281520) ≈ 530.
        assert row.signal_snr == pytest.approx(530.0, rel=0.05)

    def test_frame_boundary_resets_reset_read_and_buffer(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # Two reads on frame 10:
        red.reduce_sutr(10, 1, _full_frame(50.0),
                        stamps_and_templates=[(stamp, tmpl, 0)])
        red.reduce_sutr(10, 2, _full_frame(60.0),
                        stamps_and_templates=[(stamp, tmpl, 0)])
        # New frame 11, SUTR 1 — buffer cleared, no guide image.
        rows = red.reduce_sutr(11, 1, _full_frame(80.0),
                               stamps_and_templates=[(stamp, tmpl, 0)])
        assert rows[0].dx_px is None
        # signal_snr is relative to frame 11's reset (80.0 itself):
        # total_e <= 0 -> NULL per spec §4.
        assert rows[0].signal_snr is None

    def test_sanity_discard_returns_empty(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        red.reduce_sutr(10, 1, _full_frame(50.0),
                        stamps_and_templates=[(stamp, tmpl, 0)])
        red.reduce_sutr(10, 2, _full_frame(50.0),
                        stamps_and_templates=[(stamp, tmpl, 0)])
        # Out-of-order SUTR: must return [] (discarded).
        rows = red.reduce_sutr(10, 1, _full_frame(50.0),
                               stamps_and_templates=[(stamp, tmpl, 0)])
        assert rows == []

    def test_two_stamps_yields_two_rows(self):
        red = self._make()
        sci_stamp = Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)
        cmp_stamp = Stamp(x_center=400, x_halfwidth=25, y_lo=600, y_hi=1980)
        sci_tmpl  = _template(sci_stamp)
        cmp_tmpl  = _template(cmp_stamp)
        red.reduce_sutr(10, 1, _full_frame(50.0),
                        stamps_and_templates=[(sci_stamp, sci_tmpl, 0),
                                              (cmp_stamp, cmp_tmpl, 1)])
        rows = red.reduce_sutr(10, 2, _full_frame(50.0) + 1.0,
                               stamps_and_templates=[(sci_stamp, sci_tmpl, 0),
                                                     (cmp_stamp, cmp_tmpl, 1)])
        assert len(rows) == 2
        ids = {r.stamp_id for r in rows}
        assert ids == {0, 1}
```

- [ ] **Step 3: Run; confirm fail.**

- [ ] **Step 4: Implement `core/reducer.py`.**

```python
"""Per-SUTR orchestrator.

Reducer.reduce_sutr() takes one new raw read plus a list of
(Stamp, Template, stamp_id) tuples and produces one MeasurementRow per
stamp. It owns:

  - SanityChecker  (rejects out-of-order SUTRs / backwards frames)
  - FrameBuffer    (rolling K-window diff buffer)
  - reset_read     (this frame's _001 read; for signal_snr)
  - gain_e_per_dn  (detector gain)
  - bpm_good       (full-detector good-pixel mask)

It does not do anything I/O-related; the worker thread reads the FITS,
calls reduce_sutr(), and persists the resulting rows.
"""

from __future__ import annotations

import numpy as np

from .framebuffer import FrameBuffer
from .sanity import SanityAction, SanityChecker
from .sky import subtract_local_sky
from .template import Template
from .types import MeasurementRow, Stamp
from .xcor import xcor_2d


class Reducer:
    def __init__(
        self,
        K: int,
        stride: int,
        gain_e_per_dn: float,
        bpm_good: np.ndarray,
        xcor_search: int = 12,
    ) -> None:
        self.framebuffer = FrameBuffer(K=K, stride=stride)
        self.sanity = SanityChecker()
        self.gain_e_per_dn = gain_e_per_dn
        self.bpm_good = bpm_good
        self.xcor_search = xcor_search
        self._reset_read: np.ndarray | None = None
        self._reset_read_frame: int | None = None
        self._warned: set[str] = set()

    def reduce_sutr(
        self,
        frame_number: int,
        sutr_number: int,
        raw_read: np.ndarray,
        stamps_and_templates: list[tuple[Stamp, Template, int]],
    ) -> list[MeasurementRow]:
        verdict = self.sanity.check(frame_number, sutr_number)
        if verdict.action is SanityAction.WARN_DISCARD:
            return []

        # On a new frame, capture this read as the reset.
        if self._reset_read_frame != frame_number:
            self._reset_read = raw_read.copy()
            self._reset_read_frame = frame_number

        # K-window difference (None if buffer not warm yet).
        guide_image = self.framebuffer.add(frame_number, sutr_number, raw_read)

        rows: list[MeasurementRow] = []
        for stamp, template, stamp_id in stamps_and_templates:
            rows.append(self._reduce_one_stamp(
                frame_number, sutr_number, stamp_id, raw_read, guide_image,
                stamp, template, verdict.tags,
            ))
        return rows

    # ---- internal --------------------------------------------------------

    def _reduce_one_stamp(
        self,
        frame: int, sutr: int, stamp_id: int,
        raw_read: np.ndarray,
        guide_image: np.ndarray | None,
        stamp: Stamp,
        template: Template,
        sanity_tags: tuple[str, ...],
    ) -> MeasurementRow:
        good_stamp = self.bpm_good[
            stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max,
        ]

        # signal_snr (always computed; relative to current frame's reset).
        snr = self._signal_snr(raw_read, stamp, good_stamp)

        # If no guide image yet, return early with xcor/trace fields None.
        if guide_image is None:
            return MeasurementRow(
                frame_number=frame, sutr_number=sutr, stamp_id=stamp_id,
                signal_snr=snr,
                dx_px=None, dy_px=None,
                xcor_peak_value=None, xcor_curvature_x=None, xcor_curvature_y=None,
                trace_fwhm_x_px=None, trace_flux_adu=None, sky_background_adu=None,
                stamp_x_center=stamp.x_center, stamp_x_halfwidth=stamp.x_halfwidth,
                stamp_y_lo=stamp.y_lo, stamp_y_hi=stamp.y_hi,
                template_frame_number=template.frame_number,
                quality_flags=sanity_tags,
            )

        # Sky-subtract the guide-image stamp.
        gi_stamp = guide_image[
            stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max,
        ]
        sub, per_row_sky = subtract_local_sky(gi_stamp, good_stamp)
        sub = np.where(good_stamp, sub, 0.0)

        # 2-D xcor against the template.
        xc = xcor_2d(sub, template.image, search=self.xcor_search)

        # Trace summary stats.
        flux = float(np.sum(np.where(good_stamp, sub, 0.0)))
        sky_bg = float(np.median(per_row_sky))
        fwhm = self._trace_fwhm(sub)

        return MeasurementRow(
            frame_number=frame, sutr_number=sutr, stamp_id=stamp_id,
            signal_snr=snr,
            dx_px=xc.dx_px, dy_px=xc.dy_px,
            xcor_peak_value=xc.peak_value,
            xcor_curvature_x=xc.curvature_x,
            xcor_curvature_y=xc.curvature_y,
            trace_fwhm_x_px=fwhm,
            trace_flux_adu=flux,
            sky_background_adu=sky_bg,
            stamp_x_center=stamp.x_center, stamp_x_halfwidth=stamp.x_halfwidth,
            stamp_y_lo=stamp.y_lo, stamp_y_hi=stamp.y_hi,
            template_frame_number=template.frame_number,
            quality_flags=sanity_tags,
        )

    def _signal_snr(
        self,
        raw_read: np.ndarray,
        stamp: Stamp,
        good_stamp: np.ndarray,
    ) -> float | None:
        """Per spec §4: NULL on (reset-read itself, zero unmasked pixels,
        or any path where total_e ≤ 0). NULL is signaled by returning
        None; the store maps None to SQL NULL. A WARNING is logged on
        the first occurrence per session per cause (not per frame) so
        the operator notices a misconfigured stamp without log spam.
        """
        if self._reset_read is None:
            return None
        if not good_stamp.any():
            self._warn_once("signal_snr: zero unmasked pixels in stamp")
            return None
        sig_DN = (
            raw_read[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max]
            - self._reset_read[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max]
        )
        sig_e = float(np.sum(np.where(good_stamp, sig_DN, 0.0))) * self.gain_e_per_dn
        if sig_e <= 0.0:
            self._warn_once("signal_snr: total_e <= 0 (sub-reset read)")
            return None
        return float(np.sqrt(sig_e))

    def _warn_once(self, message: str) -> None:
        if message in self._warned:
            return
        import logging
        logging.getLogger(__name__).warning(message)
        self._warned.add(message)

    def _trace_fwhm(self, sub: np.ndarray) -> float:
        # Collapse along Y to a 1-D spatial profile, take FWHM from the
        # second moment. v1 only: spec §4 specifies a 1-D Gaussian fit;
        # promote this to scipy.optimize.curve_fit if the second-moment
        # estimate is found insufficient during commissioning.
        profile = sub.sum(axis=0)
        if profile.sum() <= 0:
            return float("nan")
        x = np.arange(profile.size)
        x_mean = float(np.sum(x * profile) / np.sum(profile))
        x_var  = float(np.sum((x - x_mean) ** 2 * profile) / np.sum(profile))
        if x_var <= 0:
            return float("nan")
        sigma = np.sqrt(x_var)
        return float(2.355 * sigma)
```

- [ ] **Step 5: Run; confirm green.**

- [ ] **Step 6: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/reducer.py henrietta_guider/core/types.py tests/unit/test_reducer.py
git commit -m "core: per-SUTR reducer + MeasurementRow type"
```

### Task 5.2: SQLite store

**Files:**
- Create: `henrietta_guider/core/store.py`
- Create: `tests/unit/test_store.py`

WAL-mode SQLite database with the two-table schema from spec §7.
`Store.open(path)` creates the file + schema if missing, opens in WAL,
and returns a context-manager-friendly object. `Store.write_frame(...)`
inserts one row into `frames` plus one row per stamp into
`stamp_measurements`. Writes are not transactional across frames (one
commit per call) — crash recovery may lose the last few rows, which is
acceptable per spec §7.

- [ ] **Step 1: Failing tests.**

```python
import json
import sqlite3
from pathlib import Path

import pytest

from henrietta_guider.core.store import FrameRecord, Store
from henrietta_guider.core.types import MeasurementRow


@pytest.mark.unit
class TestStore:
    def _row(self, frame: int = 10, sutr: int = 5, stamp_id: int = 0,
             dx: float | None = 0.05) -> MeasurementRow:
        return MeasurementRow(
            frame_number=frame, sutr_number=sutr, stamp_id=stamp_id,
            signal_snr=210.0, dx_px=dx, dy_px=-0.02,
            xcor_peak_value=1.23e6, xcor_curvature_x=-1500.0,
            xcor_curvature_y=-1700.0, trace_fwhm_x_px=3.4,
            trace_flux_adu=1.84e5, sky_background_adu=62.1,
            stamp_x_center=110, stamp_x_halfwidth=25,
            stamp_y_lo=600, stamp_y_hi=1980,
            template_frame_number=42, quality_flags=("frame_skip",),
        )

    def _frame(self, frame: int = 10, sutr: int = 5) -> FrameRecord:
        return FrameRecord(
            frame_number=frame, sutr_number=sutr,
            timestamp_utc="2026-04-30T08:14:22.137",
            frame_path=f"/data/2026-04-30/hen{frame:04d}_{sutr:03d}r.fits",
            ramp_complete=False,
            ha_hours=1.234, dec_deg=-29.501, pa_deg=45.0, airmass=1.18,
            temperature_c=-50.0, focus_position=12345.0,
            cmd_ra_arcsec=0.0, cmd_dec_arcsec=0.05,
            cmd_suppressed_by=None,
            err_ra_arcsec=0.018, err_dec_arcsec=0.052,
            guiding_state="GUIDING",
        )

    def test_creates_schema_in_new_file(self, tmp_path: Path):
        db = tmp_path / "test.db"
        with Store.open(db) as st:
            pass
        # Schema should exist.
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
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
                frame_number=42, sutr_number=23,
                timestamp_utc="2026-04-30T08:14:22.137",
                frame_path="/x/hen0042.fits",
                ramp_complete=True,                      # the slope-fit final
                ha_hours=None, dec_deg=None, pa_deg=None, airmass=None,
                temperature_c=None, focus_position=None,
                cmd_ra_arcsec=None, cmd_dec_arcsec=None,
                cmd_suppressed_by="alerted",
                err_ra_arcsec=None, err_dec_arcsec=None,
                guiding_state="ALERTED",
            )
            st.write_frame(frame, [])
        with sqlite3.connect(db) as conn:
            (rc,) = conn.execute(
                "SELECT ramp_complete FROM frames WHERE frame_number=42"
            ).fetchone()
            assert rc == 1  # bool True -> int 1

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
            indexes = {r[0] for r in st._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
        assert "idx_frames_time" in indexes
        assert "idx_frames_hadec" in indexes
```

- [ ] **Step 2: Run; confirm fail.**

- [ ] **Step 3: Implement `core/store.py`.**

```python
"""SQLite store for the per-frame measurement archive.

Two tables (spec §7):
  - frames               one row per (frame_number, sutr_number)
  - stamp_measurements   one row per (frame_number, sutr_number, stamp_id)

WAL mode so reads (analysis tools) don't block writes. One commit per
write_frame() call; no explicit transaction across frames.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .types import MeasurementRow


_SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    frame_number       INTEGER NOT NULL,
    sutr_number        INTEGER NOT NULL,
    timestamp_utc      TEXT,
    frame_path         TEXT,
    ramp_complete      INTEGER,
    ha_hours           REAL,
    dec_deg            REAL,
    pa_deg             REAL,
    airmass            REAL,
    temperature_c      REAL,
    focus_position     REAL,
    cmd_ra_arcsec      REAL,
    cmd_dec_arcsec     REAL,
    cmd_suppressed_by  TEXT,
    err_ra_arcsec      REAL,
    err_dec_arcsec     REAL,
    guiding_state      TEXT,
    PRIMARY KEY (frame_number, sutr_number)
);

CREATE TABLE IF NOT EXISTS stamp_measurements (
    frame_number          INTEGER NOT NULL,
    sutr_number           INTEGER NOT NULL,
    stamp_id              INTEGER NOT NULL,
    stamp_x_center        INTEGER,
    stamp_x_halfwidth     INTEGER,
    stamp_y_lo            INTEGER,
    stamp_y_hi            INTEGER,
    template_frame_number INTEGER,
    dx_px                 REAL,
    dy_px                 REAL,
    xcor_peak_value       REAL,
    xcor_curvature_x      REAL,
    xcor_curvature_y      REAL,
    trace_fwhm_x_px       REAL,
    trace_flux_adu        REAL,
    sky_background_adu    REAL,
    signal_snr            REAL,
    quality_flags         TEXT,
    PRIMARY KEY (frame_number, sutr_number, stamp_id),
    FOREIGN KEY (frame_number, sutr_number)
        REFERENCES frames(frame_number, sutr_number)
);

CREATE INDEX IF NOT EXISTS idx_frames_time  ON frames(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_frames_hadec ON frames(ha_hours, dec_deg);
"""


@dataclass(frozen=True)
class FrameRecord:
    """One row's worth of frame-level metadata (the science box's command,
    error, and per-frame state). Mirrors the `frames` table."""
    frame_number: int
    sutr_number: int
    timestamp_utc: str
    frame_path: str
    ramp_complete: bool
    ha_hours: float | None
    dec_deg: float | None
    pa_deg: float | None
    airmass: float | None
    temperature_c: float | None
    focus_position: float | None
    cmd_ra_arcsec: float | None
    cmd_dec_arcsec: float | None
    cmd_suppressed_by: str | None
    err_ra_arcsec: float | None
    err_dec_arcsec: float | None
    guiding_state: str


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    @contextmanager
    def open(cls, path: str | Path):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(_SCHEMA)
            conn.commit()
            yield cls(conn)
        finally:
            conn.close()

    def write_frame(self, frame: FrameRecord, rows: list[MeasurementRow]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO frames
               (frame_number, sutr_number, timestamp_utc, frame_path,
                ramp_complete, ha_hours, dec_deg, pa_deg, airmass,
                temperature_c, focus_position,
                cmd_ra_arcsec, cmd_dec_arcsec, cmd_suppressed_by,
                err_ra_arcsec, err_dec_arcsec, guiding_state)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                frame.frame_number, frame.sutr_number, frame.timestamp_utc,
                frame.frame_path, int(frame.ramp_complete),
                frame.ha_hours, frame.dec_deg, frame.pa_deg, frame.airmass,
                frame.temperature_c, frame.focus_position,
                frame.cmd_ra_arcsec, frame.cmd_dec_arcsec,
                frame.cmd_suppressed_by, frame.err_ra_arcsec,
                frame.err_dec_arcsec, frame.guiding_state,
            ),
        )
        for row in rows:
            self._conn.execute(
                """INSERT OR REPLACE INTO stamp_measurements
                   (frame_number, sutr_number, stamp_id,
                    stamp_x_center, stamp_x_halfwidth, stamp_y_lo, stamp_y_hi,
                    template_frame_number, dx_px, dy_px,
                    xcor_peak_value, xcor_curvature_x, xcor_curvature_y,
                    trace_fwhm_x_px, trace_flux_adu, sky_background_adu,
                    signal_snr, quality_flags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.frame_number, row.sutr_number, row.stamp_id,
                    row.stamp_x_center, row.stamp_x_halfwidth,
                    row.stamp_y_lo, row.stamp_y_hi,
                    row.template_frame_number, row.dx_px, row.dy_px,
                    row.xcor_peak_value, row.xcor_curvature_x,
                    row.xcor_curvature_y, row.trace_fwhm_x_px,
                    row.trace_flux_adu, row.sky_background_adu,
                    row.signal_snr,
                    json.dumps(list(row.quality_flags)),
                ),
            )
        self._conn.commit()
```

- [ ] **Step 4: Run; confirm green.**

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff format . && uv run ruff check .
git add henrietta_guider/core/store.py tests/unit/test_store.py
git commit -m "core: SQLite store (frames + stamp_measurements, WAL)"
```

### Task 5.3: End-of-chunk verification

- [ ] **Step 1: Run full suite.**

`make test` — all tests across `core/` (now: wire, tcs_client, geometry, controller, types, config, bpm, framebuffer, sky, xcor, template, quality, sanity, target_switch, stale, reducer, store) pass.

- [ ] **Step 2: Lint.**

`make lint` — clean.

- [ ] **Step 3: Push and confirm CI.**

```bash
git log --oneline -10
git push
gh run watch
```

End of Chunk 5. Working state: every per-SUTR computation produces a
`MeasurementRow` and every batch persists to SQLite. The autoguider can
now run a complete reduction pipeline against synthetic frames and
verify the database contents — but file I/O (watchdog), the TCS-talking
worker thread, and the GUI are still ahead.

---
