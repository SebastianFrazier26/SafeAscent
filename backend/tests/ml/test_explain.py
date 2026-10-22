import math

import numpy as np
import pytest

from app.ml.bundle import ModelBundle
from app.ml.explain import EVIDENCE, EXPOSURE_NAMES, LABELS, OTHER, TYPE_SEASON, explain
from app.ml.fixtures import bundle_reference_terms, toy_bundle_and_row


def test_terms_reconstruct_the_stored_rr_and_name_no_exposure():
    bundle, row = toy_bundle_and_row()
    terms = explain(bundle, **row)
    assert len(terms) == 5 and terms[-1].name == EVIDENCE and terms[-2].name == OTHER
    assert not any(t.name in EXPOSURE_NAMES for t in terms)
    full = explain(bundle, **row, top=None)
    assert sum(t.log_effect for t in full) == pytest.approx(math.log(row["rr_mean"]), abs=1e-6)
    assert all("safe" not in t.label.lower() for t in full)
    ref = bundle_reference_terms(bundle, row)
    hazard = sum(t.log_effect for t in full if t.name != EVIDENCE)
    assert hazard == pytest.approx(ref.log_hazard_over_rref, abs=1e-6)
    assert full[-1].log_effect == pytest.approx(ref.log_theta_over_mean, abs=1e-6)


@pytest.mark.parametrize("top", [0, 1, 3, 5, 100])
def test_truncated_terms_still_sum_to_the_stored_rr(top):
    bundle, row = toy_bundle_and_row()
    terms = explain(bundle, **row, top=top)
    assert sum(t.log_effect for t in terms) == pytest.approx(math.log(row["rr_mean"]), abs=1e-6)
    full = explain(bundle, **row, top=None)
    assert [t.name for t in terms if t.name not in (OTHER, EVIDENCE)] == [t.name for t in full if t.name != EVIDENCE][:top]
    assert (OTHER in [t.name for t in terms]) == (top < len(full) - 1)


def test_other_factors_label_is_plain():
    assert LABELS[OTHER] == "Other factors" and "safe" not in LABELS[OTHER].lower()


def test_type_season_is_near_zero_for_a_typical_cell():
    # Hand-built bundle: the national typical trad route in July has hazard exp(intercept + a + m) = r_ref
    # (posterior mean 1), so the relative type/season term is 0 and the covariates carry the rest.
    names = ["intercept", "fam_mountain", "a_trad", "m_7", "d_trad_7", "z_elevation_m", "ind_elevation_missing"]
    coef = np.array([math.log(0.05), 0.0, 0.2, 0.1, 0.0, 0.3, 0.4])
    r_ref = math.exp(coef[0] + coef[2] + coef[3])
    const = {"trad": {"7": -math.log(r_ref)}}
    bundle = ModelBundle("hand", "rock_family",
                         {"coef": coef, "center": np.zeros(7), "scale": np.ones(7)},
                         {"names": names, "const": const})
    terms = {t.name: t for t in explain(bundle, type_group="trad", month=7, raw_features={"elevation_m": None},
                                        rr_mean=1.0, n_eff_geo=1.0, n_eff_feature=1.0, top=None)}
    assert terms[TYPE_SEASON].log_effect == pytest.approx(0.0, abs=1e-12)
    assert terms["ind_elevation_missing"].log_effect == pytest.approx(0.4)
    assert sum(t.log_effect for t in terms.values()) == pytest.approx(0.0, abs=1e-12)


def test_type_season_folds_in_the_reference_constant():
    bundle, row = toy_bundle_and_row()
    ts = {t.name: t for t in explain(bundle, **row, top=None)}[TYPE_SEASON]
    names, coef = bundle.manifest["names"], bundle.arrays["coef"]
    raw_ts = sum(float(coef[names.index(n)]) for n in ("intercept", "a_trad", "m_7", "d_trad_7"))
    assert ts.log_effect == pytest.approx(raw_ts + bundle.manifest["const"]["trad"]["7"])


def test_top_three_are_the_largest_by_magnitude():
    bundle, row = toy_bundle_and_row()
    full = [t for t in explain(bundle, **row, top=None) if t.name != EVIDENCE]
    shown = [t for t in explain(bundle, **row) if t.name not in (EVIDENCE, OTHER)]
    assert [t.name for t in shown] == [t.name for t in full[:3]]
    assert [abs(t.log_effect) for t in full] == sorted((abs(t.log_effect) for t in full), reverse=True)


def test_unknown_design_column_names_the_column():
    bundle, row = toy_bundle_and_row()
    bad = ModelBundle("x", "rock_family", dict(bundle.arrays),
                      {**bundle.manifest, "names": [*bundle.manifest["names"][:-1], "z_mystery"]})
    with pytest.raises(ValueError, match="z_mystery"):
        explain(bad, **row)


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
