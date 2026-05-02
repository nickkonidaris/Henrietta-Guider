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
