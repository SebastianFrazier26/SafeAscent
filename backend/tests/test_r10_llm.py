import json
import uuid
from types import SimpleNamespace

import csv
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.data.repair import r10_llm, review
from app.data.repair.legacy_links import run_r10, run_r10_export
from app.data.repair.r10_llm import (
    AUDIT_SQL, AUTO_LINK_CONFIDENCE, HAIKU_THINKING_BUDGET, MAX_OUTPUT_TOKENS, PAYLOAD_FIELDS, PROMPT_VERSION,
    SAMPLE_COLUMNS, Allocation, Case, api_key_from_environment, apply_picks, build_cases, call_ceiling_usd,
    compute_gate, gate_rows, interpret, ledger_sha, sample_cases, probe_call, record_gate, run_calls, run_lock, sample_rows,
    write_sample_csv,
)
from app.pipelines.mp_catalog import refresh
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_engine, run_sql
from tests.test_legacy_links import SEED, _ns

PIN = "0010_accident_route_links"
OK = json.dumps({"choice": "A", "confidence": 0.95, "reason_code": "name_and_grade_match"})
SENTINEL = "NARRATIVE-SENTINEL"


def test_interpret_maps_letters_and_refuses_anything_else():
    assert interpret("end_turn", OK, 2) == ("ok", 0, False, 0.95, "name_and_grade_match")
    none = json.dumps({"choice": "none", "confidence": 0.8, "reason_code": "no_match"})
    assert interpret("end_turn", none, 2) == ("ok", None, True, 0.8, "no_match")
    beyond = json.dumps({"choice": "E", "confidence": 0.99, "reason_code": "name_match"})
    assert interpret("end_turn", beyond, 2)[0] == "invalid"
    assert interpret("end_turn", json.dumps({"choice": "A", "confidence": 1.7, "reason_code": "name_match"}), 2)[0] == "invalid"
    assert interpret("end_turn", json.dumps({"choice": "Z", "confidence": 0.9, "reason_code": "name_match"}), 2)[0] == "invalid"
    assert interpret("end_turn", "not json", 2)[0] == "invalid"
    assert interpret("refusal", None, 2) == ("refusal", None, None, None, None)
    assert interpret("max_tokens", OK, 2) == ("max_tokens", None, None, None, None)
    assert interpret("pause_turn", OK, 2)[0] == "invalid"


def test_key_only_from_the_exported_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=x\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "exported")
    assert api_key_from_environment(env_dir=tmp_path) == "exported"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(SystemExit, match="export ANTHROPIC_API_KEY"):
        api_key_from_environment(env_dir=tmp_path)
    assert api_key_from_environment({"ANTHROPIC_API_KEY": "k"}, tmp_path) == "k"
    assert api_key_from_environment({"ANTHROPIC_API_KEY": "k"}, tmp_path / "missing") == "k"
    with pytest.raises(SystemExit, match="export ANTHROPIC_API_KEY"):
        api_key_from_environment({}, tmp_path)


@pytest.mark.parametrize("name", [".env", ".env.ingest", ".env.local"])
@pytest.mark.parametrize("line", ["ANTHROPIC_API_KEY=leaked-sentinel", "  export ANTHROPIC_API_KEY = leaked-sentinel"])
def test_key_in_any_backend_env_file_is_refused_without_echoing_it(tmp_path, name, line):
    (tmp_path / ".env").write_text("DATABASE_URL=x\n")
    (tmp_path / name).write_text(f"X=1\n{line}\n")
    with pytest.raises(SystemExit, match=f"must not be in .*{name.replace('.', '[.]')}") as caught:
        api_key_from_environment({"ANTHROPIC_API_KEY": "k"}, tmp_path)
    assert "leaked-sentinel" not in str(caught.value)


