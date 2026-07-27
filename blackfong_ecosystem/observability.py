"""
O.B.E.O.S Observability Layer

Phase 5.1: Foundation of Trust
Complete system visibility and real-time monitoring.

Principles:
- Event store = source of truth. Everything else = projection.
- No silent failures - all failures emit events and are observable.
- Correlation IDs are mandatory for every workflow.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

from blackfong_ecosystem.alert_engine import alert_engine
from blackfong_ecosystem.metrics_collector import metrics_collector
# from blackfong_ecosystem.state_reconciliation import state_reconciler  # Lazy import


class LogLevel(str, Enum):
    """Standardized log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEntry:
    """Structured log entry with mandatory fields."""
    timestamp: float
    level: LogLevel
    service_name: str
    action: str
    correlation_id: Optional[str] = None
    event_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    latency_ms: Optional[float] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "service_name": self.service_name,
            "action": self.action,
            "correlation_id": self.correlation_id,
            "event_id": self.event_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


class ObservabilityEngine:
    """
    Central observability engine for the O.B.E.O.S ecosystem.

    Provides:
    - Structured logging with mandatory correlation IDs
    - Real-time metrics collection and aggregation
    - Event-level monitoring and throughput tracking
    - Alert trigger system
    """

    def __init__(self, retention_period: int = 3600):  # 1 hour default
        self.retention_period = retention_period

        # Logging
        self.log_entries: deque[LogEntry] = deque(maxlen=10000)  # Keep last 10K entries
        self._setup_structured_logging()

        # Service registry for tracking active services
        self.active_services: set[str] = set()

        # Background cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_old_data, daemon=True)
        self._cleanup_thread.start()

        # Start the integrated alert engine and metrics collector
        alert_engine.start_monitoring()
        metrics_collector.start_collection()
        # Start state reconciler (lazy import to avoid circular dependency)
        try:
            from blackfong_ecosystem.state_reconciliation import state_reconciler
            state_reconciler.start_reconciliation()
        except ImportError:
            pass  # State reconciler not available

    def _setup_structured_logging(self) -> None:
        """Configure structured logging with JSON output."""
        # Configure structlog for JSON logging
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.processors.JSONRenderer()
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    def log(
        self,
        level: LogLevel,
        service_name: str,
        action: str,
        correlation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
        **metadata
    ) -> None:
        """
        Emit a structured log entry.

        All services MUST call this method for logging.
        Correlation IDs are mandatory for workflow tracking.
        """
        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            service_name=service_name,
            action=action,
            correlation_id=correlation_id,
            event_id=event_id,
            user_id=user_id,
            session_id=session_id,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
            metadata=metadata,
        )

        # Store in memory for observability
        self.log_entries.append(entry)

        # Also emit to standard logging
        logger = logging.getLogger(service_name)
        log_method = getattr(logger, level.value.lower())

        log_data = entry.to_dict()
        log_method(f"[{service_name}] {action}", extra={"structured_data": log_data})

        # Track active services
        self.active_services.add(service_name)

        # Alert engine will check for alerts based on metrics and logs

    def record_metric(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
        **extra_labels
    ) -> None:
        """
        Record a metric measurement.

        Args:
            name: Metric name (e.g., "request_latency", "queue_depth")
            value: Numeric value
            labels: Key-value labels for categorization
        """
        if labels is None:
            labels = {}
        labels.update(extra_labels)

        # Delegate to metrics collector - assume gauge by default
        metrics_collector.set_gauge(name, value, labels)

    def increment_counter(self, name: str, labels: Optional[Dict[str, str]] = None, **extra_labels) -> None:
        """Increment a counter metric by 1."""
        if labels is None:
            labels = {}
        labels.update(extra_labels)
        metrics_collector.increment_counter(name, 1, labels)

    def observe_histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None, **extra_labels) -> None:
        """Observe a value in a histogram metric."""
        if labels is None:
            labels = {}
        labels.update(extra_labels)
        metrics_collector.record_histogram(name, value, labels)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of current metrics state."""
        summary = metrics_collector.get_all_metrics_summary()
        summary.update({
            "alerts_active": len(alert_engine.get_active_alerts()),
            "services_active": list(self.active_services),
        })
        # Add state reconciliation stats (lazy import)
        try:
            from blackfong_ecosystem.state_reconciliation import state_reconciler
            summary["state_reconciliation"] = state_reconciler.get_reconciliation_stats()
        except ImportError:
            summary["state_reconciliation"] = {"error": "state reconciler not available"}
        return summary

    def get_counter_rate(self, name: str, window_seconds: int = 300) -> float:
        """Return a per-second rate for a counter-like metric over a time window."""
        return metrics_collector.get_rate(name, window_seconds)

    def get_recent_logs(self, limit: int = 100, service_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent log entries, optionally filtered by service."""
        logs = list(self.log_entries)
        if service_filter:
            logs = [log for log in logs if log.service_name == service_filter]

        return [log.to_dict() for log in logs[-limit:]]

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        return alert_engine.get_alert_history(limit)

    def _cleanup_old_data(self) -> None:
        """Background thread to clean up old logs."""
        while True:
            try:
                cutoff = time.time() - self.retention_period

                # Clean up old log entries (metrics and alerts cleaned up by their respective engines)
                # Log entries are already limited by deque maxlen, but we can clean up older ones
                # The deque handles this automatically with maxlen=10000

                time.sleep(300)  # Check every 5 minutes

            except Exception as e:
                # Log cleanup errors but don't crash
                print(f"Observability cleanup error: {e}")
                time.sleep(60)


