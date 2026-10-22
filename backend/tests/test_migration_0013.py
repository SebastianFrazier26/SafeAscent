import re
from pathlib import Path

import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
R6_OK = ("INSERT INTO source_ingest_log (run_id, source, status, finished_at) VALUES "
         "('00000000-0000-0000-0000-000000000006', 'r6_verify', 'ok', now())")
R6_FAILED = R6_OK.replace("'ok'", "'failed'")


def test_0013_refuses_without_r6_evidence():
    with migrated_db(revision="0012_model_registry") as name:
        with pytest.raises(RuntimeError, match="no ok r6_verify run"):
            command.upgrade(_alembic_cfg(name), "0013_drop_legacy_weather")
        assert fetch(name, "SELECT to_regclass('public.weather')") != [(None,)]


def test_0013_refuses_when_the_only_r6_run_failed():
    with migrated_db(revision="0012_model_registry", seed_sql=R6_FAILED) as name:
        with pytest.raises(RuntimeError, match="no ok r6_verify run"):
            command.upgrade(_alembic_cfg(name), "0013_drop_legacy_weather")
        assert fetch(name, "SELECT to_regclass('public.weather')") != [(None,)]


def test_0013_drops_weather_and_is_one_way():
    with migrated_db(revision="0012_model_registry", seed_sql=R6_OK) as name:
        command.upgrade(_alembic_cfg(name), "0013_drop_legacy_weather")
        assert fetch(name, "SELECT to_regclass('public.weather')") == [(None,)]
        assert fetch(name, "SELECT to_regclass('public.weather_weather_id_seq')") == [(None,)]
        with pytest.raises(NotImplementedError, match="pre-p3-4 snapshot branch"):
            command.downgrade(_alembic_cfg(name), "0012_model_registry")


def test_no_code_reads_weather_table():
    app = Path(__file__).parents[1] / "app"
    hits = [p for p in app.rglob("*.py") if re.search(r"\b(FROM|JOIN)\s+weather\b|models\.weather", p.read_text())]
    assert hits == []