def test_gate_needs_enough_checks_and_auto_band_precision():
    good = [("900000012", "900000012", 0.95)] * 40 + [("900000011", "900000011", 0.7)] * 60
    assert compute_gate(good, auto_threshold=0.8)["passed"] is True
    few = good[:90]
    assert compute_gate(few, auto_threshold=0.8)["passed"] is False
    wrong = [("900000012", "none", 0.95)] * 3 + [("900000012", "900000012", 0.95)] * 37 + good[40:]
    gate = compute_gate(wrong, auto_threshold=0.8)
    assert (gate["n_auto_band"], gate["n_auto_band_agree"], gate["passed"]) == (40, 37, False)
    thin_band = [("900000012", "900000012", 0.95)] * 29 + [("900000011", "900000011", 0.7)] * 71
    assert compute_gate(thin_band, auto_threshold=0.8)["passed"] is False
    # 0.80 itself is in the auto band (DP8).
    edge = [("900000012", "900000012", 0.80)] * 30 + [("none", "none", 0.9)] * 70
    assert compute_gate(edge, auto_threshold=AUTO_LINK_CONFIDENCE)["n_auto_band"] == 30
    # The database's band flags, when given, decide membership (M1).
    flags = [False] * 30 + [True] * 70
    assert compute_gate(edge, auto_threshold=AUTO_LINK_CONFIDENCE, auto_flags=flags)["n_auto_band"] == 0


class FakeAllocator:
    model = "claude-haiku-4-5-20251001"

    def __init__(self, answers):
        self.answers = answers
        self.seen: list[Case] = []

    async def allocate(self, case):
        self.seen.append(case)
        status, idx, none, conf, reason = interpret("end_turn", self.answers.get(case.accident_id, OK),
                                                    len(case.candidate_ids))
        return Allocation(status, idx, none, conf, reason, 1000, 200, self.model)


def _prepared(name):
    async def prep(engine):
        async with engine.begin() as conn:
            await refresh(conn, run_id=uuid.uuid4())
            await run_r10(conn, _ns(apply=True))
    run_on_engine(name, prep)


async def _cases(engine):
    async with engine.connect() as conn:
        return await build_cases(conn)


def _gate_row(model="claude-haiku-4-5-20251001", prompt="r10-llm-v1", threshold=0.8, passed=True):
    return ("INSERT INTO internal.r10_llm_gates (gate_id, model, prompt_version, sample_run_id, n_checked, "
            "n_agree, n_auto_band, n_auto_band_agree, auto_threshold, passed) VALUES "
            f"('{uuid.uuid4()}', '{model}', '{prompt}', '{uuid.uuid4()}', 100, 99, 40, 40, {threshold}, {passed})")


@requires_pg
def test_payload_carries_the_narrative_but_no_ids_coordinates_or_exact_date():
    with migrated_db(PIN, seed_sql=SEED) as name:
        run_sql(name, f"UPDATE accidents SET description = '{SENTINEL}', state = 'Colorado', "
                      "date = '2015-07-04', latitude = 40.0, longitude = -105.3, "
                      "source_url = 'https://example.org/fixture' WHERE accident_id = 3")
        _prepared(name)
        built = {c.accident_id: c for c in run_on_engine(name, _cases)}
    assert set(built) == {3, 4, 5}
    payload = json.dumps(built[3].payload)
    assert built[3].payload["narrative"] == SENTINEL
    assert "40.0" not in payload and "2015-07-04" not in payload and "example.org" not in payload
    assert "900000012" not in payload and '"accident_id"' not in payload
    assert set(built[3].payload) == set(PAYLOAD_FIELDS)
    assert [c["label"] for c in built[3].payload["candidates"]] == ["A", "B"]
    assert built[3].payload["when"] == "2015-07"
    assert built[3].source_url == "https://example.org/fixture"


@requires_pg
def test_rows_never_searched_or_decided_or_linked_are_never_sent():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        # Candidates without a recorded search (a hand edit, or a reason with no search) stay unsent.
        run_sql(name, "UPDATE internal.r10_unresolved SET candidates_searched = false WHERE accident_id = 3; "
                      "UPDATE internal.r10_unresolved SET owner_decision = 'no_link', decided_at = now() "
                      "WHERE accident_id = 4")
        assert {c.accident_id for c in run_on_engine(name, _cases)} == {5}


@requires_pg
def test_pick_outside_the_candidates_is_invalid_never_linked():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        bad = json.dumps({"choice": "E", "confidence": 0.99, "reason_code": "name_match"})
        fake = FakeAllocator({3: bad, 4: bad, 5: bad})

        async def go(engine):
            cases = await _cases(engine)
            return await run_calls(engine, fake, cases, run_id=uuid.uuid4(), is_sample=False, max_usd=5.0)
        out = run_on_engine(name, go)
        assert out["status_counts"] == {"invalid": 3}
        rows = fetch(name, "SELECT DISTINCT status, pick_id FROM internal.r10_llm_calls")
        assert rows == [("invalid", None)]


