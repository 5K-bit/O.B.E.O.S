"""httpx client construction that does not pay for TLS on plain-http local calls."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


def client_for(url: str, **kwargs: Any) -> httpx.Client:
    """Build an ``httpx.Client`` for ``url``.

    A verifying client loads the CA bundle on construction, which costs several seconds per client on
    this Windows machine and is wasted work for ``http://`` URLs (local services never negotiate TLS).
    For ``http`` URLs certificate verification is skipped; ``https`` URLs and callers that pass
    ``verify`` explicitly keep normal behavior.
    """
    if "verify" not in kwargs and urlparse(str(url or "")).scheme.lower() == "http":
        kwargs["verify"] = False
    return httpx.Client(**kwargs)
