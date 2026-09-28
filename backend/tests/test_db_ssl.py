"""app.db.ssl.connect_args_for: verified TLS for a remote host, none for local.

No network I/O here — connect_args_for only inspects the URL and builds an
SSLContext; it never opens a socket.
"""
import ssl

import pytest

from app.db.ssl import connect_args_for


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://safeascent:pw@localhost:5432/safeascent",
        "postgresql+asyncpg://safeascent:pw@127.0.0.1:5432/safeascent",
        "postgresql+asyncpg://safeascent:pw@db:5432/safeascent?ssl=verify-full",
    ],
)
def test_no_tls_for_local_hosts(url):
    assert connect_args_for(url) == {}


def test_verified_tls_for_a_remote_host():
    url = "postgresql+asyncpg://app:pw@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=verify-full"
    args = connect_args_for(url)
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_hostless_url_is_treated_as_local():
    assert connect_args_for("postgresql+asyncpg:///db") == {}
