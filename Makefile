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
