"""The Phase 3 prospective backtest scores against the kernel's stored historical_predictions for
[2026-02-05, 2026-07-03). The kernel nightly that wrote (and purged around) that window is gone;
this keeps anything else from writing or deleting the table."""

import re
from datetime import date
from pathlib import Path

from app.ml.folds import PROSPECTIVE

APP = Path(__file__).parents[1] / "app"
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(\s+TABLE)?)\s+(public\.)?historical_predictions\b",
                   re.I)
# Seeds throwaway test databases with synthetic rows; never imported by a served or scheduled path.
SEED_ONLY = {APP / "ml" / "fixtures.py"}


def test_the_protected_window_is_the_prospective_fold():
    assert PROSPECTIVE == (date(2026, 2, 5), date(2026, 7, 3))


def test_nothing_in_app_writes_or_deletes_historical_predictions():
    offenders = [str(f.relative_to(APP)) for f in sorted(APP.rglob("*.py"))
                 if f not in SEED_ONLY and WRITE.search(f.read_text())]
    assert offenders == []


def test_no_orm_model_maps_historical_predictions():
    offenders = [str(f.relative_to(APP)) for f in sorted((APP / "models").rglob("*.py"))
                 if "historical_predictions" in f.read_text()]
    assert offenders == []
