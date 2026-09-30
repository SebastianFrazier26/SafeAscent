from datetime import date

from app.data.repair.dates import r2_precision


def test_aac_day_15_is_month_and_july_1_is_year():
    assert r2_precision("aac", date(2004, 3, 15), 2004) == "month"
    assert r2_precision("aac", date(2004, 7, 1), 2004) == "year"
    assert r2_precision("aac", date(2004, 3, 14), 2004) == "day"


def test_other_sources_keep_day_precision():
    assert r2_precision("nps", date(2004, 7, 1), 2004) == "day"
    assert r2_precision("avalanche", date(2004, 3, 15), 2004) == "day"


def test_year_only_and_missing():
    assert r2_precision("aac", None, 2004.0) == "year"
    assert r2_precision("nps", None, None) == "unknown"
