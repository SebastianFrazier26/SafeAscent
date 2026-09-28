import logging

import httpx

from app.healthchecks import ping

URL = "https://hc-ping.com/secret-uuid"


def _recording_transport(seen: list[str], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def test_ping_appends_suffix():
    seen: list[str] = []
    assert ping(URL, "/start", transport=_recording_transport(seen)) is True
    assert seen == [f"{URL}/start"]


def test_success_ping_hits_bare_url():
    seen: list[str] = []
    assert ping(URL + "/", transport=_recording_transport(seen)) is True
    assert seen == [URL]


def test_unset_url_is_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(None, "/fail") is False
    assert "skipped" in caplog.text


def test_http_error_status_returns_false():
    assert ping(URL, transport=_recording_transport([], status=500)) is False


def test_network_error_is_swallowed_and_url_never_logged(caplog):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(URL, "/fail", transport=httpx.MockTransport(boom)) is False
    assert "secret-uuid" not in caplog.text
    assert "ConnectError" in caplog.text


def test_invalid_url_returns_false_without_raising_or_logging_url(caplog):
    malformed = "https://hc-ping.com/secret-uuid\x00bad"
    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(malformed, "/start", transport=_recording_transport([])) is False
    assert "secret-uuid" not in caplog.text
    assert "InvalidURL" in caplog.text


def test_url_whitespace_from_env_is_stripped():
    seen: list[str] = []
    assert ping(f"  {URL}/ \n", "/start", transport=_recording_transport(seen)) is True
    assert seen == [f"{URL}/start"]


def test_unexpected_client_error_is_swallowed(monkeypatch, caplog):
    def broken_client(*args, **kwargs):
        raise RuntimeError(f"boom {URL}")

    monkeypatch.setattr("app.healthchecks.httpx.Client", broken_client)
    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(URL) is False
    assert "secret-uuid" not in caplog.text
