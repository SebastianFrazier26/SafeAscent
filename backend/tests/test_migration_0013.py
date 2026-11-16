import re
from pathlib import Path

import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg
from tests.test_migrations import PRE_WEATHER_DROP, _alembic_cfg

pytestmark = requires_pg
TARGET = "0013_drop_legacy_weather"
WEATHER_ROW = ("INSERT INTO weather (weather_id, date, latitude, longitude, temperature_max) "
               "VALUES (1, '2010-07-09', 40.05, -105.25, 21)")


def _r6(status: str = "ok", metrics: str | None = '{"n": 150, "r_tmax": 0.97, "p99_gust": 42.5}') -> str:
    report = "NULL" if metrics is None else f"'{metrics}'::jsonb"
    return ("INSERT INTO source_ingest_log (run_id, source, status, finished_at, validation_report) VALUES "
            f"(gen_random_uuid(), 'r6_verify', '{status}', now(), {report})")


def _refuses(seed: str) -> None:
    with migrated_db(revision=PRE_WEATHER_DROP, seed_sql=WEATHER_ROW + ";" + seed) as name:
        with pytest.raises(RuntimeError, match="no ok r6_verify run"):
            command.upgrade(_alembic_cfg(name), TARGET)
        assert fetch(name, "SELECT count(*) FROM weather") == [(1,)]
        # The refusal rolls the whole upgrade back: the database is still at 0012, not half-way.
        assert PRE_WEATHER_DROP == "0012_model_registry"
        assert fetch(name, "SELECT version_num FROM alembic_version") == [("0012_model_registry",)]


def test_0013_refuses_non_empty_weather_without_r6_evidence():
    _refuses("SELECT 1")


def test_0013_refuses_when_the_only_r6_run_failed():
    _refuses(_r6(status="failed"))


def test_0013_refuses_an_ok_run_with_no_metrics():
    _refuses(_r6(metrics=None))


@pytest.mark.parametrize("metrics", [
    '{"n": 100, "r_tmax": 0.97, "p99_gust": 42.5}',
    '{"n": 150, "r_tmax": 0.95, "p99_gust": 42.5}',
    '{"n": 150, "r_tmax": 0.97, "p99_gust": 60}',
    '{"n": 150, "r_tmax": 0.97}',
    '{"n": "150", "r_tmax": 0.97, "p99_gust": 42.5}',
], ids=["n-low", "r-low", "gust-high", "gust-missing", "n-string"])
def test_0013_refuses_an_ok_run_failing_the_thresholds(metrics):
    _refuses(_r6(metrics=metrics))


def test_0013_drops_non_empty_weather_with_a_passing_r6_run_and_is_one_way():
    with migrated_db(revision=PRE_WEATHER_DROP, seed_sql=WEATHER_ROW + ";" + _r6()) as name:
        command.upgrade(_alembic_cfg(name), TARGET)
        assert fetch(name, "SELECT to_regclass('public.weather')") == [(None,)]
        assert fetch(name, "SELECT to_regclass('public.weather_weather_id_seq')") == [(None,)]
        with pytest.raises(NotImplementedError, match="pre-p3-4 snapshot branch"):
            command.downgrade(_alembic_cfg(name), PRE_WEATHER_DROP)


def test_0013_drops_an_empty_weather_table_with_no_r6_run():
    with migrated_db(revision=PRE_WEATHER_DROP) as name:
        assert fetch(name, "SELECT count(*) FROM source_ingest_log") == [(0,)]
        command.upgrade(_alembic_cfg(name), TARGET)
        assert fetch(name, "SELECT to_regclass('public.weather')") == [(None,)]


def test_no_code_reads_weather_table():
    app = Path(__file__).parents[1] / "app"
    hits = [p for p in app.rglob("*.py") if re.search(r"\b(FROM|JOIN)\s+weather\b|models\.weather", p.read_text())]
    assert hits == []
