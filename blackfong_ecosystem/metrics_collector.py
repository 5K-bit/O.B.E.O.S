"""
Metrics Collector for O.B.E.O.S Observability

Phase 5.1: Comprehensive metrics collection and aggregation.
Collects performance, reliability, and operational metrics.
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Avoid circular import - import only when needed in _safe_log_action

def _safe_log_action(level: str, service_name: str, action: str, **kwargs):
    """Safely log an action, avoiding circular imports."""
    try:
        from blackfong_ecosystem.observability import log_action, LogLevel
        log_level = LogLevel.DEBUG  # Default to DEBUG for metrics
        if level == "INFO":
            log_level = LogLevel.INFO
        elif level == "WARNING":
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
        logger.info(message)  # Default to info for metrics


@dataclass
class MetricPoint:
    """A single metric measurement point."""
    name: str
    value: float
    timestamp: float
    tags: Dict[str, str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = {}


@dataclass
class MetricSeries:
    """Time series data for a metric."""
    name: str
    points: deque[MetricPoint] = field(default_factory=lambda: deque(maxlen=1000))
    max_points: int = 1000  # Keep last 1000 points

    def add_point(self, value: float, timestamp: Optional[float] = None, tags: Optional[Dict[str, str]] = None) -> None:
        """Add a new data point to the series."""
        if timestamp is None:
            timestamp = time.time()

        if tags is None:
            tags = {}

        point = MetricPoint(self.name, value, timestamp, tags)
        self.points.append(point)

        # Maintain max points limit
        while len(self.points) > self.max_points:
            self.points.popleft()

    def get_recent_points(self, seconds: int) -> List[MetricPoint]:
        """Get points from the last N seconds."""
        cutoff = time.time() - seconds
        return [p for p in self.points if p.timestamp >= cutoff]

    def get_aggregated_value(self, seconds: int, aggregation: str = "avg") -> Optional[float]:
        """Get aggregated value over the last N seconds."""
        points = self.get_recent_points(seconds)
        if not points:
            return None

        values = [p.value for p in points]

        if aggregation == "avg":
            return sum(values) / len(values)
        elif aggregation == "sum":
            return sum(values)
        elif aggregation == "min":
            return min(values)
        elif aggregation == "max":
            return max(values)
        elif aggregation == "count":
            return len(values)
        else:
            return None


class MetricsCollector:
    """
    Comprehensive metrics collection and aggregation system.

    Collects various types of metrics:
    - Counters: Monotonically increasing values (requests served, errors)
    - Gauges: Point-in-time values (queue depth, memory usage)
    - Histograms: Distribution of values (request latency, response sizes)
    - Timers: Duration measurements (operation time)
    """

    def __init__(self):
        # Metric storage
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, MetricSeries] = {}
        self.timers: Dict[str, MetricSeries] = {}

        # Background cleanup thread
        self.cleanup_interval = 3600  # Clean up old data every hour
        self.cleanup_thread: Optional[threading.Thread] = None
        self.running = False

    def start_collection(self) -> None:
        """Start the metrics collection system."""
        if self.running:
            return

        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()

        _safe_log_action("INFO", "metrics_collector", "collection_started")

    def stop_collection(self) -> None:
        """Stop the metrics collection system."""
        self.running = False
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)

        _safe_log_action("INFO", "metrics_collector", "collection_stopped")

    def increment_counter(self, name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter metric."""
        self.counters[name] += value

        # Also record as a point in histogram for rate calculations
        if name not in self.histograms:
            self.histograms[name] = MetricSeries(name)

        self.histograms[name].add_point(float(value), tags=tags)

        _safe_log_action(
            "DEBUG",
            "metrics_collector",
            "counter_incremented",
            metric_name=name,
            value=value,
            total=self.counters[name]
        )

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge metric to a specific value."""
        self.gauges[name] = value

        # Also record in histogram for historical tracking
        if name not in self.histograms:
            self.histograms[name] = MetricSeries(name)

        self.histograms[name].add_point(value, tags=tags)

        _safe_log_action(
            "DEBUG",
            "metrics_collector",
            "gauge_set",
            metric_name=name,
            value=value
        )

    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a value in a histogram metric."""
        if name not in self.histograms:
            self.histograms[name] = MetricSeries(name)

        self.histograms[name].add_point(value, tags=tags)

        _safe_log_action(
            "DEBUG",
            "metrics_collector",
            "histogram_recorded",
            metric_name=name,
            value=value
        )

    def start_timer(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """
        Start a timer and return a timer ID.

        Returns:
            Timer ID to use with end_timer()
        """
        timer_id = f"{name}_{int(time.time() * 1000000)}"

        # Store start time in a special gauge
        start_gauge_name = f"{name}_start_{timer_id}"
        self.set_gauge(start_gauge_name, time.time(), tags)

        _safe_log_action(
            "DEBUG",
            "metrics_collector",
            "timer_started",
            metric_name=name,
            timer_id=timer_id
        )

        return timer_id

    def end_timer(self, name: str, timer_id: str, tags: Optional[Dict[str, str]] = None) -> float:
        """
        End a timer and record the duration.

        Args:
            name: Timer metric name
            timer_id: Timer ID returned by start_timer()
            tags: Additional tags for the measurement

        Returns:
            Duration in seconds
        """
        start_gauge_name = f"{name}_start_{timer_id}"
        start_time = self.gauges.get(start_gauge_name)

        if start_time is None:
            log_action(
                LogLevel.WARNING,
                "metrics_collector",
                "timer_end_failed",
                metric_name=name,
                timer_id=timer_id,
                reason="start_time_not_found"
            )
            return 0.0

        duration = time.time() - start_time

        # Clean up start gauge
        if start_gauge_name in self.gauges:
            del self.gauges[start_gauge_name]

        # Record the duration
        if name not in self.timers:
            self.timers[name] = MetricSeries(name)

        self.timers[name].add_point(duration, tags=tags)

        _safe_log_action(
            "DEBUG",
            "metrics_collector",
            "timer_ended",
            metric_name=name,
            timer_id=timer_id,
            duration=duration
        )

        return duration

    def get_counter_value(self, name: str) -> int:
        """Get the current value of a counter."""
        return self.counters.get(name, 0)

    def get_gauge_value(self, name: str) -> Optional[float]:
        """Get the current value of a gauge."""
        return self.gauges.get(name)

    def get_histogram_stats(self, name: str, seconds: int = 300) -> Optional[Dict[str, float]]:
        """Get statistics for a histogram over the last N seconds."""
        if name not in self.histograms:
            return None

        series = self.histograms[name]
        points = series.get_recent_points(seconds)

        if not points:
            return None

        values = [p.value for p in points]

        return {
            "count": len(values),
            "sum": sum(values),
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
        }

    def get_timer_stats(self, name: str, seconds: int = 300) -> Optional[Dict[str, float]]:
        """Get statistics for a timer over the last N seconds."""
        if name not in self.timers:
            return None

        series = self.timers[name]
        points = series.get_recent_points(seconds)

        if not points:
            return None

        values = [p.value for p in points]

        return {
            "count": len(values),
            "sum": sum(values),
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "p95": sorted(values)[int(len(values) * 0.95)] if values else None,
            "p99": sorted(values)[int(len(values) * 0.99)] if values else None,
        }

    def get_all_metrics_summary(self) -> Dict[str, Any]:
        """Get a comprehensive summary of all metrics."""
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {
                name: self.get_histogram_stats(name, 300)
                for name in self.histograms.keys()
            },
            "timers": {
                name: self.get_timer_stats(name, 300)
                for name in self.timers.keys()
            },
            "timestamp": time.time(),
        }

    def get_rate(self, metric_name: str, window_seconds: int = 300) -> float:
        """
        Calculate the rate (per second) of a counter or histogram metric.

        Args:
            metric_name: Name of the metric
            window_seconds: Time window to calculate over

        Returns:
            Rate per second
        """
        if metric_name in self.histograms:
            series = self.histograms[metric_name]
            points = series.get_recent_points(window_seconds)

            if len(points) < 2:
                return 0.0

            # Calculate total value over the window
            total_value = sum(p.value for p in points)
            time_span = points[-1].timestamp - points[0].timestamp

            if time_span <= 0:
                return 0.0

            return total_value / time_span

        return 0.0

    def _cleanup_loop(self) -> None:
        """Background cleanup of old metric data."""
        while self.running:
            try:
                time.sleep(self.cleanup_interval)
                self._cleanup_old_data()
            except Exception as e:
                _safe_log_action(
                    "ERROR",
                    "metrics_collector",
                    "cleanup_error",
                    error_message=str(e)
                )

    def _cleanup_old_data(self) -> None:
        """Clean up old metric data to prevent memory bloat."""
        cutoff_time = time.time() - (24 * 3600)  # Keep last 24 hours

        # Clean up histogram points
        for series in self.histograms.values():
            while series.points and series.points[0].timestamp < cutoff_time:
                series.points.popleft()

        # Clean up timer points
        for series in self.timers.values():
            while series.points and series.points[0].timestamp < cutoff_time:
                series.points.popleft()

        log_action(
            LogLevel.DEBUG,
            "metrics_collector",
            "cleanup_completed",
            histograms_cleaned=sum(len(s.points) for s in self.histograms.values()),
            timers_cleaned=sum(len(s.points) for s in self.timers.values())
        )


