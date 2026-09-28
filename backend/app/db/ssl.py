"""TLS posture for the Postgres connection: full verification against Neon, none locally.

asyncpg's `ssl=verify-full` string mode (what SQLAlchemy's asyncpg dialect passes
straight through from a `?ssl=verify-full` URL query param) needs a root cert file
at `~/.postgresql/root.crt`, an explicit `sslrootcert=`, or `PGSSLROOTCERT` set, or it
raises `ClientConfigurationError` at connect time — there is no `sslrootcert=system`
fallback to the OS trust store like libpq 14+ has. None of those exist in the
`python:3.12-slim` image (no `ca-certificates` package, no home directory setup), so
the URL string alone can't carry verify-full. Building the SSLContext here with
certifi's bundle (already an installed dependency, pinned in pyproject.toml) instead
means verification doesn't depend on OS packages being present in any environment
this runs in.
"""
from __future__ import annotations

import ssl
from urllib.parse import urlsplit

import certifi

# docker-compose's `db` service and CI Postgres have no TLS listener at all;
# forcing ssl there would break every local/CI connection.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})


def connect_args_for(database_url: str) -> dict[str, object]:
    """asyncpg connect_args: verified TLS for a remote host, none for a local one."""
    host = urlsplit(database_url).hostname
    if host is None or host in _LOCAL_HOSTS:
        return {}
    context = ssl.create_default_context(cafile=certifi.where())
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return {"ssl": context}
