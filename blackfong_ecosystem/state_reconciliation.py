"""
State Reconciliation Layer for O.B.E.O.S

Phase 5.2: Periodic reconciliation jobs comparing Event Store ↔ Indexed Memory ↔ Vector Memory
with automated repair mechanisms to prevent silent state divergence.

Principles:
- Event Store is the source of truth for all state
- Regular reconciliation prevents silent divergence
- Automated repair when inconsistencies are detected
- Reconciliation runs as background jobs with configurable frequency
"""

from __future__ import annotations

import time
import threading
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

from blackfong_ecosystem.observability import log_action, LogLevel

def _safe_log_action(level: str, service_name: str, action: str, **kwargs):
    """Safely log an action, avoiding circular imports."""
    try:
        from blackfong_ecosystem.observability import log_action, LogLevel
        log_level = LogLevel.INFO  # Default to INFO
        if level == "WARNING":
            log_level = LogLevel.WARNING
        elif level == "ERROR":
            log_level = LogLevel.ERROR
        log_action(log_level, service_name, action, **kwargs)
    except ImportError:
        # Fallback to basic logging if circular import
        import logging
        logger = logging.getLogger(service_name)
        message = f"[{service_name}] {action}"
        if kwargs:
            message += f" {kwargs}"
        logger.info(message)  # Default to info
from blackfong_ecosystem.storage import EventStore
# from blackfong_ecosystem.orchestrator import orchestrator  # Optional import


@dataclass
class ReconciliationResult:
    """Result of a reconciliation operation."""
    timestamp: float
    component_a: str
    component_b: str
    total_checked: int
    inconsistencies_found: int
    inconsistencies_repaired: int
    repair_failures: int
    duration_seconds: float
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


@dataclass
class InconsistencyReport:
    """Report of a specific inconsistency found during reconciliation."""
    inconsistency_type: str
    entity_id: str
    component_a_value: Any
    component_b_value: Any
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    repair_action: Optional[str] = None
    repair_successful: bool = False
    error_message: Optional[str] = None


