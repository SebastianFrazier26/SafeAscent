"""No scoring or API path may depend on the R12 experience columns: they are NULL or
'unknown' for most rows and must never gate a score. Only the repair rules, the model
declaration, the clean-view migration and the R11 facts loader (which writes them as given,
never reads them to score) may name them."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {
    APP / "data" / "repair" / "experience.py", APP / "data" / "repair" / "framework.py", APP / "models" / "accident.py",
    # The R11 loader stores the experience facts a CSV row states. It must never score on
    # them, nor read them to decide anything; a scoring use belongs nowhere in app/.
    APP / "pipelines" / "accident_facts.py",
}
COLUMNS = re.compile(r"\b(?:exp_years_climbing|exp_stated_level|exp_first_season|exp_rule_version)\b")


def test_scoring_path_never_reads_experience_columns():
    offenders = [
        str(p.relative_to(APP)) for p in APP.rglob("*.py")
        if p not in ALLOWED and COLUMNS.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
