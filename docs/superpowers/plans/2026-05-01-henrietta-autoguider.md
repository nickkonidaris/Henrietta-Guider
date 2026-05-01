# Henrietta Autoguider Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Henrietta autoguider per `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`: watch SUTR FITS frames produced by the Archon, measure trace shifts via 2-D cross-correlation against a slope-fit template, send small (≤2.45″) telescope offsets to the TCS over TCP, and surface the live state in a Tk operator GUI. SQLite archive of every measurement for retrospective analysis of non-periodic mount errors.

**Architecture:** Single Python 3.14 package `henrietta_guider`. `core/` is GUI-free (no Tk imports anywhere in this subtree); `cli/` and `gui/` are thin frontends over `core/`. Concurrency: Tk on main thread; one worker thread runs the watcher + reduction + control + TCS sender + SQLite writer end-to-end; thread-safe `queue.Queue` carries `MeasurementRow` events back to the main thread. The "Estimate K" Monte Carlo runs on its own short-lived worker thread so it doesn't block live guiding.

**Tech Stack:** Python 3.14 (regular GIL build), uv (env + interpreter + lockfile management), Tk + ttk + matplotlib (GUI), watchdog (file events), astropy (FITS), numpy / scipy, stdlib `sqlite3` / `dataclasses` / `tomllib` / `logging`. Dev: pytest, ruff. CI: GitHub Actions.

**TDD discipline:** Every behavioural unit gets a failing test first, then minimal code, then passing test, then commit. The few exceptions (project-scaffolding tasks with no logic to test) are called out in the steps.

---

## Pre-flight

Before starting any task:

- [ ] Confirm the spec is final and committed:
  `docs/superpowers/specs/2026-04-30-henrietta-autoguider-design.md`
- [ ] Confirm sample SUTR data exists (gitignored):
  `test/hen1764_*.fits` (24 files), `test/hen1765_*.fits` (24 files)
- [ ] Confirm the BPM is present at the repo root:
  `bpm_25apr2026.fits` (28 MB; gitignored via `bpm_*.fits` pattern)
- [ ] Confirm `Wireformat.md`, `ALGORITHM.md`, `Questions-for-William.md` are committed at the repo root.

If any are missing, stop and reconcile with the user before proceeding.

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

- [ ] **Step 5: Run ruff.**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean exit (no findings).

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

```markdown
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
```

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