@requires_pg
def test_database_refuses_a_pick_that_was_not_offered():
    with migrated_db(PIN, seed_sql=SEED) as name:
        async def go(engine):
            async with engine.begin() as conn:
                await conn.execute(AUDIT_SQL, {
                    "c": uuid.uuid4(), "run": uuid.uuid4(), "a": 3, "m": "claude-haiku-4-5-20251001", "mr": None,
                    "pv": PROMPT_VERSION, "pf": list(PAYLOAD_FIELDS), "sha": "x", "kind": "route",
                    "ids": [900000011, 900000012], "st": "ok", "pick": 900000099, "none": False, "conf": 0.99,
                    "reason": "name_match", "it": 1, "ot": 1, "cost": 0, "sample": False})
        with pytest.raises(IntegrityError, match="r10_llm_calls_pick_offered_check"):
            run_on_engine(name, go)


class WorstCase(FakeAllocator):
    async def allocate(self, case):
        alloc = await super().allocate(case)
        return Allocation(*[getattr(alloc, f) for f in ("status", "pick_index", "pick_none", "confidence",
                                                         "reason_code")], 1000, MAX_OUTPUT_TOKENS, self.model)


@requires_pg
def test_cost_cap_stops_before_the_call_that_would_pass_it():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        # Each call spends about its own ceiling, so a cap of 1.05 ceilings fits one call, not two.
        fake = WorstCase({})

        async def go(engine):
            cases = await _cases(engine)
            one = call_ceiling_usd(fake.model, cases[0])
            return await run_calls(engine, fake, cases, run_id=uuid.uuid4(), is_sample=False, max_usd=one * 1.05)
        out = run_on_engine(name, go)
        assert out["calls"] == 1 and out["stopped_at_cap"] is True
        ledger = fetch(name, f"SELECT status, rows_in, cost_units FROM source_ingest_log WHERE run_id = '{out['run_id']}'")
        assert [(st, n, float(usd)) for st, n, usd in ledger] == [("ok", 1, pytest.approx(out["spent_usd"], abs=1e-4))]


class Crashing:
    model = "claude-haiku-4-5-20251001"

    async def allocate(self, case):
        raise RuntimeError("connection dropped after the request went out")


@requires_pg
def test_spend_is_on_record_before_the_call_goes_out():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        run_id = uuid.uuid4()

        async def go(engine):
            cases = await _cases(engine)
            try:
                await run_calls(engine, Crashing(), cases, run_id=run_id, is_sample=False, max_usd=5.0)
            except RuntimeError:
                return call_ceiling_usd(Crashing.model, cases[0])
        ceiling = run_on_engine(name, go)
        (status, cost), = fetch(name, f"SELECT status, cost_units FROM source_ingest_log WHERE run_id = '{run_id}'")
        assert status == "failed" and float(cost) == pytest.approx(ceiling, abs=1e-5)
        assert fetch(name, "SELECT count(*) FROM internal.r10_llm_calls") == [(0,)]


@requires_pg
def test_the_narrative_is_never_stored():
    with migrated_db(PIN, seed_sql=SEED) as name:
        run_sql(name, f"UPDATE accidents SET description = '{SENTINEL}' WHERE accident_id IN (3, 4, 5)")
        _prepared(name)
        fake = FakeAllocator({})

        async def go(engine):
            cases = await _cases(engine)
            out = await run_calls(engine, fake, cases, run_id=uuid.uuid4(), is_sample=True, max_usd=5.0)
            return cases, out
        cases, out = run_on_engine(name, go)
        assert {c.payload["narrative"] for c in cases} == {SENTINEL}
        for table in ("internal.r10_llm_calls", "source_ingest_log"):
            dump = fetch(name, f"SELECT t::text FROM {table} t")
            assert dump and not any(SENTINEL in row[0] for row in dump)
        rows = sample_rows(cases, {c.accident_id: (c.candidate_ids[0], False, 0.95) for c in cases},
                           uuid.UUID(out["run_id"]))
        assert rows and not any(SENTINEL in json.dumps(r, default=str) for r in rows)
        assert "narrative" not in SAMPLE_COLUMNS and "description" not in SAMPLE_COLUMNS


