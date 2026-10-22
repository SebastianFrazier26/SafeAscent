import re
from pathlib import Path

SRC = Path(__file__).parents[1] / "app" / "tasks" / "safety_computation_optimized.py"


def test_purge_keeps_the_phase3_backtest_window():
    match = re.search(r"DELETE FROM historical_predictions(.*?)\"\"\"", SRC.read_text(), re.S)
    assert match is not None
    assert "NOT (prediction_date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02')" in match.group(1)
