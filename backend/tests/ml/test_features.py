import dataclasses

import numpy as np
import pytest

from app.ml.dataset import STATIC_FEATURES
from app.ml.features import Design, TickPolicy, build_design, tick_families, transform_static
from app.ml.fixtures import synthetic_cells


def test_missing_is_indicator_plus_centre_never_raw_zero():
    cells = synthetic_cells(seed=1, n_areas=40)
    cells.season_share[0] = np.nan
    cells.static[0, 0] = np.nan
    d = build_design(cells, policy=TickPolicy())
    col = {n: i for i, n in enumerate(d.names)}
    assert d.X[0, col["ind_season_missing"]] == 1.0 and d.X[0, col["x_season"]] == 0.0
    assert d.X[0, col["ind_elevation_missing"]] == 1.0 and d.X[0, col["z_elevation_m"]] == 0.0
    present = ~np.isnan(cells.static[:, 0])
    assert abs(d.X[present, col["z_elevation_m"]].mean()) < 1e-9


def test_test_fold_reuses_training_standardization():
    train, test = synthetic_cells(seed=1, n_areas=40), synthetic_cells(seed=2, n_areas=10)
    d = build_design(train, policy=TickPolicy())
    t = build_design(test, policy=TickPolicy(), fit_stats=d)
    np.testing.assert_array_equal(t.center, d.center)
    assert t.tick_ref == d.tick_ref


def test_rock_ticks_gated_until_coverage():
    cells = synthetic_cells(seed=3, n_areas=60, rock_tick_coverage=0.5)
    assert tick_families(cells, TickPolicy()) == frozenset({"mountain"})
    d = build_design(cells, policy=TickPolicy())
    off = build_design(cells, policy=TickPolicy(enabled=False))
    col = {n: i for i, n in enumerate(d.names)}
    rock = np.isin(cells.g, [0, 1, 2])
    np.testing.assert_array_equal(d.X[rock][:, col["x_ticks"]], 0.0)
    np.testing.assert_array_equal(d.X[rock][:, col["ind_ticks_off"]], 1.0)
    np.testing.assert_array_equal(off.X[:, col["ind_ticks_off"]], 1.0)
    # pre-flight F25 (Review Focus 3): rock rows equal a run where rock carries no tick data at all
    no_rock_ticks = dataclasses.replace(cells, **{f: np.where(rock, np.nan, getattr(cells, f))
                                                  for f in ("ticks_value", "ticks_per_route", "tick_coverage")})
    d2 = build_design(no_rock_ticks, policy=TickPolicy())
    assert d2.names == d.names
    np.testing.assert_array_equal(d.X[rock], d2.X[rock])


def test_penalty_and_nonneg_layout():
    d = build_design(synthetic_cells(seed=1, n_areas=20), policy=TickPolicy())
    col = {n: i for i, n in enumerate(d.names)}
    assert d.penalty[col["intercept"]] == 0 and d.penalty[col["m_7"]] == 0
    assert d.penalty[col["a_ice"]] == 2.0 and d.penalty[col["d_ice_7"]] == 8.0
    assert d.nonneg == (col["x_density"],)
    assert d.X.shape[1] == len(d.names) == 2 + 6 + 11 + 72 + 5 + 9


def test_full_rock_coverage_turns_rock_ticks_on_and_test_fold_keeps_training_gate():
    train = synthetic_cells(seed=4, n_areas=40)
    assert tick_families(train, TickPolicy()) == frozenset({"mountain", "rock_family"})
    d = build_design(train, policy=TickPolicy())
    assert isinstance(d, Design) and d.families_on == frozenset({"mountain", "rock_family"})
    col = {n: i for i, n in enumerate(d.names)}
    assert (d.X[:, col["ind_ticks_off"]] == 0.0).all()
    test = synthetic_cells(seed=5, n_areas=20, rock_tick_coverage=0.0)
    t = build_design(test, policy=TickPolicy(), fit_stats=d)
    assert t.families_on == d.families_on
    rock = np.isin(test.g, [0, 1, 2])
    # uncovered rock rows in a gated-on fold are imputed, flagged, never a measured zero
    np.testing.assert_array_equal(t.X[rock][:, col["ind_ticks_off"]], 1.0)
    np.testing.assert_array_equal(t.X[rock][:, col["x_ticks"]], 0.0)


def test_measured_zero_season_is_not_missing():
    cells = synthetic_cells(seed=1, n_areas=40)
    cells.season_share[0] = 0.0
    d = build_design(cells, policy=TickPolicy())
    col = {n: i for i, n in enumerate(d.names)}
    assert d.X[0, col["ind_season_missing"]] == 0.0
    assert d.X[0, col["x_season"]] == pytest.approx(-d.center[col["x_season"]] / d.scale[col["x_season"]])


def test_lat_missing_without_elevation_flag_fails_closed():
    cells = synthetic_cells(seed=1, n_areas=10)
    cells.static[0, 1] = np.nan
    with pytest.raises(ValueError, match="lat missing"):
        build_design(cells, policy=TickPolicy())


def test_transform_static_matches_the_design_rows_including_nan_and_log1p():
    cells = synthetic_cells(5, 12)
    st = cells.static.copy()
    st[3, 2:] = np.nan
    st[5] = np.nan
    st[8, 4] = np.nan
    d = build_design(dataclasses.replace(cells, static=st), policy=TickPolicy())
    for i in (0, 3, 5, 8, 11, 20, 40):
        raw = {f: (None if np.isnan(st[i, j]) else float(st[i, j])) for j, f in enumerate(STATIC_FEATURES)}
        for name, v in transform_static(raw).items():
            k = d.names.index(name)
            got = v if name.startswith("ind_") else (0.0 if np.isnan(v) else (v - d.center[k]) / d.scale[k])
            assert got == pytest.approx(d.X[i, k]), (i, name)
    assert {"z_precip_mm", "z_snowfall_cm"} <= set(transform_static({"precip_mm": 3.0, "snowfall_cm": 9.0}))
    assert transform_static({"precip_mm": 3.0})["z_precip_mm"] == pytest.approx(np.log1p(3.0))


def test_transform_static_missing_and_inconsistent_lat():
    t = transform_static({"elevation_m": None, "lat": None})
    assert t["ind_elevation_missing"] == 1.0 and t["ind_normals_missing"] == 1.0
    with pytest.raises(ValueError):
        transform_static({"elevation_m": 100.0, "lat": None})