@requires_pg
def test_apply_refuses_without_a_passing_gate_for_this_model_and_prompt():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)

        async def go(engine):
            async with engine.begin() as conn:
                return await apply_picks(conn, model="claude-haiku-4-5-20251001", apply=True)
        with pytest.raises(SystemExit, match="no passing gate"):
            run_on_engine(name, go)
        run_sql(name, _gate_row(prompt="r10-llm-v0"))
        run_sql(name, _gate_row(model="claude-sonnet-5"))
        run_sql(name, _gate_row(passed=False))
        run_sql(name, _gate_row(threshold=0.9))
        with pytest.raises(SystemExit, match="no passing gate"):
            run_on_engine(name, go)


async def _run_and_apply(engine, fake, *, gate=True):
    cases = await _cases(engine)
    await run_calls(engine, fake, cases, run_id=uuid.uuid4(), is_sample=False, max_usd=5.0)
    async with engine.begin() as conn:
        if gate:
            await conn.execute(text(
                "INSERT INTO internal.r10_llm_gates (gate_id, model, prompt_version, sample_run_id, n_checked, "
                "n_agree, n_auto_band, n_auto_band_agree, auto_threshold, passed) VALUES (:g, :m, 'r10-llm-v1', "
                ":s, 100, 97, 40, 39, 0.8, true)"), {"g": uuid.uuid4(), "m": fake.model, "s": uuid.uuid4()})
        return await apply_picks(conn, model=fake.model, apply=True)


@requires_pg
def test_apply_links_the_auto_band_prefills_the_review_band_and_owner_wins():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        mid = json.dumps({"choice": "A", "confidence": 0.7, "reason_code": "name_match"})
        low = json.dumps({"choice": "A", "confidence": 0.3, "reason_code": "location_only"})
        fake = FakeAllocator({3: OK, 4: mid, 5: low})
        out = run_on_engine(name, lambda e: _run_and_apply(e, fake))
        assert out["linked"] == 1 and out["review_prefilled"] == 1 and out["left_unresolved"] == 1
        links = fetch(name, "SELECT accident_id, route_id, method FROM accident_route_links "
                                                  "WHERE method = 'llm'")
        assert links == [(3, 900000012, "llm")]
        # m14: the R10 rule version, not the prompt version (that lives on the call row).
        assert fetch(name, "SELECT r10_version FROM accident_legacy_links WHERE accident_id = 3") == [("r10-mp-v1",)]
        marked = fetch(name, "SELECT accident_id FROM internal.r10_unresolved WHERE llm_call_id IS NOT NULL ORDER BY 1")
        assert marked == [(3,), (4,)]
        run_sql(name, "UPDATE internal.r10_unresolved SET owner_decision = 'no_link', decided_at = now() "
                      "WHERE accident_id = 3")

        async def rerun(engine):
            async with engine.begin() as conn:
                await run_r10(conn, _ns(apply=True))
        run_on_engine(name, rerun)
        assert fetch(name, "SELECT count(*) FROM accident_legacy_links WHERE accident_id = 3") == [(0,)]


@requires_pg
def test_auto_link_starts_at_exactly_080():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        at = json.dumps({"choice": "A", "confidence": 0.80, "reason_code": "name_match"})
        under = json.dumps({"choice": "A", "confidence": 0.79, "reason_code": "name_match"})
        review_edge = json.dumps({"choice": "A", "confidence": 0.60, "reason_code": "name_match"})
        fake = FakeAllocator({3: at, 4: under, 5: review_edge})
        out = run_on_engine(name, lambda e: _run_and_apply(e, fake))
        assert (out["linked"], out["review_prefilled"], out["left_unresolved"]) == (1, 2, 0)
        assert fetch(name, "SELECT accident_id FROM accident_legacy_links WHERE method = 'llm'") == [(3,)]


@requires_pg
def test_a_call_over_a_changed_candidate_set_is_never_applied():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        fake = FakeAllocator({})

        async def go(engine):
            cases = await _cases(engine)
            await run_calls(engine, fake, cases, run_id=uuid.uuid4(), is_sample=False, max_usd=5.0)
        run_on_engine(name, go)
        # As if r10 re-ranked accident 3 after the call (Task 14 voids llm_call_id the same way).
        run_sql(name, "UPDATE internal.r10_unresolved SET candidate_route_ids = '{900000011}' WHERE accident_id = 3")
        run_sql(name, _gate_row())

        async def apply(engine):
            async with engine.begin() as conn:
                return await apply_picks(conn, model=fake.model, apply=True)
        out = run_on_engine(name, apply)
        assert out["linked"] == 2
        assert fetch(name, "SELECT accident_id FROM accident_legacy_links WHERE method = 'llm' ORDER BY 1") == [(4,), (5,)]


