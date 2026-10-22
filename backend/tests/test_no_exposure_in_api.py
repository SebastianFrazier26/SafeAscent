from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_endpoint_reads_the_exposure_index():
    assert [str(p) for p in API.rglob("*.py") if "exposure_index" in p.read_text(encoding="utf-8")] == []
