import math

import httpx
import pytest

from app.pipelines.dem import Elevation
from app.pipelines.epqs import EpqsClient, compare, failure_guard, local_slopes, slope_class


def _client(handler):
    return EpqsClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_parses_string_value_and_treats_sentinel_as_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        x = float(request.url.params["x"])
        return httpx.Response(200, json={"value": "-1000000" if x > 0 else "1650.25", "location": {"x": x}})

    client = _client(handler)
    assert client.elevation(40.0, -105.3) == 1650.25
    assert client.elevation(40.0, 5.0) is None


def test_retries_transient_failures_then_gives_up_without_a_value():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"value": "1650.0"})

    client = _client(handler)
    assert client.elevation(40.0, -105.3) == 1650.0
    dead = _client(lambda r: httpx.Response(503))
    assert dead.elevation(40.0, -105.3) is None and dead.failed_calls == 1


def test_slope_classes():
    assert [slope_class(d) for d in (0.0, 9.9, 10.0, 29.9, 30.0, 80.0)] == [
        "flat", "flat", "moderate", "moderate", "steep", "steep"]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_slope_class_refuses_a_non_finite_slope(bad):
    with pytest.raises(ValueError, match="finite"):
        slope_class(bad)


def test_local_slope_of_a_30_degree_plane():
    def plane(points):
        return [Elevation(1000.0 + (lat - 40.0) * 110_574.0 * math.tan(math.radians(30.0)), "3dep_13", 10.0)
                for lat, _ in points]

    [s] = local_slopes([(40.0, -105.3)], plane)
    assert s == pytest.approx(30.0, abs=0.5)
    assert local_slopes([(40.0, -105.3)], lambda pts: [None] * len(pts)) == [None]


def _rows(cls, n, diff):
    return [(cls, 1000.0, 1000.0 + diff) for _ in range(n)]


def test_gate_passes_within_class_tolerances():
    result = compare(_rows("flat", 60, 1.0) + _rows("moderate", 60, 3.0) + _rows("steep", 60, 7.0))
    assert (result["compared"], result["gross"], result["passed"]) == (180, 0, 1)


def test_gate_needs_180_points_and_every_class():
    assert compare(_rows("flat", 40, 1.0) + _rows("moderate", 30, 1.0) + _rows("steep", 30, 1.0))["passed"] == 0
    assert compare(_rows("flat", 60, 1.0) + _rows("moderate", 120, 1.0))["passed"] == 0
    steep = _rows("steep", 53, 5.0) + _rows("steep", 7, 25.0)
    assert compare(_rows("flat", 60, 1.0) + _rows("moderate", 60, 1.0) + steep)["passed"] == 0


def test_one_gross_error_fails_and_missing_pairs_are_ignored():
    rows = _rows("flat", 60, 1.0) + _rows("moderate", 60, 1.0) + _rows("steep", 59, 1.0) + [("steep", 1000.0, 1200.0)]
    assert compare(rows)["passed"] == 0
    assert compare(rows[:-1] + [("steep", None, 5.0), ("steep", 1000.0, 1001.0)])["compared"] == 180


def test_unreadable_or_non_finite_epqs_answers_are_missing_not_values():
    bodies = iter([b"<html>busy</html>", b'{"value": "NaN"}', b'["x"]', b'{"value": null}'])
    client = _client(lambda r: httpx.Response(200, content=next(bodies)))
    assert [client.elevation(40.0, -105.3) for _ in range(4)] == [None, None, None, None]
    assert client.failed_calls == 0
    bad = _client(lambda r: httpx.Response(400))
    assert bad.elevation(40.0, -105.3) is None


def test_gate_fails_closed_on_179_valid_pairs_even_with_misses_padding_the_count():
    rows = _rows("flat", 60, 1.0) + _rows("moderate", 60, 1.0) + _rows("steep", 59, 1.0)
    rows += [("steep", 1000.0, None)] * 10 + [("steep", 1000.0, math.nan)]
    result = compare(rows)
    assert (result["compared"], result["passed"]) == (179, 0)
    assert compare(rows + [("steep", 1000.0, 1001.0)])["passed"] == 1


def test_gate_edges_at_the_tolerances():
    at = compare(_rows("flat", 60, 2.0) + _rows("moderate", 60, 4.0) + _rows("steep", 60, 8.0))
    assert at["passed"] == 1
    over = compare(_rows("flat", 60, 2.01) + _rows("moderate", 60, 1.0) + _rows("steep", 60, 1.0))
    assert over["passed"] == 0 and over["classes"]["flat"]["passed"] == 0
    p90 = _rows("moderate", 53, 1.0) + _rows("moderate", 7, 10.5)
    assert compare(_rows("flat", 60, 1.0) + p90 + _rows("steep", 60, 1.0))["passed"] == 0
    thin = compare(_rows("flat", 29, 1.0) + _rows("moderate", 91, 1.0) + _rows("steep", 60, 1.0))
    assert thin["passed"] == 0 and thin["classes"]["flat"] == {"n": 29, "median": None, "p90": None, "passed": 0}


def _with_failures(cls, n, failed):
    return [(cls, 1000.0, None)] * failed + _rows(cls, n - failed, 1.0)


def test_failure_guard_counts_per_class_and_fails_above_20_percent():
    rows = _with_failures("flat", 80, 20) + _with_failures("moderate", 80, 8) + _rows("steep", 80, 1.0)
    counts, problems = failure_guard(rows)
    assert counts == {"flat": {"attempted": 80, "failed": 20, "compared": 60},
                      "moderate": {"attempted": 80, "failed": 8, "compared": 72},
                      "steep": {"attempted": 80, "failed": 0, "compared": 80}}
    assert len(problems) == 1 and "class flat" in problems[0]


def test_failure_guard_passes_at_10_percent_and_at_exactly_20():
    assert failure_guard(_with_failures("flat", 80, 8))[1] == []
    assert failure_guard(_with_failures("flat", 80, 16))[1] == []
    assert failure_guard(_with_failures("flat", 80, 17))[1] != []
    assert failure_guard([("steep", 1000.0, math.nan)])[0]["steep"]["failed"] == 1
