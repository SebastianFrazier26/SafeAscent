import json
import logging
from datetime import date

import httpx
import pytest

from app.pipelines.open_meteo import (
    CUSTOMER_URL,
    DAILY_VARS,
    ArchiveClient,
    Location,
    OpenMeteoError,
    cost_units,
)

TZ = "America/Denver"
LOC = [Location(4003947, 40.0, -105.3, TZ), Location(4003948, 40.0, -105.2, TZ)]
D1 = date(2020, 1, 1)


def _payload(lat: float, lon: float, days: list[str], tmax: list[float | None]) -> dict[str, object]:
    daily: dict[str, object] = {"time": days}
    for var in DAILY_VARS:
        daily[var] = tmax if var == "temperature_2m_max" else [1.0] * len(days)
    return {"latitude": lat, "longitude": lon, "elevation": 1650.0, "timezone": TZ, "daily": daily}


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[_payload(40.0, -105.3, ["2020-01-01"], [1.0])])


def _offline(handler) -> ArchiveClient:
    return ArchiveClient(None, transport=httpx.MockTransport(handler))


def test_cost_units_rule_is_a_ceiling():
    assert cost_units(1, 6, 7) == 1
    assert cost_units(50, 6, 366) == 50 * 27
    assert cost_units(2, 11, 14) == 4


def test_request_parameters_and_list_response():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        seen["host"] = request.url.host
        body = [_payload(40.0, -105.3, ["2020-01-01"], [3.0]), _payload(40.0, -105.2, ["2020-01-01"], [2.0])]
        return httpx.Response(200, json=body)

    client = ArchiveClient("secret-key", transport=httpx.MockTransport(handler))
    out = client.fetch(LOC, D1, D1)
    assert [r.daily.temperature_2m_max for r in out] == [[3.0], [2.0]]
    assert out[0].timezone == TZ
    assert seen["host"] == "customer-archive-api.open-meteo.com"
    assert seen["wind_speed_unit"] == "ms" and seen["models"] == "era5_seamless" and seen["timezone"] == TZ
    assert seen["latitude"] == "40.0,40.0" and seen["longitude"] == "-105.3,-105.2"
    assert seen["daily"] == ",".join(DAILY_VARS)
    assert seen["start_date"] == "2020-01-01" and seen["end_date"] == "2020-01-01"


def test_one_request_never_mixes_timezones():
    mixed = [LOC[0], Location(4003948, 40.0, -105.2, "America/Los_Angeles")]
    with pytest.raises(ValueError, match="one timezone"):
        _offline(lambda r: httpx.Response(500)).fetch(mixed, D1, D1)
    with pytest.raises(ValueError, match="one timezone"):
        _offline(lambda r: httpx.Response(500)).fetch([], D1, D1)


def test_start_after_end_is_refused():
    with pytest.raises(ValueError, match="start"):
        _offline(_ok).fetch(LOC[:1], date(2020, 1, 2), D1)


def test_single_location_response_is_a_list_of_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(40.0, -105.3, ["2020-01-01"], [None]))

    out = _offline(handler).fetch(LOC[:1], D1, D1)
    assert len(out) == 1 and out[0].daily.temperature_2m_max == [None]


def test_missing_values_stay_none_never_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_payload(40.0, -105.3, ["2020-01-01", "2020-01-02"], [None, 0.0])])

    out = _offline(handler).fetch(LOC[:1], D1, date(2020, 1, 2))
    assert out[0].daily.temperature_2m_max == [None, 0.0]


def test_a_missing_variable_is_rejected_not_defaulted():
    def handler(request: httpx.Request) -> httpx.Response:
        body = _payload(40.0, -105.3, ["2020-01-01"], [1.0])
        del body["daily"]["snowfall_sum"]  # type: ignore[attr-defined]
        return httpx.Response(200, json=[body])

    with pytest.raises(OpenMeteoError, match="snowfall_sum"):
        _offline(handler).fetch(LOC[:1], D1, D1)


def test_non_finite_values_are_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps([_payload(40.0, -105.3, ["2020-01-01"], [float("nan")])])
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with pytest.raises(OpenMeteoError, match="invalid response"):
        _offline(handler).fetch(LOC[:1], D1, D1)


def test_ragged_arrays_are_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        bad = _payload(40.0, -105.3, ["2020-01-01", "2020-01-02"], [1.0])
        return httpx.Response(200, json=[bad])

    with pytest.raises(OpenMeteoError, match="length"):
        _offline(handler).fetch(LOC[:1], D1, date(2020, 1, 2))


@pytest.mark.parametrize("days", [["2019-12-31"], ["2020-01-02", "2020-01-01"], ["2020-01-01", "2020-01-01"]])
def test_days_outside_the_request_or_out_of_order_are_rejected(days):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_payload(40.0, -105.3, days, [1.0] * len(days))])

    with pytest.raises(OpenMeteoError, match="time"):
        _offline(handler).fetch(LOC[:1], D1, date(2020, 1, 2))


def test_location_count_mismatch_is_rejected():
    with pytest.raises(OpenMeteoError, match="expected 2 locations, got 1"):
        _offline(_ok).fetch(LOC, D1, D1)


def test_non_json_200_is_an_open_meteo_error():
    with pytest.raises(OpenMeteoError, match="non-JSON"):
        _offline(lambda r: httpx.Response(200, content=b"<html>oops")).fetch(LOC[:1], D1, D1)


