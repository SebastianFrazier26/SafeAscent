"""healthchecks.io dead-man's-switch pings. Alerting must never break the job it watches."""

from __future__ import annotations

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

PingSuffix = Literal["", "/start", "/fail"]


def ping(
    url: str | None,
    suffix: PingSuffix = "",
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 10.0,
) -> bool:
    if not url:
        logger.warning("healthchecks ping%s skipped: no ping URL configured", suffix or " (success)")
        return False
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            client.get(f"{url.rstrip('/')}{suffix}").raise_for_status()
    except httpx.HTTPError as exc:
        # The ping URL is the check's credential, so only the error type is logged.
        logger.warning("healthchecks ping%s failed: %s", suffix or " (success)", type(exc).__name__)
        return False
    return True