@requires_pg
def test_owner_decided_rows_are_never_touched_by_apply():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        fake = FakeAllocator({})

        async def calls(engine):
            await run_calls(engine, fake, await _cases(engine), run_id=uuid.uuid4(), is_sample=False, max_usd=5.0)
        run_on_engine(name, calls)
        run_sql(name, "UPDATE internal.r10_unresolved SET owner_decision = 'no_link', decided_at = now() "
                      "WHERE accident_id = 4")
        run_sql(name, _gate_row())

        async def apply(engine):
            async with engine.begin() as conn:
                return await apply_picks(conn, model=fake.model, apply=True)
        assert run_on_engine(name, apply)["linked"] == 2
        assert fetch(name, "SELECT llm_call_id FROM internal.r10_unresolved WHERE accident_id = 4") == [(None,)]
        assert fetch(name, "SELECT count(*) FROM accident_legacy_links WHERE accident_id = 4") == [(0,)]


@requires_pg
def test_gate_reads_the_models_answers_from_the_audit_not_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        # 5 answers at exactly 0.80: the auto band's edge, compared in SQL as REAL (M1).
        fake = FakeAllocator({4: json.dumps({"choice": "none", "confidence": 0.9, "reason_code": "no_match"}),
                              5: json.dumps({"choice": "A", "confidence": 0.80, "reason_code": "name_match"})})
        run_id = uuid.uuid4()

        async def go(engine):
            cases = await _cases(engine)
            await run_calls(engine, fake, cases, run_id=run_id, is_sample=True, max_usd=5.0)
            return cases
        cases = run_on_engine(name, go)
        offered = {c.accident_id: c.candidate_ids for c in cases}
        sheet = tmp_path / "sample.csv"
        # The owner disagrees on 3, and an edited llm_answer column must not count.
        sheet.write_text("accident_id,run_id,llm_answer,owner_answer\n"
                         f"3,{run_id},{offered[3][1]},{offered[3][1]}\n4,{run_id},none,none\n"
                         f"5,{run_id},{offered[5][0]},{offered[5][0]}\n", encoding="utf-8")

        async def gate(engine):
            async with engine.begin() as conn:
                rid, rows, flags = await gate_rows(conn, sheet, model=fake.model)
                return await record_gate(conn, model=fake.model, run_id=rid, rows=rows, auto_flags=flags)
        out = run_on_engine(name, gate)
        assert (out["n_checked"], out["n_agree"], out["n_auto_band"], out["n_auto_band_agree"]) == (3, 2, 2, 1)
        assert out["passed"] is False
        assert fetch(name, "SELECT passed, auto_threshold FROM internal.r10_llm_gates") == [(False, pytest.approx(0.8))]
        # A gate the gate step itself recorded opens apply: auto_threshold is REAL, 0.8 is not exact.
        passing = [("900000012", "900000012", 0.95)] * 40 + [("none", "none", 0.9)] * 60

        async def passing_gate(engine):
            async with engine.begin() as conn:
                await record_gate(conn, model=fake.model, run_id=run_id, rows=passing)
                return await apply_picks(conn, model=fake.model, apply=True)
        assert run_on_engine(name, passing_gate)["linked"] == 2

        # The latest gate decides (M2): a later failing gate revokes the pass.
        async def revoked(engine):
            async with engine.begin() as conn:
                await record_gate(conn, model=fake.model, run_id=run_id, rows=passing[:50])
                return await apply_picks(conn, model=fake.model, apply=True)
        with pytest.raises(SystemExit, match="no passing gate"):
            run_on_engine(name, revoked)
        # Every ok call of the run must be in the file (M3).
        sheet.write_text(f"accident_id,run_id,owner_answer\n3,{run_id},none\n4,{run_id},none\n", encoding="utf-8")
        with pytest.raises(ValueError, match="1 ok calls of run .* missing"):
            run_on_engine(name, gate)
        sheet.write_text(f"accident_id,run_id,owner_answer\n3,{run_id},900000099\n", encoding="utf-8")
        with pytest.raises(ValueError, match="offered ids"):
            run_on_engine(name, gate)
        sheet.write_text(f"accident_id,run_id,owner_answer\n3,{run_id},\n", encoding="utf-8")
        with pytest.raises(ValueError, match="offered ids"):
            run_on_engine(name, gate)


