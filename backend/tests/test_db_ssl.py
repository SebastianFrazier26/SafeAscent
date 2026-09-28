"""app.db.ssl.connect_args_for: verified TLS for a remote host, none for local.

No network I/O here — connect_args_for only inspects the URL and builds an
SSLContext; it never opens a socket.
"""
import ssl

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.engine import make_url

import app.db.ssl as db_ssl
from app.db.ssl import connect_args_for


# --- exemption is by parsed host, not by string-matching the URL --------------

@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://safeascent:pw@localhost:5432/safeascent",
        "postgresql+asyncpg://safeascent:pw@127.0.0.1:5432/safeascent",
        "postgresql+asyncpg://safeascent:pw@db:5432/safeascent?ssl=verify-full",
        "postgresql+asyncpg://user:pw@[::1]:5432/db",
        "postgresql+asyncpg://user:pw@/dbname?host=/var/run/postgresql",
    ],
)
def test_no_tls_for_local_hosts_and_unix_sockets(url):
    assert connect_args_for(url) == {}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://app:pw@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=verify-full",
        "postgresql+asyncpg://user:pw@/dbname?host=ep-x.neon.tech",
        # Password contains an unescaped "/": urlsplit's naive parse would read the
        # netloc as "db:p" and hostname as "db" (a name in _LOCAL_HOSTS), exempting a
        # Neon connection. make_url (the parser SQLAlchemy's own dialect uses) gets
        # user="db", password="p/x", host="ep.neon.tech" — this must verify, not exempt.
        "postgresql+asyncpg://db:p/x@ep.neon.tech/db",
    ],
)
def test_verified_tls_for_remote_and_mismatched_hosts(url):
    args = connect_args_for(url)
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_no_host_at_all_raises_instead_of_defaulting():
    with pytest.raises(ValueError):
        connect_args_for("postgresql+asyncpg:///db")


# --- CA source is certifi's bundle, not whatever the OS happens to have -------

def test_uses_certifis_cafile(monkeypatch):
    monkeypatch.setattr(db_ssl.certifi, "where", lambda: "/no/such/file.pem")
    with pytest.raises(FileNotFoundError):
        connect_args_for("postgresql+asyncpg://app:pw@ep-x.neon.tech/neondb")


# --- dialect-level: connect_args wins over the URL's own ssl= query param -----

@pytest.mark.parametrize("url_ssl_param", ["disable", "require"])
def test_connect_args_overrides_url_ssl_param_the_way_sqlalchemy_merges_it(url_ssl_param):
    """Mirrors sqlalchemy.engine.create's `cparams.update(connect_args)` (create.py):
    dialect-parsed opts first, engine-level connect_args merged in after and winning.
    """
    url = f"postgresql+asyncpg://app:pw@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl={url_ssl_param}"
    dialect = PGDialect_asyncpg()
    _, cparams = dialect.create_connect_args(make_url(url))
    assert cparams["ssl"] == url_ssl_param  # the dialect alone just forwards the string

    cparams.update(connect_args_for(url))

    assert isinstance(cparams["ssl"], ssl.SSLContext)
    assert cparams["ssl"].verify_mode == ssl.CERT_REQUIRED
