from datetime import date
from typing import Any

import pytest

from app.ml.incidents import AreaInfo, AreaLookup, IncidentRow, interval, place, window_fraction
from app.pipelines.geo import haversine_km

AREAS = AreaLookup([
    AreaInfo(900000110, "CO", 40.0, -105.0, {"trad": 3, "sport": 1}),
    AreaInfo(900000120, "CO", 40.5, -105.5, {"ice": 2}),
])


def row(**over: Any) -> IncidentRow:
    base: dict[str, Any] = dict(
        accident_id=900000001, date=date(2010, 7, 14), date_precision="day", year_lo=None, year_hi=None,
        state="Colorado", geocode_precision="exact", lat=40.001, lon=-105.001, inclusion_flag=None,
        link_method=None, link_route_id=None, link_area_id=None, route_area_id=None,
        route_type_group=None, route_scorable=None, weight=1.0,
    )
    base.update(over)
    return IncidentRow(**base)


def test_route_link_places_cell_with_its_group():
    p = place(row(link_method="mp_route_id", link_route_id=900000001, route_area_id=900000110,
                  route_type_group="trad", route_scorable=True), AREAS)
    assert (p.level, p.area_id, p.group_weights, p.month) == ("cell", 900000110, (("trad", 1.0),), 7)


def test_boulder_link_is_dropped():
    p = place(row(link_method="mp_route_id", link_route_id=900000009, route_area_id=900000110,
                  route_type_group=None, route_scorable=False), AREAS)
    assert (p.level, p.reason) == ("dropped", "boulder_route")


def test_unknown_type_route_splits_by_area_routes():
    p = place(row(link_method="owner", link_route_id=900000009, route_area_id=900000110,
                  route_type_group="unknown", route_scorable=False), AREAS)
    assert p.level == "cell" and dict(p.group_weights) == pytest.approx({"trad": 0.75, "sport": 0.25})


def test_area_link_with_no_scorable_route_is_state_not_nearest():
    areas = AreaLookup([*AREAS._by_id.values(), AreaInfo(900000130, "CO", 40.001, -105.001, {"unknown": 4})])
    p = place(row(link_method="owner", link_area_id=900000130), areas)
    assert (p.level, p.area_id, p.group_weights) == ("state", None, ())


def test_point_only_uses_nearest_area_within_3km_else_state():
    near = place(row(), AREAS)
    assert (near.level, near.area_id, near.reason) == ("cell", 900000110, "nearest_area")
    far = place(row(lat=40.05, lon=-105.05), AREAS)  # ~7 km away
    assert (far.level, far.state_code, far.area_id) == ("state", "CO", None)


def test_nearest_at_alaska_latitude():
    areas = AreaLookup([AreaInfo(900000140, "AK", 61.0, -150.0, {"alpine": 1})])
    lat, lon = 61.0, -150.0537
    assert abs(lon - -150.0) > 0.05
    assert haversine_km(lat, lon, 61.0, -150.0) == pytest.approx(2.9, abs=0.01)
    found = areas.nearest(lat, lon, max_km=3)
    assert found is not None and found.area_id == 900000140


def test_park_centroid_and_untrusted_points_are_state_only():
    for precision in ("park_centroid", "region_fallback", "unknown", None):
        p = place(row(geocode_precision=precision), AREAS)
        assert (p.level, p.area_id, p.group_weights) == ("state", None, ())


def test_state_only_never_carries_area_or_groups_but_keeps_its_window():
    p = place(row(geocode_precision="park_centroid"), AREAS)
    assert (p.level, p.reason, p.state_code) == ("state", "state_only", "CO")
    assert p.area_id is None and p.group_weights == ()
    assert window_fraction(p, date(2010, 1, 1), date(2011, 1, 1)) == 1.0


def test_no_state_and_no_date_are_dropped():
    assert place(row(geocode_precision=None, state=None), AREAS).reason == "no_state"
    assert place(row(date=None, date_precision=None), AREAS).reason == "no_date"


def test_unknown_precision_is_never_read_as_a_day():
    for precision in ("unknown", None):
        p = place(row(date_precision=precision), AREAS)
        assert (p.level, p.reason) == ("dropped", "no_date")
        assert window_fraction(p, date(1990, 1, 1), date(2030, 1, 1)) == 0.0


def test_year_interval_straddles_cutoff():
    r = row(date=date(2004, 7, 1), date_precision="year", year_lo=2003, year_hi=2005)
    p = place(r, AREAS)
    assert p.month is None
    assert interval(r) == (date(2003, 1, 1), date(2006, 1, 1))
    assert window_fraction(p, date(1990, 1, 1), date(2004, 1, 1)) == pytest.approx(365 / 1096)
    assert window_fraction(p, date(2004, 1, 1), date(2007, 1, 1)) == pytest.approx(731 / 1096)


def test_year_range_wins_over_a_point_precision():
    r = row(date=date(2004, 3, 15), date_precision="month", year_lo=2003, year_hi=2004)
    assert interval(r) == (date(2003, 1, 1), date(2005, 1, 1))
    assert place(r, AREAS).month is None


def test_year_precision_without_bounds_is_its_own_year():
    assert interval(row(date=date(1999, 7, 1), date_precision="year")) == (date(1999, 1, 1), date(2000, 1, 1))
    assert interval(row(date=None, date_precision="year", year=1999)) == (date(1999, 1, 1), date(2000, 1, 1))


def test_month_precision_is_its_month_and_day_is_its_day():
    m = place(row(date=date(1998, 3, 15), date_precision="month"), AREAS)
    assert m.month == 3 and window_fraction(m, date(1998, 3, 20), date(1999, 1, 1)) == pytest.approx(12 / 31)  # F17: half-open 03-20..04-01
    d = place(row(), AREAS)
    assert window_fraction(d, date(2010, 7, 14), date(2010, 7, 15)) == 1.0
    assert window_fraction(d, date(2010, 7, 15), date(2011, 1, 1)) == 0.0


def test_flags_carry_ski_approach_and_llm_link():
    p = place(row(inclusion_flag="ski_approach_best_effort", link_method="llm", link_route_id=900000009,
                  route_area_id=900000120, route_type_group="ice", route_scorable=True), AREAS)
    assert p.flags == frozenset({"ski_approach", "llm_link"})
