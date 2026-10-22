import math

import numpy as np
import pytest

from app.ml.bundle import ModelBundle
from app.ml.explain import EVIDENCE, EXPOSURE_NAMES, LABELS, explain
from app.ml.fixtures import bundle_reference_terms, toy_bundle_and_row


def test_terms_reconstruct_the_stored_rr_and_name_no_exposure():
    bundle, row = toy_bundle_and_row()
    terms = explain(bundle, **row)
    assert len(terms) == 4 and terms[-1].name == EVIDENCE
    assert not any(t.name in EXPOSURE_NAMES for t in terms)
    const = bundle.manifest["const"][row["type_group"]][str(row["month"])]
    full = explain(bundle, **row, top=None)
    assert sum(t.log_effect for t in full) + const == pytest.approx(math.log(row["rr_mean"]), abs=1e-6)
    assert all("safe" not in t.label.lower() for t in full)
    ref = bundle_reference_terms(bundle, row)
    hazard = sum(t.log_effect for t in full if t.name != EVIDENCE)
    assert hazard + const == pytest.approx(ref.log_hazard_over_rref, abs=1e-6)
    assert full[-1].log_effect == pytest.approx(ref.log_theta_over_mean, abs=1e-6)


def test_top_three_are_the_largest_by_magnitude():
    bundle, row = toy_bundle_and_row()
    full = [t for t in explain(bundle, **row, top=None) if t.name != EVIDENCE]
    shown = [t for t in explain(bundle, **row) if t.name != EVIDENCE]
    assert [t.name for t in shown] == [t.name for t in full[:3]]
    assert [abs(t.log_effect) for t in full] == sorted((abs(t.log_effect) for t in full), reverse=True)


def test_missing_feature_is_explained_by_its_indicator_not_a_zero_value():
    bundle, row = toy_bundle_and_row()
    row = {**row, "raw_features": {**row["raw_features"], "elevation_m": None, "lat": None}}
    full = {t.name: t for t in explain(bundle, **row, top=None)}
    assert full["z_elevation_m"].log_effect == 0.0
    assert full["ind_elevation_missing"].log_effect == pytest.approx(0.3)
    assert full["ind_elevation_missing"].label == "Elevation not on file"
    present = {t.name: t for t in explain(bundle, **toy_bundle_and_row()[1], top=None)}
    assert present["ind_elevation_missing"].log_effect == 0.0


def test_labels_cover_every_term_and_are_plain():
    bundle, row = toy_bundle_and_row()
    for t in explain(bundle, **row, top=None):
        assert t.label == LABELS[t.name]
    assert LABELS["z_elevation_m"] == "Elevation"
    assert LABELS["z_freeze_thaw_days"] == "Freeze-thaw days this month"


def test_rejects_bad_input():
    bundle, row = toy_bundle_and_row()
    with pytest.raises(ValueError):
        explain(bundle, **{**row, "month": 13})
    with pytest.raises(ValueError):
        explain(bundle, **{**row, "type_group": "nope"})
    with pytest.raises(ValueError):
        explain(bundle, **{**row, "n_eff_geo": float("nan")})
    with pytest.raises(ValueError):
        explain(bundle, **{**row, "raw_features": {"bogus": 1.0}})


def test_survives_bundle_round_trip():
    bundle, row = toy_bundle_and_row()
    again = ModelBundle.from_bytes(bundle.to_bytes())
    assert explain(again, **row, top=None) == explain(bundle, **row, top=None)
    assert np.isfinite([t.log_effect for t in explain(again, **row)]).all()