@requires_pg
def test_r10_export_shows_the_prefilled_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        mid = json.dumps({"choice": "A", "confidence": 0.7, "reason_code": "name_match"})
        fake = FakeAllocator({3: mid, 4: mid, 5: mid})
        run_on_engine(name, lambda e: _run_and_apply(e, fake))

        async def export(engine):
            async with engine.begin() as conn:
                await run_r10_export(conn, _ns(out=str(tmp_path / "r10.csv")))
        run_on_engine(name, export)
        with (tmp_path / "r10.csv").open(newline="") as fh:
            exported = {int(r["accident_id"]): r for r in csv.DictReader(fh) if r["llm_pick"]}
        assert {a: (r["llm_pick"], float(r["llm_confidence"])) for a, r in exported.items()} == {
            a: (r["candidate_1_id"], pytest.approx(0.7)) for a, r in exported.items()}
        assert set(exported) == {3, 4, 5}


def test_claude_allocator_request_shape_with_a_mocked_client():
    pytest.importorskip("anthropic")
    import asyncio

    from app.data.repair.r10_llm import ClaudeAllocator

    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", text=None), SimpleNamespace(type="text", text=OK)],
            stop_reason="end_turn", usage=SimpleNamespace(input_tokens=900, output_tokens=300), model=kwargs["model"])

    case = Case(3, "route", (900000012, 900000011), {"narrative": "Fixture fall", "candidates": [
        {"label": "A", "name": "Fixture Dihedral"}, {"label": "B", "name": "Fixture Arete"}]})
    results = []
    for model in ("claude-haiku-4-5-20251001", "claude-sonnet-5"):
        alloc = ClaudeAllocator("test-key-not-real", model)
        alloc._client = SimpleNamespace(messages=SimpleNamespace(create=create))
        results.append(asyncio.run(alloc.allocate(case)))
    haiku, sonnet = sent
    assert haiku["thinking"] == {"type": "enabled", "budget_tokens": HAIKU_THINKING_BUDGET}
    assert haiku["max_tokens"] == MAX_OUTPUT_TOKENS > HAIKU_THINKING_BUDGET
    assert sonnet["thinking"] == {"type": "adaptive"} and sonnet["output_config"]["effort"] == "high"
    assert all(s["output_config"]["format"]["type"] == "json_schema" for s in sent)
    assert "900000012" not in json.dumps(haiku["messages"])
    assert results[0] == Allocation("ok", 0, False, 0.95, "name_and_grade_match", 900, 300,
                                    "claude-haiku-4-5-20251001")
    with pytest.raises(SystemExit, match="not pinned"):
        ClaudeAllocator("test-key-not-real", "claude-unpinned")

    # I2: the SDK never retries on its own; one attempt is one audited, capped call.
    fresh = ClaudeAllocator("test-key-not-real", "claude-haiku-4-5-20251001")
    assert fresh._client.max_retries == 0 and fresh._client.timeout == 600.0

    import anthropic
    import httpx2

    async def times_out(**kwargs):
        raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.invalid/v1/messages"))

    async def other_model(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=OK)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=900, output_tokens=300), model="claude-other")

    fresh._client = SimpleNamespace(messages=SimpleNamespace(create=times_out))
    assert asyncio.run(fresh.allocate(case)) == Allocation("error", None, None, None, None, None, None, None,
                                                            "APITimeoutError")
    fresh._client = SimpleNamespace(messages=SimpleNamespace(create=other_model))
    assert asyncio.run(fresh.allocate(case)) == Allocation("error", None, None, None, None, 900, 300, "claude-other",
                                                            "ModelMismatch")



async def _calls(engine, allocator, *, max_usd=5.0, is_sample=False, run_id=None):
    return await run_calls(engine, allocator, await _cases(engine), run_id=run_id or uuid.uuid4(),
                           is_sample=is_sample, max_usd=max_usd)


