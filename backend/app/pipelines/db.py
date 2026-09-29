"""The ingest role's engine. Jobs never use DATABASE_URL (the app role cannot write).

The ingest role writes data every model trains on, so a remote connection must verify the
server certificate and host name (D14). connect_args_for already builds a verify-full
context; this module additionally refuses a URL whose own query asks for a weaker mode, so a
copied `sslmode=require` URL fails loudly instead of depending on which setting wins.
"""

from __future__ import annotations

import ssl

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.ssl import connect_args_for

TLS_QUERY_KEYS = ("ssl", "sslmode")


def verified_connect_args(url: str) -> dict[str, object]:
    args = connect_args_for(url)
    if not args:
        return args
    query = make_url(url).query
    weaker = sorted(key for key in TLS_QUERY_KEYS if key in query and query[key] != "verify-full")
    if weaker:
        raise SystemExit(
            f"INGEST_DATABASE_URL sets {', '.join(weaker)} to something other than verify-full on a "
            "remote host; remove it (TLS is verified by app.db.ssl) or set verify-full"
        )
    context = args.get("ssl")
    if not (
        isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    ):
        raise SystemExit("INGEST_DATABASE_URL would connect to a remote host without verify-full TLS")
    return args


def ingest_engine() -> AsyncEngine:
    url = settings.INGEST_DATABASE_URL
    if not url:
        raise SystemExit("INGEST_DATABASE_URL is not set (the ingest role's URL); see DEPLOYMENT.md")
    return create_async_engine(url, poolclass=NullPool, connect_args=verified_connect_args(url))
