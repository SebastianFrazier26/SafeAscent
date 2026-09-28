"""TLS posture for the Postgres connection: full verification against Neon, none locally.

asyncpg's `ssl=verify-full` string mode (what SQLAlchemy's asyncpg dialect passes
straight through from a `?ssl=verify-full` URL query param) needs a root cert file
at `~/.postgresql/root.crt`, an explicit `sslrootcert=`, or `PGSSLROOTCERT` set, or it
raises `ClientConfigurationError` at connect time — there is no `sslrootcert=system`
fallback to the OS trust store like libpq 16+ has. None of those exist in the
`python:3.12-slim` image (no `ca-certificates` package, no home directory setup), so
the URL string alone can't carry verify-full. Building the SSLContext here with
certifi's bundle (already an installed dependency, pinned in pyproject.toml) instead
means verification doesn't depend on OS packages being present in any environment
this runs in.

Fail-closed: parsed with `make_url`, the same parser SQLAlchemy's dialect uses, not
`urlsplit` — a password containing `/`, `#`, or `?` can make `urlsplit` report a
different (and possibly locally-named) host than the one the driver actually
connects to. Every host the URL could resolve to (`url.host` and any `?host=`
query value, which the postgres dialects also honor and which may repeat as a
tuple) must be in the explicit local set, or a unix-socket path, for the
connection to be exempted; anything else, including a URL with no host at all,
gets (or is refused) verification rather than silently skipping it.
"""
from __future__ import annotations

import ssl

import certifi
from sqlalchemy.engine import URL, make_url

# docker-compose's `db` service and CI Postgres have no TLS listener at all;
# forcing ssl there would break every local/CI connection.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})


def is_local_host(host: str) -> bool:
    """True for docker-compose's `db`, loopback names, or a unix-socket path."""
    return host in _LOCAL_HOSTS or host.startswith("/")


def _hosts(url: URL) -> list[str]:
    hosts = [url.host] if url.host else []
    query_host = url.query.get("host")
    if isinstance(query_host, tuple):
        hosts.extend(h for h in query_host if h)
    elif isinstance(query_host, str):
        hosts.extend(h for h in query_host.split(",") if h)
    return hosts


def connect_args_for(database_url: str) -> dict[str, object]:
    """asyncpg connect_args: verified TLS for a remote host, none for a local one.

    Raises ValueError rather than defaulting to either posture when the URL has
    no host component to judge at all.
    """
    url = make_url(database_url)
    hosts = _hosts(url)
    if not hosts:
        raise ValueError(f"database URL has no host to check (not even a unix socket path): {url!r}")
    if all(is_local_host(host) for host in hosts):
        return {}
    context = ssl.create_default_context(cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return {"ssl": context}