@requires_pg
def test_the_cap_is_cumulative_across_runs_for_a_model_and_prompt():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        fake = WorstCase({})

        async def go(engine):
            cases = await _cases(engine)
            cap = call_ceiling_usd(fake.model, cases[0]) * 1.5
            first = await run_calls(engine, fake, cases[:1], run_id=uuid.uuid4(), is_sample=False, max_usd=cap)
            second = await run_calls(engine, fake, cases[1:], run_id=uuid.uuid4(), is_sample=False, max_usd=cap)
            return first, second
        first, second = run_on_engine(name, go)
        assert first["calls"] == 1 and first["stopped_at_cap"] is False
        assert second["calls"] == 0 and second["stopped_at_cap"] is True
        assert second["spent_before_usd"] == pytest.approx(first["spent_usd"], abs=1e-4)
        # Failed and running rows count too: a run that died after its request went out was still billed.
        run_sql(name, "INSERT INTO source_ingest_log (run_id, source, status, content_sha256, cost_units) VALUES "
                      f"('{uuid.uuid4()}', 'r10_llm', 'failed', '{ledger_sha(fake.model)}', 99)")
        assert run_on_engine(name, lambda e: _calls(e, FakeAllocator({}), max_usd=99.0))["calls"] == 0
        # Another model's spend is its own.
        sonnet = FakeAllocator({})
        sonnet.model = "claude-sonnet-5"
        assert run_on_engine(name, lambda e: _calls(e, sonnet, max_usd=5.0))["calls"] == 3


@requires_pg
def test_a_second_run_refuses_while_another_holds_the_lock():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)

        async def go(engine):
            async with run_lock(engine):
                with pytest.raises(SystemExit, match="holds the spend lock"):
                    await _calls(engine, FakeAllocator({}))
            return await _calls(engine, FakeAllocator({}))
        assert run_on_engine(name, go)["calls"] == 3
        assert fetch(name, "SELECT count(*) FROM source_ingest_log WHERE source = 'r10_llm'") == [(1,)]


class Failing(FakeAllocator):
    async def allocate(self, case):
        self.seen.append(case)
        return Allocation("error", None, None, None, None, None, None, None, "APITimeoutError")


@requires_pg
def test_a_case_is_attempted_at_most_twice():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        fake = Failing({})

        async def go(engine):
            return [await _calls(engine, fake) for _ in range(3)]
        first, second, third = run_on_engine(name, go)
        assert first["error_counts"] == {"APITimeoutError": 3} and second["calls"] == 3
        assert third["calls"] == 0 and third["skipped_attempt_limit"] == 3 and len(fake.seen) == 6
        assert fetch(name, "SELECT max(n) FROM (SELECT count(*) AS n FROM internal.r10_llm_calls "
                           "GROUP BY accident_id) t") == [(2,)]


class WrongModel(FakeAllocator):
    async def allocate(self, case):
        self.seen.append(case)
        return Allocation("error", None, None, None, None, 1000, 200, "claude-haiku-4-5-other", "ModelMismatch")


@requires_pg
def test_a_reply_from_another_model_is_audited_then_stops_the_run():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        with pytest.raises(SystemExit, match="not the pinned"):
            run_on_engine(name, lambda e: _calls(e, WrongModel({})))
        assert fetch(name, "SELECT status, model_reported FROM internal.r10_llm_calls") == [
            ("error", "claude-haiku-4-5-other")]
        assert fetch(name, "SELECT status FROM source_ingest_log WHERE source = 'r10_llm'") == [("failed",)]


@requires_pg
def test_apply_counts_a_link_only_when_it_was_inserted(monkeypatch):
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        fake = FakeAllocator({})
        run_on_engine(name, lambda e: _calls(e, fake))
        run_sql(name, _gate_row())
        # As if accident 3 got linked between apply's read and its insert: drop the read's own
        # already-linked filter so the insert meets the conflict.
        monkeypatch.setattr(r10_llm, "LATEST_SQL", text(r10_llm.LATEST_SQL.text.replace(
            "AND NOT EXISTS (SELECT 1 FROM accident_legacy_links l WHERE l.accident_id = c.accident_id)", "")))
        run_sql(name, "INSERT INTO accident_legacy_links (accident_id, route_id, method, r10_version) "
                      "VALUES (3, 900000012, 'owner', 'r10-mp-v1')")

        async def apply(engine):
            async with engine.begin() as conn:
                return await apply_picks(conn, model=fake.model, apply=True)
        assert run_on_engine(name, apply)["linked"] == 2
        assert fetch(name, "SELECT method FROM accident_legacy_links WHERE accident_id = 3") == [("owner",)]


