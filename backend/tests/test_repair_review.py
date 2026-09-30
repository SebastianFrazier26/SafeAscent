import pytest

from app.data.repair.review import export_sample, export_stratified, read_decisions


def test_export_is_seeded_and_adds_a_blank_decision_column(tmp_path):
    rows = [{"accident_id": i, "year": 2000 + i} for i in range(100)]
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    assert export_sample(rows, n=10, seed=7, path=a, columns=["accident_id", "year"], decision_column="agree") == 10
    export_sample(rows, n=10, seed=7, path=b, columns=["accident_id", "year"], decision_column="agree")
    assert a.read_text() == b.read_text()
    assert a.read_text().splitlines()[0] == "accident_id,year,agree"


def test_stratified_export_fills_rare_strata_and_hides_the_stratum(tmp_path):
    rows = [{"accident_id": i, "kind": "rare" if i % 10 == 0 else "common"} for i in range(200)]
    path = tmp_path / "s.csv"
    taken = export_stratified(
        rows, stratum=lambda r: str(r["kind"]), quotas={"rare": 15, "common": 5}, seed=3, path=path,
        columns=["accident_id"], decision_column="label",
    )
    assert taken == {"common": 5, "rare": 15}
    lines = path.read_text().splitlines()
    assert lines[0] == "accident_id,label" and len(lines) == 21
    ids = {int(line.split(",")[0]) for line in lines[1:]}
    assert sum(1 for i in ids if i % 10 == 0) == 15


def test_stratified_export_tops_up_a_short_stratum_from_the_rest(tmp_path):
    rows = [{"accident_id": i, "kind": "rare" if i < 3 else "common"} for i in range(50)]
    taken = export_stratified(
        rows, stratum=lambda r: str(r["kind"]), quotas={"rare": 10, "common": 10}, seed=3, path=tmp_path / "t.csv",
        columns=["accident_id"], decision_column="label",
    )
    assert taken == {"common": 10, "rare": 3, "_topup": 7}
    assert len((tmp_path / "t.csv").read_text().splitlines()) == 21


def test_read_decisions_rejects_blank_and_unknown_values(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("accident_id,agree\n1,yes\n2,\n")
    with pytest.raises(ValueError, match="row 2: blank"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\n2,maybe\n")
    with pytest.raises(ValueError, match="'maybe'"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\n2,NO\n")
    assert read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"})) == {
        1: "yes",
        2: "no",
    }
