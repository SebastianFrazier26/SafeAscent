"""The trainer role's engine (P3 M2). Training reads clean views and writes only model tables.

The trainer URL lives only in backend/.env.trainer on the owner's Mac (pre-flight F48). Like
ingest, it must name the direct host and verify-full TLS; no message here ever echoes the URL."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.pipelines.db import require_direct_host, verified_connect_args

VAR = "TRAINER_DATABASE_URL"


def trainer_engine() -> AsyncEngine:
    url = settings.TRAINER_DATABASE_URL
    if not url:
        raise SystemExit(f"{VAR} is not set (the trainer role's URL); see DEPLOYMENT.md")
    require_direct_host(url, var=VAR)
    try:
        connect_args = verified_connect_args(url, var=VAR)
    except ValueError:
        # connect_args_for's message embeds the (password-masked) URL; the host is still not ours to print.
        raise SystemExit(f"{VAR} has no host to verify") from None
    return create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
