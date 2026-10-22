from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
# The serving code is app/api and the services it calls; the DB grants (app has no access to
# internal) are the real guard, this only catches a reader being added by mistake.
MARKERS = ("exposure_index", "ExposureIndex", "app.models.exposure", "models import exposure")


@pytest.mark.parametrize("package", ["api", "services"])
def test_no_serving_code_reads_the_exposure_index(package: str):
    root = APP / package
    assert root.is_dir(), root
    hits = [str(p) for p in root.rglob("*.py") if any(m in p.read_text(encoding="utf-8") for m in MARKERS)]
    assert hits == []
