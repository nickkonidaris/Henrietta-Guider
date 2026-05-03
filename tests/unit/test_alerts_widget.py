import pytest


@pytest.mark.unit
class TestAlertBannerImports:
    def test_module_importable(self):
        # The banner module must not pull in core.audio at import time
        # (it's lazily loaded inside show()). This protects the SSH /
        # headless path.
        import sys

        # Clear core.audio if any prior test imported it.
        sys.modules.pop("henrietta_guider.core.audio", None)
        from henrietta_guider.tui.widgets.alerts import AlertBanner  # noqa: F401

        assert "henrietta_guider.core.audio" not in sys.modules
