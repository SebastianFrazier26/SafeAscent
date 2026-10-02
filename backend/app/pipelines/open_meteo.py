"""Open-Meteo historical archive (ERA5 via era5_seamless). Open, documented API; wind in m/s.

httpx exceptions carry the request URL, which holds the API key, so they are never
re-raised or logged as-is: every failure becomes an OpenMeteoError with a clean message.
The archive needs the Professional plan (Standard excludes it); callers run only inside a
declared Professional window (era5_window).
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

DAILY_VARS: tuple[str, ...] = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
)
ARCHIVE_MODEL = "era5_seamless"
PUBLIC_URL = "https://archive-api.open-meteo.com/v1/archive"
CUSTOMER_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
AttemptOutcome = Literal["rate_limited", "server_error", "transport_error"]

_APIKEY_IN_TEXT = re.compile(r"(apikey=)[^&\s\"']+")


class _RedactApiKey(logging.Filter):
    """httpx logs every request URL at INFO, query string included, so the key would reach any
    handler a CLI sets to INFO. Filtering the logger (not raising its level) keeps the line."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "apikey=" in message:
            record.msg, record.args = _APIKEY_IN_TEXT.sub(r"\1***", message), None
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _RedactApiKey) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactApiKey())


class OpenMeteoError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    grid_bucket: int
    lat: float
    lon: float
    tz: str


class DailyBlock(BaseModel):
    # NaN/Infinity would otherwise validate as floats and be stored as if measured.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    time: list[date]
    temperature_2m_max: list[float | None]
    temperature_2m_min: list[float | None]
    precipitation_sum: list[float | None]
    snowfall_sum: list[float | None]
    wind_speed_10m_max: list[float | None]
    wind_gusts_10m_max: list[float | None]

    @model_validator(mode="after")
    def _same_length(self) -> DailyBlock:
        n = len(self.time)
        for var in DAILY_VARS:
            if len(getattr(self, var)) != n:
                raise ValueError(f"{var} length {len(getattr(self, var))} != time length {n}")
        return self


class LocationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    latitude: float
    longitude: float
    elevation: float | None = None
    timezone: str | None = None
    daily: DailyBlock


def cost_units(n_locations: int, n_vars: int, n_days: int) -> int:
    # Open-Meteo bills fractionally; the ceiling over-estimates, so a budget cap stops early, never late.
    return n_locations * math.ceil(n_vars / 10) * math.ceil(n_days / 14)


def kmh_to_ms(kmh: float) -> float:
    return kmh / 3.6


class ArchiveClient:
    """With no key the client refuses to start unless `allow_public=True`: the public host is
    non-commercial and rate-limited, so a missing key must stop a paid run, not reroute it.
    Only an httpx.MockTransport (tests) is exempt: it never leaves the process, whereas any
    other injected transport can still reach the public host."""

    def __init__(
        self,
        api_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        allow_public: bool = False,
    ) -> None:
        key = (api_key or "").strip() or None
        if key is None and not allow_public and not isinstance(transport, httpx.MockTransport):
            raise OpenMeteoError("OPEN_METEO_API_KEY is not set; refusing to use the public non-commercial archive host")
        self._key = key
        self._url = CUSTOMER_URL if key else PUBLIC_URL
        self._client = httpx.Client(transport=transport, timeout=120.0)
        self._max_retries = max_retries
        self._sleep = sleep

    def __enter__(self) -> ArchiveClient:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(
        self,
        locations: Sequence[Location],
        start: date,
        end: date,
        *,
        on_attempt: Callable[[int, AttemptOutcome | None], None] | None = None,
    ) -> list[LocationResponse]:
        """`on_attempt(attempt, previous)` runs before every send, with the outcome that caused the
        retry; whatever it raises propagates and the request is not sent. Paid callers use it to
        record each billable attempt before it can be billed."""
        zones = {loc.tz for loc in locations}
        if len(zones) != 1:
            raise ValueError(f"one request covers exactly one timezone, got {sorted(zones)}")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        params = {
            "latitude": ",".join(f"{loc.lat:.1f}" for loc in locations),
            "longitude": ",".join(f"{loc.lon:.1f}" for loc in locations),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARS),
            "timezone": zones.pop(),
            "wind_speed_unit": "ms",
            "models": ARCHIVE_MODEL,
        }
        if self._key:
            params["apikey"] = self._key
        if self._client.is_closed:
            raise OpenMeteoError("client is closed")
        previous: AttemptOutcome | None = None
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            if on_attempt is not None:
                on_attempt(attempt, previous)
            try:
                response = self._client.get(self._url, params=params)
            except httpx.TransportError as exc:
                if last_try:
                    raise OpenMeteoError(f"transport error: {type(exc).__name__} after {attempt + 1} attempts") from None
                previous = "transport_error"
                self._sleep(5.0 * 2**attempt)
                continue
            except httpx.HTTPError as exc:
                raise OpenMeteoError(f"request error: {type(exc).__name__}") from None
            if response.status_code in RETRY_STATUSES and not last_try:
                previous = "rate_limited" if response.status_code == 429 else "server_error"
                self._sleep(5.0 * 2**attempt)
                continue
            if response.status_code != 200:
                raise OpenMeteoError(self._scrub(f"HTTP {response.status_code}: {self._reason(response)}"))
            return self._parse(response, len(locations), start, end)
        raise AssertionError("unreachable: the last attempt always returns or raises")

    def _scrub(self, message: str) -> str:
        message = _APIKEY_IN_TEXT.sub(r"\1***", message)
        return message.replace(self._key, "***") if self._key else message

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return "non-JSON error body"
        return str(body.get("reason", "no reason given")) if isinstance(body, dict) else "unexpected error body"

    @staticmethod
    def _parse(response: httpx.Response, expected: int, start: date, end: date) -> list[LocationResponse]:
        try:
            body = response.json()
        except ValueError:
            raise OpenMeteoError("non-JSON response body") from None
        items = body if isinstance(body, list) else [body]
        if len(items) != expected:
            raise OpenMeteoError(f"expected {expected} locations, got {len(items)}")
        try:
            parsed = [LocationResponse.model_validate(item) for item in items]
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise OpenMeteoError(f"invalid response at {where}: {first['msg']}") from None
        for item in parsed:
            days = item.daily.time
            if any(d < start or d > end for d in days) or any(a >= b for a, b in zip(days, days[1:])):
                raise OpenMeteoError(f"daily.time outside {start}..{end} or not strictly increasing")
        return parsed