@requires_pg
def test_sample_csv_is_rebuilt_from_the_audit_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    with migrated_db(PIN, seed_sql=SEED) as name:
        run_sql(name, f"UPDATE accidents SET description = '{SENTINEL}' WHERE accident_id IN (3, 4, 5)")
        _prepared(name)
        run_id = uuid.uuid4()
        run_on_engine(name, lambda e: _calls(e, FakeAllocator({}), is_sample=True, run_id=run_id))

        async def rebuild(engine):
            async with engine.connect() as conn:
                return await write_sample_csv(conn, run_id, tmp_path / "again.csv")
        assert run_on_engine(name, rebuild) == 3
        body = (tmp_path / "again.csv").read_text()
        assert SENTINEL not in body
        with (tmp_path / "again.csv").open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert {(r["accident_id"], r["llm_answer"]) for r in rows} == {
            (r["accident_id"], r["candidate_1_id"]) for r in rows}
        run_sql(name, "UPDATE internal.r10_unresolved SET candidate_route_ids = '{900000011}' WHERE accident_id = 3")
        (tmp_path / "again.csv").unlink()
        with pytest.raises(SystemExit, match="cannot be spot-checked"):
            run_on_engine(name, rebuild)


@requires_pg
def test_probe_spends_into_the_ledger_without_an_audit_row():
    with migrated_db(PIN, seed_sql=SEED) as name:
        out = run_on_engine(name, lambda e: probe_call(e, FakeAllocator({}), max_usd=1.0))
        assert out["status"] == "ok" and out["pick_index"] == 0 and out["cost_usd"] <= out["ceiling_usd"]
        assert fetch(name, "SELECT count(*) FROM internal.r10_llm_calls") == [(0,)]
        (usd,), = fetch(name, "SELECT cost_units FROM source_ingest_log WHERE source = 'r10_llm' AND status = 'ok'")
        assert float(usd) == pytest.approx(out["cost_usd"], abs=1e-6)
        with pytest.raises(SystemExit, match="would pass the cap"):
            run_on_engine(name, lambda e: probe_call(e, FakeAllocator({}), max_usd=out["cost_usd"]))


@pytest.mark.parametrize("argv", [["sample", "--model", "haiku", "--n", "1", "--max-usd", "12.01", "--out", "x.csv"],
                                  ["run", "--model", "sonnet", "--max-usd", "22.5"],
                                  ["probe", "--model", "haiku", "--max-usd", "-1"]])
def test_max_usd_above_the_owners_cap_is_refused_before_anything_runs(argv, monkeypatch):
    import asyncio

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="owner's cap"):
        asyncio.run(r10_llm._main(r10_llm.build_parser().parse_args(argv)))


@requires_pg
def test_sample_never_repicks_a_case_already_called_for_the_model():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)

        async def go(engine):
            cases = await _cases(engine)
            await run_calls(engine, FakeAllocator({}), cases[:2], run_id=uuid.uuid4(), is_sample=True, max_usd=5.0)
            async with engine.connect() as conn:
                haiku = await sample_cases(conn, cases, model=FakeAllocator.model, n=10, seed=1)
                sonnet = await sample_cases(conn, cases, model="claude-sonnet-5", n=10, seed=1)
            return [c.accident_id for c in haiku], {c.accident_id for c in sonnet}, [c.accident_id for c in cases]
        haiku, sonnet, all_ids = run_on_engine(name, go)
        assert haiku == all_ids[2:] and sonnet == set(all_ids)


@requires_pg
def test_bands_compare_as_real_even_where_real_rounds_down(monkeypatch):
    # float32(0.7) = 0.69999999: a threshold compared as double would drop a 0.7 answer out of its band.
    monkeypatch.setattr(r10_llm, "AUTO_LINK_CONFIDENCE", 0.7)
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepared(name)
        at = json.dumps({"choice": "A", "confidence": 0.7, "reason_code": "name_match"})
        fake = FakeAllocator({3: at, 4: at, 5: at})
        run_on_engine(name, lambda e: _calls(e, fake))
        run_sql(name, _gate_row(threshold=0.7))

        async def apply(engine):
            async with engine.begin() as conn:
                return await apply_picks(conn, model=fake.model, apply=True)
        assert run_on_engine(name, apply)["linked"] == 3
