from datetime import date

import numpy as np
import pytest

from app.ml.dataset import ExposureRow, MissingAreas, StaticRow, assemble_cells, count_incidents, seasonal_profile
from app.ml.incidents import AreaInfo, Placement

AREAS = [AreaInfo(900000110, "CO", 40.0, -105.0, {"trad": 2}), AreaInfo(900000210, "UT", 38.0, -110.0, {"ice": 1})]
PATHS = {900000110: "900000100.900000110", 900000210: "900000200.900000210"}


def _exposure():
    rows = []
    for m in range(1, 13):
        rows.append(ExposureRow(900000110, "trad", m, 2, 0.5 if m != 2 else None, 10.0, 5.0, 1.0))
        rows.append(ExposureRow(900000210, "ice", m, 1, 0.2, None, None, 0.0))
    return rows


def _static():
    return [StaticRow(a, m, (2000.0, 40.0, None if a == 900000210 else 20.0, 5.0, 50.0, 10.0, 3.0))
            for a in (900000110, 900000210) for m in range(1, 13)]


def _p(aid, area, groups, month, lo, hi, level="cell", state="CO", flags=frozenset()):
    reason = "route_link" if level == "cell" else "no_coordinates"
    return Placement(aid, level, reason, area, state, groups, month, lo, hi, 1.0, flags)


def test_missing_stays_nan_never_zero():
    cells = assemble_cells(_exposure(), _static(), AREAS, PATHS)
    i = cells.index[(900000110, 1, 2)]                        # trad, February, season missing
    assert np.isnan(cells.season_share[i]) and cells.ticks_value[i] == 10.0
    j = cells.index[(900000210, 4, 1)]                        # ice: ticks missing, tmax missing
    assert np.isnan(cells.ticks_value[j]) and np.isnan(cells.static[j, 2]) and cells.tick_coverage[j] == 0.0
    assert cells.states == ("CO", "UT") and len(cells.regions) == 2


def test_area_without_static_rows_reads_nan():
    cells = assemble_cells(_exposure(), [s for s in _static() if s.area_id == 900000110], AREAS, PATHS)
    assert np.isnan(cells.static[cells.index[(900000210, 4, 6)]]).all()
    assert np.isnan(cells.area_static[1]).all() and not np.isnan(cells.area_static[0]).any()


def test_unknown_exposure_area_is_a_named_error():
    with pytest.raises(MissingAreas, match="900000210"):
        assemble_cells(_exposure(), _static(), AREAS[:1], PATHS)


def test_counts_respect_window_month_spread_and_flags():
    cells = assemble_cells(_exposure(), _static(), AREAS, PATHS)
    day = _p(1, 900000110, (("trad", 1.0),), 7, date(2010, 7, 14), date(2010, 7, 15))
    year = _p(2, 900000110, (("trad", 1.0),), None, date(2003, 1, 1), date(2006, 1, 1))
    ski = _p(3, 900000210, (("ice", 1.0),), 1, date(2012, 1, 5), date(2012, 1, 6), state="UT",
             flags=frozenset({"ski_approach"}))
    state_only = _p(4, None, (), 8, date(2011, 8, 1), date(2011, 8, 2), level="state")
    profile = np.full((6, 12), 1 / 12)
    c = count_incidents([day, year, ski, state_only], cells, start=date(1990, 1, 1), end=date(2004, 1, 1),
                        profile=profile)
    assert c.y.sum() == pytest.approx(365 / 1096)            # only the year row overlaps, spread evenly
    assert c.y[cells.index[(900000110, 1, 5)]] == pytest.approx(365 / 1096 / 12)
    c2 = count_incidents([day, year, ski, state_only], cells, start=date(2004, 1, 1), end=date(2027, 1, 1),
                         profile=profile, exclude_flags=frozenset({"ski_approach"}))
    assert c2.y[cells.index[(900000110, 1, 7)]] == pytest.approx(1 + 731 / 1096 / 12)
    assert c2.y[cells.index[(900000210, 4, 1)]] == 0.0
    assert c2.state_mass.sum() == pytest.approx(1.0)          # the state-only row, split by state exposure
    assert c2.years == pytest.approx(23.0, abs=0.01)
    assert c2.dropped == {"excluded_flag": 1}


def test_state_only_never_enters_cell_counts():
    cells = assemble_cells(_exposure(), _static(), AREAS, PATHS)
    state_only = _p(4, None, (), 8, date(2011, 8, 1), date(2011, 8, 2), level="state")
    c = count_incidents([state_only], cells, start=date(2004, 1, 1), end=date(2027, 1, 1))
    assert c.y.sum() == 0.0 and c.state_mass[0, 1] == pytest.approx(1.0)


def test_partial_snapshot_is_refused():
    cells = assemble_cells(_exposure()[:-1], _static(), AREAS, PATHS)
    with pytest.raises(ValueError, match="partial exposure snapshot"):
        count_incidents([], cells, start=date(2004, 1, 1), end=date(2027, 1, 1))


def test_seasonal_profile_smooths_and_sums_to_one():
    day = _p(1, 900000110, (("trad", 1.0),), 7, date(2010, 7, 14), date(2010, 7, 15))
    prof = seasonal_profile([day], start=date(1990, 1, 1), end=date(2027, 1, 1))
    assert prof.shape == (6, 12) and np.allclose(prof.sum(axis=1), 1.0)
    assert prof[1, 6] > prof[1, 0] > 0.0                     # trad July above an empty month, never zero
