import time

from blackfong_ecosystem.http_clients import client_for


def _verifies(client) -> bool:
    # httpx exposes no public flag; the transport's SSL context shows whether verification was requested.
    pool = client._transport._pool
    ctx = pool._ssl_context
    import ssl

    return ctx is not None and ctx.verify_mode != ssl.CERT_NONE


def test_plain_http_skips_certificate_verification():
    with client_for("http://127.0.0.1:5000/api/status", timeout=1) as client:
        assert not _verifies(client)


def test_https_keeps_verification():
    with client_for("https://example.com", timeout=1) as client:
        assert _verifies(client)


def test_explicit_verify_is_respected():
    with client_for("http://127.0.0.1:5000", timeout=1, verify=True) as client:
        assert _verifies(client)


def test_http_client_construction_is_cheap():
    start = time.perf_counter()
    client_for("http://localhost:11434").close()
    assert time.perf_counter() - start < 0.5