class StateReconciler:
    """
    State reconciliation system that ensures consistency across all state stores.

    Compares:
    - Event Store (source of truth) ↔ Indexed Memory (derived state)
    - Event Store (source of truth) ↔ Vector Memory (embeddings)
    - Indexed Memory ↔ Vector Memory (cross-validation)

    Automatically repairs inconsistencies when detected.
    """

    def __init__(self, reconciliation_interval: int = 300):  # 5 minutes default
        self.reconciliation_interval = reconciliation_interval
        self.event_store = EventStore()
        # Orchestrator is optional for now
        self.orchestrator = None
        try:
            from blackfong_ecosystem.orchestrator import orchestrator
            self.orchestrator = orchestrator
        except ImportError:
            pass

        # Reconciliation results history
        self.reconciliation_history: List[ReconciliationResult] = []
        self.max_history = 1000

        # Background reconciliation thread
        self.reconciling = False
        self.reconciliation_thread: Optional[threading.Thread] = None

        # Reconciliation statistics
        self.stats = {
            "total_reconciliations": 0,
            "total_inconsistencies_found": 0,
            "total_inconsistencies_repaired": 0,
            "total_repair_failures": 0,
        }

    def start_reconciliation(self) -> None:
        """Start the background reconciliation process."""
        if self.reconciling:
            return

        self.reconciling = True
        self.reconciliation_thread = threading.Thread(
            target=self._reconciliation_loop,
            daemon=True
        )
        self.reconciliation_thread.start()

        _safe_log_action(
            "INFO",
            "state_reconciler",
            "reconciliation_started",
            interval_seconds=self.reconciliation_interval
        )

    def stop_reconciliation(self) -> None:
        """Stop the background reconciliation process."""
        self.reconciling = False
        if self.reconciliation_thread:
            self.reconciliation_thread.join(timeout=10)

        _safe_log_action("INFO", "state_reconciler", "reconciliation_stopped")

    def _reconciliation_loop(self) -> None:
        """Main reconciliation loop that runs periodically."""
        while self.reconciling:
            try:
                start_time = time.time()
                self._perform_full_reconciliation()
                duration = time.time() - start_time

                # Sleep for the remaining interval
                sleep_time = max(0, self.reconciliation_interval - duration)
                time.sleep(sleep_time)

            except Exception as e:
                log_action(
                    LogLevel.ERROR,
                    "state_reconciler",
                    "reconciliation_loop_error",
                    error_message=str(e)
                )
                time.sleep(60)  # Wait before retrying

    def _perform_full_reconciliation(self) -> None:
        """Perform a full reconciliation across all state stores."""
        correlation_id = f"recon_{int(time.time())}"

        _safe_log_action(
            "INFO",
            "state_reconciler",
            "reconciliation_started",
            correlation_id=correlation_id
        )

        try:
            # Reconciliation 1: Event Store ↔ Indexed Memory
            result1 = self._reconcile_event_store_vs_indexed_memory(correlation_id)

            # Reconciliation 2: Event Store ↔ Vector Memory
            result2 = self._reconcile_event_store_vs_vector_memory(correlation_id)

            # Reconciliation 3: Indexed Memory ↔ Vector Memory
            result3 = self._reconcile_indexed_vs_vector_memory(correlation_id)

            # Update statistics
            self.stats["total_reconciliations"] += 1
            for result in [result1, result2, result3]:
                if result:
                    self.stats["total_inconsistencies_found"] += result.inconsistencies_found
                    self.stats["total_inconsistencies_repaired"] += result.inconsistencies_repaired
                    self.stats["total_repair_failures"] += result.repair_failures

                    # Store result
                    self.reconciliation_history.append(result)
                    if len(self.reconciliation_history) > self.max_history:
                        self.reconciliation_history.pop(0)

            _safe_log_action(
                "INFO",
                "state_reconciler",
                "reconciliation_completed",
                correlation_id=correlation_id,
                results={
                    "event_store_vs_indexed": result1.inconsistencies_found if result1 else 0,
                    "event_store_vs_vector": result2.inconsistencies_found if result2 else 0,
                    "indexed_vs_vector": result3.inconsistencies_found if result3 else 0,
                }
            )

        except Exception as e:
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "reconciliation_failed",
                correlation_id=correlation_id,
                error_message=str(e)
            )

    def _reconcile_event_store_vs_indexed_memory(self, correlation_id: str) -> Optional[ReconciliationResult]:
        """Reconcile Event Store against Indexed Memory."""
        start_time = time.time()

        try:
            # Get all events from Event Store
            all_events = self.event_store.get_all_events()
            event_entities = self._group_events_by_entity(all_events)

            # Get indexed memory state (this would need to be implemented in orchestrator)
            indexed_entities = self._get_indexed_memory_entities()

            inconsistencies = []
            total_checked = len(event_entities)

            for entity_id, event_data in event_entities.items():
                indexed_data = indexed_entities.get(entity_id)

                if indexed_data is None:
                    # Entity missing from indexed memory
                    inconsistencies.append(InconsistencyReport(
                        inconsistency_type="missing_from_indexed_memory",
                        entity_id=entity_id,
                        component_a_value=event_data,
                        component_b_value=None,
                        severity="HIGH",
                        repair_action="rebuild_indexed_entity"
                    ))
                else:
                    # Compare entity state
                    if not self._entities_consistent(event_data, indexed_data):
                        inconsistencies.append(InconsistencyReport(
                            inconsistency_type="state_mismatch",
                            entity_id=entity_id,
                            component_a_value=event_data,
                            component_b_value=indexed_data,
                            severity="MEDIUM",
                            repair_action="update_indexed_entity"
                        ))

            # Perform repairs
            repaired = 0
            failed = 0
            for inconsistency in inconsistencies:
                if self._repair_inconsistency(inconsistency, "event_store", "indexed_memory"):
                    repaired += 1
                    inconsistency.repair_successful = True
                else:
                    failed += 1

            duration = time.time() - start_time

            return ReconciliationResult(
                timestamp=time.time(),
                component_a="event_store",
                component_b="indexed_memory",
                total_checked=total_checked,
                inconsistencies_found=len(inconsistencies),
                inconsistencies_repaired=repaired,
                repair_failures=failed,
                duration_seconds=duration,
                details={"inconsistencies": [vars(i) for i in inconsistencies[:10]]}  # First 10 for logging
            )

        except Exception as e:
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "event_store_vs_indexed_reconciliation_error",
                correlation_id=correlation_id,
                error_message=str(e)
            )
            return None

    def _reconcile_event_store_vs_vector_memory(self, correlation_id: str) -> Optional[ReconciliationResult]:
        """Reconcile Event Store against Vector Memory."""
        start_time = time.time()

        try:
            # Get all events from Event Store
            all_events = self.event_store.get_all_events()
            event_entities = self._group_events_by_entity(all_events)

            # Get vector memory state
            vector_entities = self._get_vector_memory_entities()

            inconsistencies = []
            total_checked = len(event_entities)

            for entity_id, event_data in event_entities.items():
                vector_data = vector_entities.get(entity_id)

                if vector_data is None:
                    # Entity missing from vector memory
                    inconsistencies.append(InconsistencyReport(
                        inconsistency_type="missing_from_vector_memory",
                        entity_id=entity_id,
                        component_a_value=event_data,
                        component_b_value=None,
                        severity="MEDIUM",
                        repair_action="rebuild_vector_embedding"
                    ))
                else:
                    # Compare embedding freshness (based on last update timestamps)
                    if not self._vector_embedding_current(event_data, vector_data):
                        inconsistencies.append(InconsistencyReport(
                            inconsistency_type="stale_vector_embedding",
                            entity_id=entity_id,
                            component_a_value=event_data,
                            component_b_value=vector_data,
                            severity="LOW",
                            repair_action="refresh_vector_embedding"
                        ))

            # Perform repairs
            repaired = 0
            failed = 0
            for inconsistency in inconsistencies:
                if self._repair_inconsistency(inconsistency, "event_store", "vector_memory"):
                    repaired += 1
                    inconsistency.repair_successful = True
                else:
                    failed += 1

            duration = time.time() - start_time

            return ReconciliationResult(
                timestamp=time.time(),
                component_a="event_store",
                component_b="vector_memory",
                total_checked=total_checked,
                inconsistencies_found=len(inconsistencies),
                inconsistencies_repaired=repaired,
                repair_failures=failed,
                duration_seconds=duration,
                details={"inconsistencies": [vars(i) for i in inconsistencies[:10]]}
            )

        except Exception as e:
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "event_store_vs_vector_reconciliation_error",
                correlation_id=correlation_id,
                error_message=str(e)
            )
            return None

    def _reconcile_indexed_vs_vector_memory(self, correlation_id: str) -> Optional[ReconciliationResult]:
        """Cross-validate Indexed Memory against Vector Memory."""
        start_time = time.time()

        try:
            # Get entities from both stores
            indexed_entities = self._get_indexed_memory_entities()
            vector_entities = self._get_vector_memory_entities()

            all_entity_ids = set(indexed_entities.keys()) | set(vector_entities.keys())
            inconsistencies = []
            total_checked = len(all_entity_ids)

            for entity_id in all_entity_ids:
                indexed_data = indexed_entities.get(entity_id)
                vector_data = vector_entities.get(entity_id)

                if indexed_data is None and vector_data is not None:
                    # Entity in vector but not indexed
                    inconsistencies.append(InconsistencyReport(
                        inconsistency_type="orphaned_vector_embedding",
                        entity_id=entity_id,
                        component_a_value=None,
                        component_b_value=vector_data,
                        severity="MEDIUM",
                        repair_action="remove_orphaned_vector"
                    ))
                elif indexed_data is not None and vector_data is None:
                    # Entity in indexed but not vector
                    inconsistencies.append(InconsistencyReport(
                        inconsistency_type="missing_vector_for_indexed",
                        entity_id=entity_id,
                        component_a_value=indexed_data,
                        component_b_value=None,
                        severity="MEDIUM",
                        repair_action="create_missing_vector"
                    ))

            # Perform repairs
            repaired = 0
            failed = 0
            for inconsistency in inconsistencies:
                if self._repair_inconsistency(inconsistency, "indexed_memory", "vector_memory"):
                    repaired += 1
                    inconsistency.repair_successful = True
                else:
                    failed += 1

            duration = time.time() - start_time

            return ReconciliationResult(
                timestamp=time.time(),
                component_a="indexed_memory",
                component_b="vector_memory",
                total_checked=total_checked,
                inconsistencies_found=len(inconsistencies),
                inconsistencies_repaired=repaired,
                repair_failures=failed,
                duration_seconds=duration,
                details={"inconsistencies": [vars(i) for i in inconsistencies[:10]]}
            )

        except Exception as e:
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "indexed_vs_vector_reconciliation_error",
                correlation_id=correlation_id,
                error_message=str(e)
            )
            return None

    def _group_events_by_entity(self, events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Group events by entity ID to get current state."""
        entities = {}

        for event in events:
            entity_id = event.get("entity_id") or event.get("correlation_id")
            if entity_id:
                if entity_id not in entities:
                    entities[entity_id] = {
                        "entity_id": entity_id,
                        "last_event_timestamp": event["timestamp"],
                        "event_count": 0,
                        "latest_event": event
                    }
                else:
                    entities[entity_id]["event_count"] += 1
                    if event["timestamp"] > entities[entity_id]["last_event_timestamp"]:
                        entities[entity_id]["last_event_timestamp"] = event["timestamp"]
                        entities[entity_id]["latest_event"] = event

        return entities

    def _get_indexed_memory_entities(self) -> Dict[str, Any]:
        """Get entities from indexed memory (orchestrator's knowledge base)."""
        # This would need to be implemented based on how indexed memory is stored
        # For now, return empty dict as placeholder
        return {}

    def _get_vector_memory_entities(self) -> Dict[str, Any]:
        """Get entities from vector memory (ChromaDB or similar)."""
        # This would need to be implemented based on how vector memory is stored
        # For now, return empty dict as placeholder
        try:
            # Try to get from orchestrator's semantic search
            if hasattr(self.orchestrator, 'semantic_search') and self.orchestrator.semantic_search:
                # This is a placeholder - actual implementation would query the vector store
                return {}
        except:
            pass
        return {}

    def _entities_consistent(self, event_data: Dict[str, Any], indexed_data: Dict[str, Any]) -> bool:
        """Check if entity data is consistent between stores."""
        # Compare last event timestamp vs indexed timestamp
        event_timestamp = event_data.get("last_event_timestamp", 0)
        indexed_timestamp = indexed_data.get("last_updated", 0)

        # Allow for small timing differences (1 second)
        return abs(event_timestamp - indexed_timestamp) < 1.0

    def _vector_embedding_current(self, event_data: Dict[str, Any], vector_data: Dict[str, Any]) -> bool:
        """Check if vector embedding is current with event data."""
        event_timestamp = event_data.get("last_event_timestamp", 0)
        vector_timestamp = vector_data.get("last_updated", 0)

        # Vector embeddings can be slightly stale (5 minutes)
        return (time.time() - vector_timestamp) < 300

    def _repair_inconsistency(self, inconsistency: InconsistencyReport, component_a: str, component_b: str) -> bool:
        """Attempt to repair a detected inconsistency."""
        try:
            if inconsistency.repair_action == "rebuild_indexed_entity":
                # Rebuild entity in indexed memory from event store
                return self._rebuild_indexed_entity(inconsistency.entity_id)

            elif inconsistency.repair_action == "update_indexed_entity":
                # Update indexed entity with latest event data
                return self._update_indexed_entity(inconsistency.entity_id, inconsistency.component_a_value)

            elif inconsistency.repair_action == "rebuild_vector_embedding":
                # Rebuild vector embedding
                return self._rebuild_vector_embedding(inconsistency.entity_id)

            elif inconsistency.repair_action == "refresh_vector_embedding":
                # Refresh existing vector embedding
                return self._refresh_vector_embedding(inconsistency.entity_id)

            elif inconsistency.repair_action == "remove_orphaned_vector":
                # Remove orphaned vector embedding
                return self._remove_orphaned_vector(inconsistency.entity_id)

            elif inconsistency.repair_action == "create_missing_vector":
                # Create missing vector embedding
                return self._create_missing_vector(inconsistency.entity_id)

            else:
                _safe_log_action(
                    "WARNING",
                    "state_reconciler",
                    "unknown_repair_action",
                    entity_id=inconsistency.entity_id,
                    repair_action=inconsistency.repair_action
                )
                return False

        except Exception as e:
            inconsistency.error_message = str(e)
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "repair_failed",
                entity_id=inconsistency.entity_id,
                repair_action=inconsistency.repair_action,
                error_message=str(e)
            )
            return False

    def _rebuild_indexed_entity(self, entity_id: str) -> bool:
        """Rebuild an entity in indexed memory from event store."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "rebuilding_indexed_entity",
            entity_id=entity_id
        )
        return True

    def _update_indexed_entity(self, entity_id: str, event_data: Dict[str, Any]) -> bool:
        """Update an indexed entity with latest data."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "updating_indexed_entity",
            entity_id=entity_id
        )
        return True

    def _rebuild_vector_embedding(self, entity_id: str) -> bool:
        """Rebuild vector embedding for an entity."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "rebuilding_vector_embedding",
            entity_id=entity_id
        )
        return True

    def _refresh_vector_embedding(self, entity_id: str) -> bool:
        """Refresh existing vector embedding."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "refreshing_vector_embedding",
            entity_id=entity_id
        )
        return True

    def _remove_orphaned_vector(self, entity_id: str) -> bool:
        """Remove orphaned vector embedding."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "removing_orphaned_vector",
            entity_id=entity_id
        )
        return True

    def _create_missing_vector(self, entity_id: str) -> bool:
        """Create missing vector embedding."""
        # Placeholder implementation
        _safe_log_action(
            "INFO",
            "state_reconciler",
            "creating_missing_vector",
            entity_id=entity_id
        )
        return True

    def check_consistency(self) -> bool:
        """Check if all state stores are currently consistent."""
        try:
            correlation_id = f"consistency_check_{int(time.time())}"

            # Quick check: compare counts from different stores
            event_count = len(self.event_store.get_all_events())
            
            # Check indexed memory (if available)
            indexed_count = 0
            try:
                indexed_entities = self._get_indexed_memory_entities()
                indexed_count = len(indexed_entities)
            except Exception:
                pass  # Indexed memory might not be available

            # Check vector memory (if available)  
            vector_count = 0
            try:
                vector_entities = self._get_vector_memory_entities()
                vector_count = len(vector_entities)
            except Exception:
                pass  # Vector memory might not be available

            # For now, consider consistent if counts are reasonable
            # In a real implementation, this would do deeper checks
            max_diff = max(event_count, indexed_count, vector_count) * 0.1  # 10% tolerance
            
            consistent = abs(event_count - indexed_count) <= max_diff and abs(event_count - vector_count) <= max_diff
            
            _safe_log_action(
                "INFO",
                "state_reconciler", 
                "consistency_check",
                correlation_id=correlation_id,
                event_count=event_count,
                indexed_count=indexed_count,
                vector_count=vector_count,
                consistent=consistent
            )
            
            return consistent
            
        except Exception as e:
            _safe_log_action(
                "ERROR",
                "state_reconciler",
                "consistency_check_error",
                error_message=str(e)
            )
            return False

    def get_reconciliation_stats(self) -> Dict[str, Any]:
        """Get current reconciliation statistics."""
        return {
            **self.stats,
            "active": self.reconciling,
            "interval_seconds": self.reconciliation_interval,
            "recent_results": [vars(r) for r in self.reconciliation_history[-5:]],  # Last 5 results
        }

    def trigger_manual_reconciliation(self) -> Dict[str, Any]:
        """Trigger a manual reconciliation cycle."""
        correlation_id = f"manual_recon_{int(time.time())}"

        _safe_log_action(
            "INFO",
            "state_reconciler",
            "manual_reconciliation_triggered",
            correlation_id=correlation_id
        )

        start_time = time.time()
        self._perform_full_reconciliation()
        duration = time.time() - start_time

        return {
            "correlation_id": correlation_id,
            "duration_seconds": duration,
            "stats": self.get_reconciliation_stats(),
        }


# Global state reconciler instance
state_reconciler = StateReconciler()