"""
Alert Engine for O.B.E.O.S Observability

Phase 5.1: Simple alert triggers for common issues.
Monitors system health and triggers alerts when conditions are met.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# Avoid circular import by importing later or using a lazy import
# from blackfong_ecosystem.observability import LogLevel, log_action, observability

def _safe_log_action(level: str, service_name: str, action: str, **kwargs):
    """Safely log an action, avoiding circular imports."""
    try:
        from blackfong_ecosystem.observability import log_action, LogLevel
        log_level = LogLevel.WARNING if level == "WARNING" else LogLevel.ERROR if level == "ERROR" else LogLevel.INFO
        log_action(log_level, service_name, action, **kwargs)
    except ImportError:
        # Fallback to basic logging if circular import
        import logging
        logger = logging.getLogger(service_name)
        message = f"[{service_name}] {action}"
        if kwargs:
            message += f" {kwargs}"
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)


@dataclass
class AlertRule:
    """Definition of an alert rule."""
    name: str
    condition: Callable[[], bool]  # Function that returns True if alert should trigger
    severity: str  # "WARNING", "ERROR", "CRITICAL"
    message: str
    cooldown_seconds: int = 300  # Minimum time between alerts of same type
    enabled: bool = True
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        self.last_triggered: float = 0.0
        self.trigger_count: int = 0


@dataclass
class Alert:
    """An alert that has been triggered."""
    rule_name: str
    severity: str
    message: str
    timestamp: float
    context: Dict[str, Any] = None
    resolved: bool = False
    resolved_at: Optional[float] = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "timestamp": self.timestamp,
            "context": self.context,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at,
        }


class AlertEngine:
    """
    Alert engine that monitors system conditions and triggers alerts.

    Simple but effective - checks conditions periodically and triggers
    alerts when thresholds are exceeded.
    """

    def __init__(self, check_interval: int = 30):  # Check every 30 seconds
        self.rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self.check_interval = check_interval

        # Background monitoring thread
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None

        # Setup default alert rules
        self._setup_default_rules()

    def _setup_default_rules(self) -> None:
        """Setup the default alert rules for common O.B.E.O.S issues."""

        # Queue backlog alert
        self.add_rule(AlertRule(
            name="queue_backlog_high",
            condition=self._check_queue_backlog,
            severity="WARNING",
            message="Event queue backlog exceeds safe threshold",
            cooldown_seconds=300,  # 5 minutes
            metadata={"threshold": 100, "description": "Monitors event processing queue depth"}
        ))

        # Event publish failure rate alert
        self.add_rule(AlertRule(
            name="event_publish_failures_high",
            condition=self._check_event_publish_failures,
            severity="ERROR",
            message="Event publish failure rate is too high",
            cooldown_seconds=600,  # 10 minutes
            metadata={"threshold": 10, "window_seconds": 300, "description": "Monitors event publishing reliability"}
        ))

        # Routing confidence alert
        self.add_rule(AlertRule(
            name="routing_confidence_low",
            condition=self._check_routing_confidence,
            severity="WARNING",
            message="Average routing confidence has dropped below acceptable level",
            cooldown_seconds=300,  # 5 minutes
            metadata={"threshold": 0.7, "description": "Monitors routing decision quality"}
        ))

        # Tool execution failure alert
        self.add_rule(AlertRule(
            name="tool_execution_failures_high",
            condition=self._check_tool_execution_failures,
            severity="ERROR",
            message="Tool execution failure rate is too high",
            cooldown_seconds=600,  # 10 minutes
            metadata={"threshold": 5, "window_seconds": 300, "description": "Monitors tool execution reliability"}
        ))

        # Memory usage alert
        self.add_rule(AlertRule(
            name="memory_usage_high",
            condition=self._check_memory_usage,
            severity="WARNING",
            message="System memory usage is approaching limits",
            cooldown_seconds=900,  # 15 minutes
            metadata={"threshold_mb": 1000, "description": "Monitors system memory consumption"}
        ))

        # Service heartbeat alert
        self.add_rule(AlertRule(
            name="service_heartbeat_missing",
            condition=self._check_service_heartbeats,
            severity="ERROR",
            message="One or more critical services have missed heartbeat",
            cooldown_seconds=300,  # 5 minutes
            metadata={"timeout_seconds": 300, "description": "Monitors service health via heartbeats"}
        ))

    def add_rule(self, rule: AlertRule) -> None:
        """Add a new alert rule."""
        self.rules[rule.name] = rule
        _safe_log_action(
            "INFO",
            "alert_engine",
            "rule_added",
            rule_name=rule.name,
            severity=rule.severity
        )

    def remove_rule(self, rule_name: str) -> bool:
        """Remove an alert rule."""
        if rule_name in self.rules:
            del self.rules[rule_name]
            _safe_log_action(
                "INFO",
                "alert_engine",
                "rule_removed",
                rule_name=rule_name
            )
            return True
        return False

    def enable_rule(self, rule_name: str) -> bool:
        """Enable an alert rule."""
        if rule_name in self.rules:
            self.rules[rule_name].enabled = True
            _safe_log_action(
                "INFO",
                "alert_engine",
                "rule_enabled",
                rule_name=rule_name
            )
            return True
        return False

    def disable_rule(self, rule_name: str) -> bool:
        """Disable an alert rule."""
        if rule_name in self.rules:
            self.rules[rule_name].enabled = False
            _safe_log_action(
                "INFO",
                "alert_engine",
                "rule_disabled",
                rule_name=rule_name
            )
            return True
        return False

    def start_monitoring(self) -> None:
        """Start the background monitoring thread."""
        if self.monitoring:
            return

        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

        _safe_log_action(
            "INFO",
            "alert_engine",
            "monitoring_started",
            check_interval=self.check_interval
        )

    def stop_monitoring(self) -> None:
        """Stop the background monitoring thread."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

        _safe_log_action("INFO", "alert_engine", "monitoring_stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop that checks all rules periodically."""
        while self.monitoring:
            try:
                self._check_all_rules()
                time.sleep(self.check_interval)
            except Exception as e:
                _safe_log_action(
                    "ERROR",
                    "alert_engine",
                    "monitor_loop_error",
                    error_message=str(e)
                )
                time.sleep(60)  # Wait longer on error

    def _check_all_rules(self) -> None:
        """Check all enabled alert rules."""
        current_time = time.time()

        for rule in self.rules.values():
            if not rule.enabled:
                continue

            try:
                # Check if rule condition is met
                if rule.condition():
                    # Check cooldown period
                    time_since_last = current_time - rule.last_triggered
                    if time_since_last >= rule.cooldown_seconds:
                        self._trigger_alert(rule, current_time)

            except Exception as e:
                _safe_log_action(
                    "ERROR",
                    "alert_engine",
                    "rule_check_error",
                    rule_name=rule.name,
                    error_message=str(e)
                )

    def _trigger_alert(self, rule: AlertRule, timestamp: float) -> None:
        """Trigger an alert for a rule."""
        rule.trigger_count += 1
        rule.last_triggered = timestamp

        alert = Alert(
            rule_name=rule.name,
            severity=rule.severity,
            message=rule.message,
            timestamp=timestamp,
            context={
                "rule_metadata": rule.metadata,
                "trigger_count": rule.trigger_count,
                "cooldown_seconds": rule.cooldown_seconds,
            }
        )

        # Store as active alert
        self.active_alerts[rule.name] = alert
        self.alert_history.append(alert)

        # Keep only last 1000 alerts in history
        if len(self.alert_history) > 1000:
            self.alert_history.pop(0)

        # Log the alert (avoid circular import by using direct logging)
        _safe_log_action(
            rule.severity,
            "alert_engine",
            "alert_triggered",
            rule_name=rule.name,
            severity=rule.severity,
            message=rule.message,
            alert_id=f"{rule.name}_{int(timestamp)}"
        )

    def resolve_alert(self, rule_name: str) -> bool:
        """Manually resolve an active alert."""
        if rule_name in self.active_alerts:
            alert = self.active_alerts[rule_name]
            alert.resolved = True
            alert.resolved_at = time.time()

            _safe_log_action(
                "INFO",
                "alert_engine",
                "alert_resolved",
                rule_name=rule_name,
                resolution_time=alert.resolved_at - alert.timestamp
            )

            return True
        return False

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get all currently active alerts."""
        return [alert.to_dict() for alert in self.active_alerts.values()]

    def get_alert_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent alert history."""
        return [alert.to_dict() for alert in self.alert_history[-limit:]]

    def get_rule_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all alert rules."""
        return {
            name: {
                "enabled": rule.enabled,
                "severity": rule.severity,
                "last_triggered": rule.last_triggered,
                "trigger_count": rule.trigger_count,
                "cooldown_seconds": rule.cooldown_seconds,
                "metadata": rule.metadata,
            }
            for name, rule in self.rules.items()
        }

    # Alert condition checkers

    def _get_observability(self):
        """Load observability lazily so startup checks do not explode on import order."""
        try:
            from blackfong_ecosystem.observability import observability

            return observability
        except Exception:
            return None

    def _check_queue_backlog(self) -> bool:
        """Check if event queue backlog is too high."""
        # Get current queue depth from observability metrics
        observability = self._get_observability()
        if observability is None:
            return False
        summary = observability.get_metrics_summary()
        queue_depth = summary.get("gauges", {}).get("queue_depth", 0)
        threshold = 100  # Configurable threshold

        return queue_depth > threshold

    def _check_event_publish_failures(self) -> bool:
        """Check if event publish failure rate is too high."""
        # Calculate failure rate over last 5 minutes
        observability = self._get_observability()
        if observability is None:
            return False
        failure_rate = observability.get_counter_rate("event_publish_failures", 300)
        threshold = 10  # Failures per 5 minutes

        return failure_rate > threshold

    def _check_routing_confidence(self) -> bool:
        """Check if average routing confidence is too low."""
        # This would need to be implemented based on how routing confidence is tracked
        # For now, return False (placeholder)
        return False

    def _check_tool_execution_failures(self) -> bool:
        """Check if tool execution failure rate is too high."""
        # Calculate failure rate over last 5 minutes
        observability = self._get_observability()
        if observability is None:
            return False
        failure_rate = observability.get_counter_rate("tool_execution_failures", 300)
        threshold = 5  # Failures per 5 minutes

        return failure_rate > threshold

    def _check_memory_usage(self) -> bool:
        """Check if system memory usage is too high."""
        # This would need system monitoring integration
        # For now, return False (placeholder)
        return False

    def _check_service_heartbeats(self) -> bool:
        """Check if critical services have missed heartbeats."""
        # This would need heartbeat tracking
        # For now, return False (placeholder)
        return False


# Global alert engine instance
alert_engine = AlertEngine()


def trigger_alert(rule_name: str, context: Optional[Dict[str, Any]] = None) -> None:
    """
    Manually trigger an alert for testing or custom conditions.

    Args:
        rule_name: Name of the alert rule
        context: Additional context for the alert
    """
    if rule_name in alert_engine.rules:
        rule = alert_engine.rules[rule_name]
        alert_engine._trigger_alert(rule, time.time())
    else:
        _safe_log_action(
            "WARNING",
            "alert_engine",
            "manual_alert_trigger_failed",
            rule_name=rule_name,
            reason="rule_not_found"
        )
