from pathlib import Path

import pytest

from app.data.repair import review
from app.data.repair.review import export_sample, export_stratified, read_decisions


@pytest.fixture(autouse=True)
def _review_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)


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


def test_default_review_dir_is_the_gitignored_repo_data_review():
    root = Path(__file__).resolve().parents[2]
    assert Path(review.__file__).resolve().parents[4] / "data" / "review" == root / "data" / "review"
    assert "data/review/" in (root / ".gitignore").read_text().splitlines()


def test_writer_refuses_paths_outside_the_review_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review")
    rows = [{"accident_id": 1}]
    for bad in (tmp_path / "elsewhere.csv", tmp_path / "review" / ".." / "escape.csv"):
        with pytest.raises(ValueError, match="review CSVs go under"):
            export_sample(rows, n=1, seed=1, path=bad, columns=["accident_id"], decision_column="agree")
    assert export_sample(rows, n=1, seed=1, path=tmp_path / "review" / "sub" / "ok.csv",
                         columns=["accident_id"], decision_column="agree") == 1


@pytest.mark.parametrize("columns,decision", [(["accident_id", "description"], "agree"), (["accident_id"], "Description")])
def test_writer_refuses_the_description_column(tmp_path, columns, decision):
    with pytest.raises(ValueError, match="description"):
        export_sample([{"accident_id": 1, "description": "x"}], n=1, seed=1, path=tmp_path / "d.csv",
                      columns=columns, decision_column=decision)


def test_formula_like_cells_are_escaped(tmp_path):
    path = tmp_path / "f.csv"
    columns = ["accident_id", "mountain", "route", "state", "tags", "longitude", "latitude", "elevation", "location"]
    rows = [{"accident_id": 1, "mountain": "=HYPERLINK(1)", "route": "+cmd", "state": "@x", "tags": "-2+3",
             "longitude": -105.5, "latitude": "+3", "elevation": "-12", "location": "a-b"}]
    export_sample(rows, n=1, seed=1, path=path, columns=columns, decision_column="agree")
    assert path.read_text().splitlines()[1] == "1,'=HYPERLINK(1),'+cmd,'@x,'-2+3,-105.5,+3,-12,a-b,"


def test_read_decisions_rejects_duplicate_and_non_numeric_keys(tmp_path):
    path = tmp_path / "k.csv"
    path.write_text("accident_id,agree\n1,yes\n1,no\n")
    with pytest.raises(ValueError, match="k.csv row 2: duplicate accident_id"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\nabc,no\n")
    with pytest.raises(ValueError, match="k.csv row 2: invalid accident_id"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\n²,no\n")
    with pytest.raises(ValueError, match="k.csv row 2: invalid accident_id"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
