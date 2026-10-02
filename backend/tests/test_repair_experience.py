import argparse
import asyncio
import csv
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import experience, review
from app.data.repair.__main__ import STEPS
from app.data.repair.experience import NOTHING, Experience, extract_experience, parse_number
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_years_of_climbing():
    assert extract_experience("She had 12 years of climbing experience.").years == 12
    assert extract_experience("with five years climbing").years == 5
    assert extract_experience("the 12 year old route").years is None


def test_word_numbers_and_boundaries():
    assert parse_number("twenty-five") == 25 and parse_number("twenty five") == 25 and parse_number("7") == 7
    assert extract_experience("He had twenty-five years of climbing behind him.").years == 25
    assert extract_experience("the club logged 112 years of climbing").years is None
    assert extract_experience("She had been climbing for 8 years.").years == 8


def test_only_climbing_years_count():
    assert extract_experience("He had 20 years of experience as a nurse.").years is None
    assert extract_experience("30 years of ski patrol work").years is None
    assert extract_experience("10 years of ice climbing").years == 10


def test_levels_and_conflicts():
    assert extract_experience("An experienced leader").level == "experienced"
    assert extract_experience("a beginner on his second trip").level == "novice"
    assert extract_experience("an experienced leader with a novice partner").level == "unknown"


def test_first_season_and_guided():
    assert extract_experience("It was her first season of ice climbing.").first_season is True
    assert extract_experience("The client was roped to the guide.").guided == "guided"


def test_negated_forms_do_not_match_the_positive_class():
    e = extract_experience("The inexperienced pair were unguided and went without a guide.")
    assert (e.level, e.guided) == ("novice", "unguided")


def test_relative_and_compound_forms_set_nothing_positive():
    assert extract_experience("the less experienced partner").level == "unknown"
    assert extract_experience("a more experienced climber led").level == "unknown"
    assert extract_experience("a non-expert on ice").level == "unknown"
    assert extract_experience("It was a self-guided trip.").guided == "unguided"
    assert extract_experience("They hired a guided party's rope.").guided == "guided"


def test_nothing_stated_is_unknown_not_a_guess():
    assert extract_experience(None) == Experience(None, "unknown", None, "unknown")
    assert extract_experience("They fell on the descent.") == Experience(None, "unknown", None, "unknown")


# Precision guards beyond the plan's cases: each is a common narrative phrasing that
# states no experience fact, so a match would be a wrong filled value.
@pytest.mark.parametrize(
    "narrative",
    [
        "He experienced a fall of 30 feet.",
        "She experienced severe hypothermia overnight.",
        "Rescuers were experts in high-angle work.",
        "The slope is expert terrain.",
        "They skied the beginner slope.",
        "He was not an experienced climber.",
        "She was not a novice.",
        "the most experienced climber in the group",
    ],
)
def test_verbs_terrain_and_negations_state_no_level(narrative):
    assert extract_experience(narrative).level == "unknown"


@pytest.mark.parametrize(
    ("narrative", "level"),
    [
        ("He was an expert climber.", "expert"),
        ("Both were experienced alpinists.", "experienced"),
        ("The pair were very experienced.", "experienced"),
        ("an experienced ice climber", "experienced"),
        ("She was relatively inexperienced.", "novice"),
    ],
)
def test_stated_levels(narrative, level):
    assert extract_experience(narrative).level == level


@pytest.mark.parametrize(
    "narrative",
    ["It was his first year of college.", "It was not her first season of climbing.", "the first year the route was open"],
)
def test_first_season_needs_climbing_and_no_negation(narrative):
    assert extract_experience(narrative).first_season is None


def test_first_season_as_a_climber():
    assert extract_experience("He was in his first year as a climber.").first_season is True


@pytest.mark.parametrize(
    "narrative",
    [
        "Rescuers guided him to safety.",
        "According to the guide book the route is easy.",
        "The two climbed independently of each other.",
        "A guided party nearby helped the unguided pair.",
    ],
)
def test_guided_needs_a_clear_statement_and_conflicts_are_unknown(narrative):
    assert extract_experience(narrative).guided == "unknown"


