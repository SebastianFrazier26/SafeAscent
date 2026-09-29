"""Boundary checks shared by every loader. Bad rows are quarantined with a reason and
counted, never dropped silently; `today` is always injected so "future" moves with the run."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

# (lat_lo, lat_hi, lon_lo, lon_hi). Alaska's Aleutians cross the antimeridian, hence two boxes.
# Deliberately coarse: southern BC/AB and Tijuana fall inside. This rejects gross errors only;
# plan 2's R3 decides the country.
US_BOXES: tuple[tuple[float, float, float, float], ...] = (
    (24.3, 49.5, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.9),
    (51.0, 53.1, 172.0, 180.0),
    (18.8, 22.4, -160.3, -154.7),
)


def in_us(lat: float, lon: float) -> bool:
    return any(lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi for lat_lo, lat_hi, lon_lo, lon_hi in US_BOXES)


def date_problem(value: date | None, *, today: date, earliest: date | None = None) -> str | None:
    if value is None:
        return "missing_date"
    if value > today:
        return "future"
    if earliest is not None and value < earliest:
        return "too_early"
    return None


def coord_problem(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return "missing_coords"
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return "not_finite"
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return "out_of_range"
    if not in_us(lat, lon):
        return "outside_us"
    return None


def range_problem(value: float | None, lo: float, hi: float) -> str | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return "not_finite"
    if not lo <= value <= hi:
        return "out_of_range"
    return None


@dataclass(frozen=True)
class Issue:
    row_ref: str
    reason: str
    detail: dict[str, object]


@dataclass
class ValidationReport:
    source: str
    rows_in: int = 0
    accepted: int = 0
    quarantined: Counter[str] = field(default_factory=Counter)
    issues: list[Issue] = field(default_factory=list)

    def accept(self) -> None:
        self.rows_in += 1
        self.accepted += 1

    def quarantine(self, row_ref: str, reason: str, **detail: object) -> None:
        self.rows_in += 1
        self.quarantined[reason] += 1
        self.issues.append(Issue(row_ref, reason, dict(detail)))

    def quarantined_total(self) -> int:
        return sum(self.quarantined.values())

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "rows_in": self.rows_in,
            "accepted": self.accepted,
            "quarantined": dict(sorted(self.quarantined.items())),
        }


def batch_gate(
    report: ValidationReport,
    *,
    previous_rows_in: int | None,
    count_tolerance: float,
    max_quarantine_share: float,
) -> list[str]:
    if report.rows_in == 0:
        return ["no rows"]
    problems: list[str] = []
    if previous_rows_in:
        if abs(report.rows_in - previous_rows_in) / previous_rows_in > count_tolerance:
            problems.append(
                f"rows_in {report.rows_in} differs from last ok run {previous_rows_in} "
                f"by more than {count_tolerance:.0%}"
            )
    share = report.quarantined_total() / report.rows_in
    if share > max_quarantine_share:
        problems.append(f"quarantined share {share:.3f} exceeds {max_quarantine_share:.3f}")
    return problems
