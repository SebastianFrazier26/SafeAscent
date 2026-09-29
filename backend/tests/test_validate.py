from datetime import date

import pytest

from app.pipelines.validate import (
    ValidationReport,
    batch_gate,
    coord_problem,
    date_problem,
    in_us,
    range_problem,
)

TODAY = date(2026, 9, 28)


@pytest.mark.parametrize(
    "lat,lon",
    [
        (40.0, -105.3),  # Boulder
        (63.07, -151.0),  # Denali
        (19.8, -155.5),  # Mauna Kea
        (24.6, -82.9),  # Dry Tortugas
        (44.8, -66.95),  # Maine, east of Lubec
    ],
)
def test_us_points_are_inside(lat, lon):
    assert in_us(lat, lon)


def test_aleutians_east_of_180_are_inside_the_us():
    assert in_us(52.9, 175.0)


@pytest.mark.parametrize("lat,lon", [(51.0, -115.0), (-33.4, -70.6), (27.99, 86.93), (37.8, 122.0)])
def test_foreign_or_sign_flipped_points_are_outside(lat, lon):
    assert not in_us(lat, lon)


@pytest.mark.parametrize("lat,lon", [(49.25, -123.1), (49.1, -113.9), (32.5, -117.0)])
def test_us_boxes_are_a_coarse_prefilter_that_admits_border_slivers(lat, lon):
    # Southern BC/AB and Tijuana sit inside the boxes. in_us only rejects gross errors
    # (sign flips, other continents); country is decided by plan 2's R3 geocode repair.
    assert in_us(lat, lon)


def test_date_problem_is_relative_to_the_injected_today():
    assert date_problem(TODAY, today=TODAY) is None
    assert date_problem(date(2026, 9, 29), today=TODAY) == "future"
    assert date_problem(date(2026, 9, 29), today=date(2026, 9, 29)) is None
    assert date_problem(None, today=TODAY) == "missing_date"
    assert date_problem(date(1969, 12, 31), today=TODAY, earliest=date(1970, 1, 1)) == "too_early"


def test_coord_problem_reasons():
    assert coord_problem(None, -105.0) == "missing_coords"
    assert coord_problem(float("nan"), -105.0) == "not_finite"
    assert coord_problem(91.0, -105.0) == "out_of_range"
    assert coord_problem(48.0, 2.3) == "outside_us"
    assert coord_problem(40.0, -105.3) is None


def test_range_problem_treats_missing_as_allowed_and_nan_as_bad():
    assert range_problem(None, 0, 75) is None
    assert range_problem(float("inf"), 0, 75) == "not_finite"
    assert range_problem(-0.1, 0, 75) == "out_of_range"
    assert range_problem(75.0, 0, 75) is None


def test_report_counts_and_summary_hold_no_row_payloads():
    report = ValidationReport("fixture")
    report.accept()
    report.quarantine("row-2", "future", value="2027-01-01")
    report.quarantine("row-3", "future", value="3901-01-01")
    assert report.rows_in == 3
    assert report.accepted == 1
    assert report.quarantined_total() == 2
    summary = report.summary()
    assert summary == {"source": "fixture", "rows_in": 3, "accepted": 1, "quarantined": {"future": 2}}


def test_batch_gate_rejects_count_swings_and_heavy_quarantine():
    report = ValidationReport("fixture")
    for _ in range(90):
        report.accept()
    for i in range(10):
        report.quarantine(f"r{i}", "outside_us")
    assert batch_gate(report, previous_rows_in=100, count_tolerance=0.05, max_quarantine_share=0.2) == []
    assert batch_gate(report, previous_rows_in=120, count_tolerance=0.05, max_quarantine_share=0.2) == [
        "rows_in 100 differs from last ok run 120 by more than 5%"
    ]
    assert batch_gate(report, previous_rows_in=None, count_tolerance=0.05, max_quarantine_share=0.05) == [
        "quarantined share 0.100 exceeds 0.050"
    ]


def test_batch_gate_rejects_an_empty_batch():
    assert batch_gate(ValidationReport("x"), previous_rows_in=None, count_tolerance=0.05, max_quarantine_share=1.0) == [
        "no rows"
    ]
