from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from .config import paths
from .storage import EventStore, NullVectorStore, VectorStore

_memory_chambers: Dict[Tuple[int, int, str], Any] = {}
_runtime_lock = threading.RLock()


def _memory_chamber_key(*, event_store: EventStore, vector_store: VectorStore, workspace_root: Path) -> Tuple[int, int, str]:
    return (id(event_store), id(vector_store), str(workspace_root.resolve()))


def get_shared_memory_chamber(
    *,
    event_store: Optional[EventStore] = None,
    vector_store: Optional[VectorStore] = None,
    workspace_root: Optional[Path] = None,
    integration_registry: Any = None,
    startup_coordinator: Any = None,
    extra_roots: Optional[Iterable[Path]] = None,
    start_watch: bool = False,
    watch_interval_s: Optional[float] = None,
) -> Any:
    """
    Reuse a single Memory Chamber instance for the same event/vector/root authority.

    This keeps desktop, HUD, and D.A.I.S.E aligned on one runtime graph cache
    without introducing a second persistence authority.
    """

    from .memory_chamber import MemoryChamberService

    shared_event_store = event_store or EventStore()
    shared_vector_store = vector_store or NullVectorStore()
    resolved_root = Path(workspace_root or paths.root)
    key = _memory_chamber_key(
        event_store=shared_event_store,
        vector_store=shared_vector_store,
        workspace_root=resolved_root,
    )
    with _runtime_lock:
        chamber = _memory_chambers.get(key)
        if chamber is None:
            kwargs: Dict[str, Any] = {
                "event_store": shared_event_store,
                "vector_store": shared_vector_store,
                "workspace_root": resolved_root,
                "integration_registry": integration_registry,
                "startup_coordinator": startup_coordinator,
                "extra_roots": extra_roots,
                "start_watch": start_watch,
            }
            if watch_interval_s is not None:
                kwargs["watch_interval_s"] = watch_interval_s
            chamber = MemoryChamberService(**kwargs)
            _memory_chambers[key] = chamber
        else:
            if integration_registry is not None and getattr(chamber, "integration_registry", None) is None:
                chamber.integration_registry = integration_registry
            if startup_coordinator is not None and getattr(chamber, "startup_coordinator", None) is None:
                chamber.startup_coordinator = startup_coordinator
            if start_watch:
                chamber.ensure_live_watch(start_index=False)
        return chamber


def reset_runtime_ownership_for_tests(*, shutdown: bool = False) -> None:
    with _runtime_lock:
        chambers = list(_memory_chambers.values())
        _memory_chambers.clear()
    if not shutdown:
        return
    for chamber in chambers:
        try:
            chamber.shutdown()
        except Exception:
            continue
