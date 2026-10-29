"""Read-only readiness checks shared by model supervision and generation."""

import httpx

from .chat_backends import ChatBackendError


def verify_llamacpp(client, base_url: str, model: str) -> None:
    """A listening HTTP process is not necessarily our configured model."""
    metadata = {"provider": "llamacpp", "model": model}
    try:
        health = client.get(f"{base_url}/health", timeout=2.0)
        health.raise_for_status()
        response = client.get(f"{base_url}/v1/models", timeout=2.0)
        response.raise_for_status()
        models = response.json().get("data", [])
        if not any(item.get("id") == model for item in models):
            raise ChatBackendError(
                "The local endpoint is not serving the configured D.A.I.S.E. model",
                metadata={**metadata, "error_type": "model_identity_mismatch"},
            )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise ChatBackendError(
            "The configured D.A.I.S.E. endpoint is not ready",
            metadata={**metadata, "error_type": "endpoint_not_ready"},
        ) from exc