@pytest.mark.parametrize(
    "narrative",
    ["They were on a guided climb.", "She climbed with a guide from the service.", "His guide lowered him."],
)
def test_guided_statements(narrative):
    assert extract_experience(narrative).guided == "guided"


def test_independent_party_is_unguided():
    assert extract_experience("They were an independent party.").guided == "unguided"


def test_combined_years_and_conflicting_numbers_are_not_a_person_fact():
    assert extract_experience("The pair had a combined 50 years of climbing.").years is None
    assert extract_experience("They had 40 years of climbing between them.").years is None
    assert extract_experience("He had 10 years of climbing; she had 3 years of climbing.").years is None
    assert extract_experience("He had over 30 years of climbing.").years == 30
    assert extract_experience("He had 90 years of climbing.").years is None


def test_parse_number_rejects_non_numbers():
    assert parse_number("eleven") == 11 and parse_number("ninety-nine") == 99 and parse_number("forty") == 40
    assert parse_number("twenty-ten") is None and parse_number("lots") is None


def test_cli_registers_the_experience_steps():
    assert {"r12", "r12-golden-export"} <= set(STEPS)


# Row 1-3: plain R12 rows. Row 4: a hand-set level with no revision. Row 5: guided last set
# by another rule version (stands in for the manual AAC CSV / Phase 4 self-reports).
# Row 6: loaded after the snapshot. Row 7: NULL narrative.
EXPERIENCE_SEED = """
INSERT INTO accidents (accident_id, source, source_id, date, description) VALUES
  (1, 'AAC', '201', '2010-06-01', 'An experienced leader with 12 years of climbing fell.'),
  (2, 'AAC', '202', '2011-06-01', 'It was her first season of ice climbing. The client was roped to the guide.'),
  (3, 'AAC', '203', '2012-06-01', 'They fell on the descent.'),
  (4, 'AAC', '204', '2013-06-01', 'A beginner on his second trip.'),
  (5, 'AAC', '205', '2014-06-01', 'The inexperienced pair were unguided.'),
  (6, 'AAC', '206', '2015-06-01', 'An experienced leader.'),
  (7, 'AAC', '207', '2016-06-01', NULL);
INSERT INTO internal.accidents_raw (accident_id, source, source_id, date)
  SELECT accident_id, source, source_id, date FROM accidents WHERE accident_id <> 6;
UPDATE accidents SET exp_stated_level = 'expert' WHERE accident_id = 4;
UPDATE accidents SET guided = 'guided' WHERE accident_id = 5;
INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, rule_version, run_id)
  VALUES (5, 'guided', 'unknown', 'guided', 'manual_csv', 'aac-csv-v1', gen_random_uuid());
"""

EXP_SQL = (
    "SELECT accident_id, exp_years_climbing, exp_stated_level, exp_first_season, guided, exp_rule_version "
    "FROM accidents ORDER BY accident_id"
)


async def _all(conn, sql: str) -> list[tuple]:
    return [tuple(r) for r in await conn.execute(text(sql))]


