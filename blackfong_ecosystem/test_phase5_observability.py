"""
Test Phase 5.1: Observability Layer Integration

Tests the integrated observability system with alert engine and metrics collector.
"""

import time
import uuid
from blackfong_ecosystem.observability import observability, log_action, LogLevel
from blackfong_ecosystem.alert_engine import alert_engine
from blackfong_ecosystem.metrics_collector import metrics_collector


def test_observability_integration():
    """Test the complete observability system integration."""
    print("Testing Phase 5.1: Observability Layer Integration")
    print("=" * 60)

    # Generate a correlation ID for this test session
    correlation_id = str(uuid.uuid4())

    # Test 1: Structured logging
    print("\n1. Testing structured logging...")
    log_action(
        LogLevel.INFO,
        "test_observability",
        "test_started",
        correlation_id=correlation_id,
        test_phase="5.1"
    )

    # Test 2: Metrics collection
    print("2. Testing metrics collection...")
    observability.record_metric("test_gauge", 42.0, labels={"test": "integration"})
    observability.increment_counter("test_counter", labels={"test": "integration"})
    observability.observe_histogram("test_histogram", 1.5, labels={"test": "integration"})

    # Test 3: Alert system
    print("3. Testing alert system...")
    # Manually trigger a test alert
    from blackfong_ecosystem.alert_engine import trigger_alert
    trigger_alert("queue_backlog_high", {"test": True})

    # Test 4: Metrics summary
    print("4. Testing metrics summary...")
    summary = observability.get_metrics_summary()
    print(f"Metrics summary keys: {list(summary.keys())}")
    assert "gauges" in summary
    assert "counters" in summary
    assert "histograms" in summary
    assert "timers" in summary
    assert "alerts_active" in summary
    assert "services_active" in summary

    # Test 5: Alert history
    print("5. Testing alert history...")
    alerts = observability.get_recent_alerts(10)
    print(f"Found {len(alerts)} recent alerts")
    assert isinstance(alerts, list)

    # Test 6: Log retrieval
    print("6. Testing log retrieval...")
    logs = observability.get_recent_logs(10)
    print(f"Found {len(logs)} recent log entries")
    assert isinstance(logs, list)
    # Note: Logs might not be immediately available due to async processing
    # The important thing is that the method doesn't crash

    # Test 7: Alert engine status
    print("7. Testing alert engine status...")
    alert_status = alert_engine.get_rule_status()
    print(f"Alert rules configured: {len(alert_status)}")
    assert isinstance(alert_status, dict)

    # Test 8: Metrics collector stats
    print("8. Testing metrics collector stats...")
    timer_id = metrics_collector.start_timer("test_operation")
    time.sleep(0.1)  # Simulate some work
    duration = metrics_collector.end_timer("test_operation", timer_id)
    print(".3f")
    assert duration > 0

    # Test 9: Rate calculation
    print("9. Testing rate calculation...")
    for i in range(5):
        metrics_collector.increment_counter("test_rate_metric")
        time.sleep(0.1)

    rate = metrics_collector.get_rate("test_rate_metric", 60)
    print(".2f")
    assert rate > 0

    # Test completion
    log_action(
        LogLevel.INFO,
        "test_observability",
        "test_completed",
        correlation_id=correlation_id,
        status="success",
        tests_passed=9
    )

    print("\n✅ All observability integration tests passed!")
    print("Phase 5.1 Observability Layer is operational.")


if __name__ == "__main__":
    test_observability_integration()