def test_retries_on_429_then_succeeds_and_errors_never_leak_the_key():
    calls = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": True, "reason": "limit"})
        return _ok(request)

    client = ArchiveClient("secret-key", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert len(client.fetch(LOC[:1], D1, D1)) == 1
    assert slept == [5.0, 10.0]

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=json.dumps({"error": True, "reason": "bad date"}))

    with pytest.raises(OpenMeteoError) as err:
        ArchiveClient("secret-key", transport=httpx.MockTransport(bad)).fetch(LOC[:1], D1, D1)
    assert "secret-key" not in str(err.value) and "bad date" in str(err.value)


def test_retries_exhausted_reports_the_last_status():
    slept: list[float] = []
    client = ArchiveClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(503)), max_retries=2,
                           sleep=slept.append)
    with pytest.raises(OpenMeteoError, match="HTTP 503"):
        client.fetch(LOC[:1], D1, D1)
    assert slept == [5.0, 10.0]


def test_transport_errors_are_retried_and_never_leak_the_url():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout(f"timed out reading {request.url}", request=request)
        return _ok(request)

    slept: list[float] = []
    client = ArchiveClient("secret-key", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert len(client.fetch(LOC[:1], D1, D1)) == 1 and slept == [5.0]

    def always(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(OpenMeteoError) as err:
        ArchiveClient("secret-key", transport=httpx.MockTransport(always), max_retries=1,
                      sleep=lambda s: None).fetch(LOC[:1], D1, D1)
    assert "ConnectError" in str(err.value)
    assert "secret-key" not in str(err.value) and "open-meteo" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_a_reason_echoing_the_key_is_scrubbed():
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": True, "reason": "apikey secret-key is invalid"})

    with pytest.raises(OpenMeteoError) as err:
        ArchiveClient("secret-key", transport=httpx.MockTransport(bad)).fetch(LOC[:1], D1, D1)
    assert "secret-key" not in str(err.value) and "HTTP 401" in str(err.value)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_no_key_fails_closed_instead_of_using_the_public_host(key):
    with pytest.raises(OpenMeteoError, match="OPEN_METEO_API_KEY"):
        ArchiveClient(key)


def test_a_real_custom_transport_without_a_key_is_refused():
    with pytest.raises(OpenMeteoError, match="OPEN_METEO_API_KEY"):
        ArchiveClient(None, transport=httpx.HTTPTransport())


def test_mock_transport_is_exempt_from_the_key_check():
    # MockTransport answers in-process, so the public URL it is handed is never contacted.
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return _ok(request)

    ArchiveClient(None, transport=httpx.MockTransport(handler)).fetch(LOC[:1], D1, D1)
    assert hosts == ["archive-api.open-meteo.com"]


class _RecordingTransport(httpx.BaseTransport):
    """Not a MockTransport, so the key check treats it like a real network transport."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        assert "apikey" not in request.url.params
        return _ok(request)


def test_public_host_only_on_explicit_opt_in():
    with pytest.raises(OpenMeteoError, match="OPEN_METEO_API_KEY"):
        ArchiveClient(None, transport=_RecordingTransport())
    transport = _RecordingTransport()
    ArchiveClient(None, allow_public=True, transport=transport).fetch(LOC[:1], D1, D1)
    assert transport.hosts == ["archive-api.open-meteo.com"]


def test_key_is_sent_to_the_customer_host_only():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _ok(request)

    with ArchiveClient("secret-key", transport=httpx.MockTransport(handler)) as client:
        client.fetch(LOC[:1], D1, D1)
    assert str(seen[0]).startswith(CUSTOMER_URL) and seen[0].params["apikey"] == "secret-key"


def test_close_releases_the_http_client():
    client = ArchiveClient("k", transport=httpx.MockTransport(_ok))
    client.close()
    with pytest.raises(OpenMeteoError):
        client.fetch(LOC[:1], D1, D1)


def test_httpx_request_log_line_never_carries_the_key(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        ArchiveClient("secret-key", transport=httpx.MockTransport(_ok)).fetch(LOC[:1], D1, D1)
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert lines and all("secret-key" not in line for line in lines)
    assert any("apikey=***" in line for line in lines)


def test_on_attempt_runs_before_every_send_with_the_previous_outcome():
    calls: list[str] = []
    script = iter([httpx.Response(503), httpx.Response(429), "timeout", None])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append("sent")
        step = next(script)
        if step == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return step if isinstance(step, httpx.Response) else _ok(request)

    def on_attempt(attempt: int, previous: str | None) -> None:
        calls.append(f"attempt {attempt} after {previous}")

    client = ArchiveClient("k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert len(client.fetch(LOC[:1], D1, D1, on_attempt=on_attempt)) == 1
    assert calls == ["attempt 0 after None", "sent", "attempt 1 after server_error", "sent",
                     "attempt 2 after rate_limited", "sent", "attempt 3 after transport_error", "sent"]


def test_an_on_attempt_refusal_stops_the_request_unsent():
    sent = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        return httpx.Response(503)

    def refuse_retries(attempt: int, previous: str | None) -> None:
        if attempt:
            raise RuntimeError("no budget")

    with pytest.raises(RuntimeError, match="no budget"):
        ArchiveClient("k", transport=httpx.MockTransport(handler), sleep=lambda s: None).fetch(
            LOC[:1], D1, D1, on_attempt=refuse_retries)
    assert sent["n"] == 1
