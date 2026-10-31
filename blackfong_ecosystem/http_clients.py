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

    Every caller of this helper talks to a local service (Ollama, llama-server, TinyProgrammer, Earth's
    own live stream) on 127.0.0.1/localhost. httpx defaults to trust_env=True, which reads the system's
    HTTP_PROXY/HTTPS_PROXY/NO_PROXY environment variables - on a machine with any proxy configured
    (a VPN, an antivirus web-shield) and no NO_PROXY exemption for localhost, that silently routes these
    local-only requests through the external proxy, which then rejects them (seen as a raw "401" surfacing
    in TinyProgrammer's embedded stream panel - the proxy's auth challenge, not anything from
    TinyProgrammer's own server, which has no auth of its own). Several call sites already passed
    trust_env=False individually as a one-off workaround; defaulting it here means every caller gets it,
    including the ones that hadn't. Callers that genuinely need the system proxy (none currently do -
    this helper is documented as local-service-only) can still pass trust_env=True explicitly.
    """
    if "verify" not in kwargs and urlparse(str(url or "")).scheme.lower() == "http":
        kwargs["verify"] = False
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)