@requires_pg
def test_r12_fills_stated_facts_skips_rows_decided_elsewhere_and_is_idempotent():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                dry = await experience.run_r12(conn, argparse.Namespace(apply=False))
                assert dry["mode"] == "dry_run"
                assert await _all(conn, "SELECT count(*) FROM accidents WHERE exp_rule_version IS NOT NULL") == [(0,)]
            async with engine.begin() as conn:
                first = await experience.run_r12(conn, argparse.Namespace(apply=True))
            async with engine.begin() as conn:
                again = await experience.run_r12(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                got = await _all(conn, EXP_SQL)
                revisions = await _all(
                    conn,
                    "SELECT count(*) FROM internal.accident_revisions WHERE rule_version = 'r12-v1'",
                )
                logged = await _all(
                    conn,
                    "SELECT rows_in, rows_quarantined, validation_report->>'rule_version' FROM source_ingest_log "
                    "WHERE source = 'repair:r12' ORDER BY started_at",
                )
            assert got == [
                (1, 12, "experienced", None, "unknown", "r12-v1"),
                (2, None, "unknown", True, "guided", "r12-v1"),
                (3, None, "unknown", None, "unknown", "r12-v1"),
                (4, None, "expert", None, "unknown", None),
                (5, None, "unknown", None, "guided", None),
                (6, None, "unknown", None, "unknown", None),
                (7, None, "unknown", None, "unknown", "r12-v1"),
            ]
            assert first["skipped"] == {"labelled_elsewhere": 1, "decided_elsewhere": 1, "not_in_snapshot": 1}
            assert first["filled"] == {"exp_first_season": 1, "exp_stated_level": 1, "exp_years_climbing": 1, "guided": 1}
            # Only real differences become changes (G14): row 1 = years, level, version; row 2 =
            # first season, guided, version; rows 3 and 7 = version only.
            assert first["applied"] == 8 and first["unchanged"] == 0 and revisions == [(8,)]
            assert again["applied"] == 0 and again["proposed"] == {}
            assert logged == [(4, 2, "r12-v1"), (4, 2, "r12-v1")]
            assert "description" not in json.dumps(first) and "climbing fell" not in json.dumps(first)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=EXPERIENCE_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r12_never_redecides_a_field_another_writer_set_after_it():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await experience.run_r12(conn, argparse.Namespace(apply=True))
                # The owner corrects row 1's level after R12 ran.
                await conn.execute(text("UPDATE accidents SET exp_stated_level = 'expert' WHERE accident_id = 1"))
                await conn.execute(text(
                    "INSERT INTO internal.accident_revisions "
                    "(accident_id, field, old_value, new_value, method, rule_version, run_id) "
                    "VALUES (1, 'exp_stated_level', 'experienced', 'expert', 'owner_review', 'r12-review-v1', gen_random_uuid())"
                ))
            async with engine.connect() as conn:
                again = await experience.run_r12(conn, argparse.Namespace(apply=False))
                [(level,)] = await _all(conn, "SELECT exp_stated_level FROM accidents WHERE accident_id = 1")
            assert level == "expert"
            assert again["skipped"]["decided_elsewhere"] == 2 and again["proposed"] == {}
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=EXPERIENCE_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_golden_export_hides_the_rule_and_the_narrative(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                out = await experience.run_r12_golden_export(conn, argparse.Namespace(out=None, overwrite=False))
                with pytest.raises(FileExistsError):
                    await experience.run_r12_golden_export(conn, argparse.Namespace(out=None, overwrite=False))
            path = tmp_path / "experience_golden_candidates.csv"
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            assert list(rows[0]) == [
                "accident_id", "source", "source_id", "date",
                "exp_years_climbing", "exp_stated_level", "exp_first_season", "guided",
            ]
            # Rows decided elsewhere (4, 5) and outside the snapshot (6) are not drawn.
            assert sorted(int(r["accident_id"]) for r in rows) == [1, 2, 3, 7]
            assert all(r[c] == "" for r in rows for c in experience.FACT_COLUMNS)
            assert out["strata"] == {"fired": 2, "silent": 2, "_topup": 0}
            assert "climbing fell" not in path.read_text(encoding="utf-8")
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=EXPERIENCE_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


def test_nothing_constant():
    assert NOTHING == Experience(None, "unknown", None, "unknown")


# Review round 1: a guide's credentials are not the party's experience.
@pytest.mark.parametrize(
    "narrative",
    [
        "The guide had 20 years of climbing experience.",
        "Their guide, with 15 years of climbing experience, led the group",
        "The group's guide was an expert climber.",
        "His guide was a very experienced mountaineer.",
    ],
)
def test_a_guides_credentials_fill_no_party_fact(narrative):
    e = extract_experience(narrative)
    assert (e.years, e.level) == (None, "unknown")


def test_the_partys_own_experience_still_counts_in_a_guided_narrative():
    e = extract_experience("Their guide fixed the rope. The client, an experienced climber, had 10 years of climbing.")
    assert (e.years, e.level, e.guided) == (10, "experienced", "guided")


def test_a_guide_dog_is_not_a_guide():
    assert extract_experience("She hiked with a guide dog.").guided == "unknown"