# Global metrics collector instance
metrics_collector = MetricsCollector()


# Convenience functions for common metric operations

def record_request_duration(endpoint: str, duration: float, status: str = "success") -> None:
    """Record the duration of an API request."""
    metrics_collector.record_histogram(
        "request_duration",
        duration,
        tags={"endpoint": endpoint, "status": status}
    )


def record_event_publish(success: bool = True) -> None:
    """Record an event publish operation."""
    if success:
        metrics_collector.increment_counter("event_publish_success")
    else:
        metrics_collector.increment_counter("event_publish_failures")


def record_tool_execution(tool_name: str, success: bool = True, duration: Optional[float] = None) -> None:
    """Record a tool execution."""
    if success:
        metrics_collector.increment_counter("tool_execution_success", tags={"tool": tool_name})
    else:
        metrics_collector.increment_counter("tool_execution_failures", tags={"tool": tool_name})

    if duration is not None:
        metrics_collector.record_histogram(
            "tool_execution_duration",
            duration,
            tags={"tool": tool_name, "success": str(success)}
        )


def record_routing_decision(agent_name: str, confidence: float, success: bool = True) -> None:
    """Record a routing decision."""
    metrics_collector.record_histogram(
        "routing_confidence",
        confidence,
        tags={"agent": agent_name, "success": str(success)}
    )


def update_queue_depth(queue_name: str, depth: int) -> None:
    """Update the depth of a processing queue."""
    metrics_collector.set_gauge(f"queue_depth_{queue_name}", float(depth))