"""Explicit orchestrator loading without silent dummy fallbacks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OrchestratorUnavailable(RuntimeError):
    """Raised when D.A.I.S.E orchestration cannot be imported or initialized."""


@dataclass
class OrchestratorLoadResult:
    input_modality: Any = None
    orchestrator: Any = None
    orchestrator_class: Any = None
    available: bool = False
    error: Optional[str] = None

    def voice_unavailable_payload(self) -> dict[str, Any]:
        return {"active": False, "message": self.error or "D.A.I.S.E backend unavailable"}


_loaded: Optional[OrchestratorLoadResult] = None


def load_unified_orchestrator(*, required: bool = True, instance: bool = True) -> OrchestratorLoadResult:
    """Load the real unified orchestrator and make failures explicit."""

    global _loaded
    if _loaded is not None and (_loaded.available or not required):
        return _loaded
    try:
        from blackfong_ecosystem.unified_orchestrator import InputModality, UnifiedOrchestrator
        from blackfong_ecosystem.unified_orchestrator import unified_orchestrator

        _loaded = OrchestratorLoadResult(
            input_modality=InputModality,
            orchestrator=unified_orchestrator if instance else None,
            orchestrator_class=UnifiedOrchestrator,
            available=True,
            error=None,
        )
        return _loaded
    except Exception as exc:
        logger.exception("D.A.I.S.E orchestrator import failed")
        result = OrchestratorLoadResult(available=False, error=f"D.A.I.S.E backend unavailable: {exc}")
        _loaded = result
        if required:
            raise OrchestratorUnavailable(result.error) from exc
        return result


def reset_orchestrator_loader_for_tests() -> None:
    global _loaded
    _loaded = None