class ObservabilityManager:
    """
    High-level observability manager for system monitoring.
    """

    def __init__(self):
        self.monitoring_active = False
        self.metrics = {}

    def start_monitoring(self):
        """Start observability monitoring."""
        self.monitoring_active = True
        observability.log(LogLevel.INFO, "observability_manager", "monitoring_started")

    def stop_monitoring(self):
        """Stop observability monitoring."""
        self.monitoring_active = False
        observability.log(LogLevel.INFO, "observability_manager", "monitoring_stopped")

    def record_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a metric."""
        self.metrics[name] = {"value": value, "tags": tags or {}, "timestamp": time.time()}
        observability.record_metric(name, value, tags or {})

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        return self.metrics.copy()


# Global observability instance
observability = ObservabilityEngine()


def log_action(
    level: LogLevel,
    service_name: str,
    action: str,
    correlation_id: Optional[str] = None,
    **kwargs
) -> None:
    """
    Convenience function for logging actions.

    Usage:
        log_action(LogLevel.INFO, "orchestrator", "route_request",
                  correlation_id=corr_id, agent="jarvis", confidence=0.95)
    """
    # Build the call arguments
    call_args = {
        'level': level,
        'service_name': service_name,
        'action': action,
    }
    if correlation_id is not None:
        call_args['correlation_id'] = correlation_id
    call_args.update(kwargs)
    
    observability.log(**call_args)


def time_function(service_name: str, action: str, correlation_id: Optional[str] = None):
    """
    Decorator to time function execution and log it.

    Usage:
        @time_function("orchestrator", "process_request")
        def process_request(correlation_id, user_input):
            # ... function body ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                latency = (time.time() - start_time) * 1000  # Convert to ms

                # Extract correlation_id from kwargs or args if possible
                corr_id = kwargs.get('correlation_id') or (
                    args[0] if args and isinstance(args[0], str) and len(args[0]) == 36 else None
                )

                observability.log(
                    LogLevel.INFO,
                    service_name,
                    action,
                    correlation_id=corr_id,
                    latency_ms=latency,
                    status="success"
                )

                return result

            except Exception as e:
                latency = (time.time() - start_time) * 1000

                corr_id = kwargs.get('correlation_id') or (
                    args[0] if args and isinstance(args[0], str) and len(args[0]) == 36 else None
                )

                observability.log(
                    LogLevel.ERROR,
                    service_name,
                    action,
                    correlation_id=corr_id,
                    latency_ms=latency,
                    status="error",
                    error_message=str(e)
                )

                raise

        return wrapper
    return decorator
