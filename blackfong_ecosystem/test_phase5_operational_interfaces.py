"""
Test suite for Operational Interfaces (Phase 5.5)

Tests web dashboard, session explorer, replay interface, and admin controls.
"""

import time
import pytest
import json
from unittest.mock import patch, MagicMock

from blackfong_ecosystem.operational_interfaces import (
    WebDashboard,
    SessionExplorer,
    AdminControls,
    DashboardMetrics,
    SessionInfo,
    web_dashboard,
    session_explorer,
    admin_controls
)


class TestWebDashboard:
    """Test the WebDashboard class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.dashboard = WebDashboard(host="localhost", port=8081)  # Use different port for testing

    def test_initialization(self):
        """Test dashboard initialization."""
        assert self.dashboard.host == "localhost"
        assert self.dashboard.port == 8081
        assert isinstance(self.dashboard.metrics, DashboardMetrics)
        assert len(self.dashboard.sessions) == 0
        assert len(self.dashboard.recent_events) == 0
        assert not self.dashboard.update_active

    def test_metrics_update(self):
        """Test metrics update functionality."""
        # Mock the external dependencies
        with patch('blackfong_ecosystem.operational_interfaces.memory_lifecycle_manager') as mock_memory, \
             patch('blackfong_ecosystem.operational_interfaces.decision_quality_controller') as mock_quality:

            mock_memory.get_memory_stats.return_value = {"total_size_mb": 150.5}
            mock_quality.get_quality_metrics.return_value = {"success_rate": 0.85}

            self.dashboard.update_metrics()

            assert self.dashboard.metrics.memory_usage_mb == 150.5
            assert self.dashboard.metrics.decision_quality_score == 0.85
            assert self.dashboard.metrics.system_health == "healthy"  # Should be healthy with good metrics

    def test_metrics_update_uses_short_cache(self):
        """Test that repeated HUD metrics requests reuse a short-lived cache."""
        with patch('blackfong_ecosystem.operational_interfaces.memory_lifecycle_manager') as mock_memory, \
             patch('blackfong_ecosystem.operational_interfaces.decision_quality_controller') as mock_quality:

            self.dashboard._metrics_cache_ttl_s = 60.0
            self.dashboard._get_sessions_from_store = MagicMock(return_value=[])
            self.dashboard._sync_recent_events = MagicMock()
            self.dashboard.event_store = MagicMock()
            self.dashboard.event_store.list_events_by_type.return_value = []
            self.dashboard.event_store.count_events.return_value = 0
            mock_memory.get_memory_stats.return_value = {"total_size_mb": 42.0}
            mock_quality.get_quality_metrics.return_value = {"success_rate": 0.92}

            self.dashboard.update_metrics(force=True)
            self.dashboard.update_metrics()

            assert mock_memory.get_memory_stats.call_count == 1
            assert mock_quality.get_quality_metrics.call_count == 1

    def test_add_event(self):
        """Test adding events to the dashboard."""
        event = {
            "level": "info",
            "message": "Test event",
            "component": "test"
        }

        self.dashboard.add_event(event)

        assert len(self.dashboard.recent_events) == 1
        assert self.dashboard.recent_events[0]["message"] == "Test event"
        assert "timestamp" in self.dashboard.recent_events[0]

    def test_add_alert(self):
        """Test adding alerts to the dashboard."""
        alert = {
            "type": "warning",
            "message": "Test alert",
            "severity": "high"
        }

        self.dashboard.add_alert(alert)

        assert len(self.dashboard.alerts) == 1
        assert self.dashboard.alerts[0]["message"] == "Test alert"
        assert "timestamp" in self.dashboard.alerts[0]

    def test_event_buffer_limits(self):
        """Test that event buffers are properly limited."""
        # Add many events
        for i in range(1100):  # More than the 1000 limit
            self.dashboard.add_event({"message": f"Event {i}"})

        assert len(self.dashboard.recent_events) == 1000

        # Add many alerts
        for i in range(120):  # More than the 100 limit
            self.dashboard.add_alert({"message": f"Alert {i}"})

        assert len(self.dashboard.alerts) == 100


class TestSessionExplorer:
    """Test the SessionExplorer class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.dashboard = WebDashboard()
        self.explorer = SessionExplorer(self.dashboard)

    def test_initialization(self):
        """Test session explorer initialization."""
        assert self.explorer.dashboard == self.dashboard
        assert len(self.explorer.session_workflows) == 0
        assert len(self.explorer.event_replay_buffer) == 0

    def test_track_session_event(self):
        """Test tracking session events."""
        session_id = "test-session-123"
        event = {
            "event_type": "user_request",
            "payload": {"input": "hello"},
            "agent": "jarvis",
            "timestamp": time.time()
        }

        self.explorer.track_session_event(session_id, event)

        # Check session was created
        assert session_id in self.dashboard.sessions
        session = self.dashboard.sessions[session_id]
        assert session.session_id == session_id
        assert session.total_requests == 1
        assert "jarvis" in session.agents_used

        # Check workflow was stored
        assert session_id in self.explorer.session_workflows
        assert len(self.explorer.session_workflows[session_id]) == 1

        # Check replay buffer
        assert session_id in self.explorer.event_replay_buffer
        assert len(self.explorer.event_replay_buffer[session_id]) == 1

    def test_buffer_limits(self):
        """Test that session buffers are properly limited."""
        session_id = "test-session-456"

        # Add many events
        for i in range(1100):  # More than the 1000 limit
            event = {"event_type": f"event_{i}", "payload": {"data": i}}
            self.explorer.track_session_event(session_id, event)

        assert len(self.explorer.session_workflows[session_id]) == 1000
        assert len(self.explorer.event_replay_buffer[session_id]) == 1000

    def test_cleanup_old_sessions(self):
        """Test cleanup of old sessions."""
        # Create some sessions
        old_session = "old-session"
        new_session = "new-session"

        # Add old session (inactive for 48 hours)
        old_time = time.time() - (48 * 60 * 60)
        self.dashboard.sessions[old_session] = SessionInfo(
            session_id=old_session,
            start_time=old_time,
            last_activity=old_time,
            status="inactive"
        )

        # Add new session
        self.dashboard.sessions[new_session] = SessionInfo(
            session_id=new_session,
            status="active"
        )

        # Add some workflow data for old session
        self.explorer.session_workflows[old_session] = [{"test": "data"}]
        self.explorer.event_replay_buffer[old_session] = [{"test": "event"}]

        # Cleanup old sessions (24 hour max age)
        removed_count = self.explorer.cleanup_old_sessions(max_age_hours=24)

        assert removed_count == 1
        assert old_session not in self.dashboard.sessions
        assert old_session not in self.explorer.session_workflows
        assert old_session not in self.explorer.event_replay_buffer
        assert new_session in self.dashboard.sessions


class TestAdminControls:
    """Test the AdminControls class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.dashboard = WebDashboard()
        self.admin = AdminControls(self.dashboard)

    def test_initialization(self):
        """Test admin controls initialization."""
        assert self.admin.dashboard == self.dashboard
        assert len(self.admin.admin_sessions) == 0
        assert "maintenance_mode" in self.admin.system_config
        assert not self.admin.system_config["maintenance_mode"]

    def test_maintenance_mode(self):
        """Test maintenance mode controls."""
        # Start maintenance
        self.admin.system_config["maintenance_mode"] = True
        assert self.admin.is_maintenance_mode()

        # Stop maintenance
        self.admin.system_config["maintenance_mode"] = False
        assert not self.admin.is_maintenance_mode()

    def test_get_system_status(self):
        """Test system status retrieval."""
        status = self.admin.get_system_status()

        assert "maintenance_mode" in status
        assert "config" in status
        assert "active_sessions" in status
        assert "total_memory_items" in status
        assert "decision_quality" in status
        assert "timestamp" in status


class TestDashboardMetrics:
    """Test the DashboardMetrics class."""

    def test_initialization(self):
        """Test metrics initialization."""
        metrics = DashboardMetrics()

        assert metrics.total_requests == 0
        assert metrics.active_sessions == 0
        assert metrics.avg_response_time == 0.0
        assert metrics.error_rate == 0.0
        assert metrics.memory_usage_mb == 0.0
        assert metrics.decision_quality_score == 0.0
        assert metrics.system_health == "healthy"
        assert len(metrics.agent_performance) == 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = DashboardMetrics(
            total_requests=100,
            active_sessions=5,
            avg_response_time=2.5,
            system_health="warning"
        )

        data = metrics.to_dict()

        assert data["total_requests"] == 100
        assert data["active_sessions"] == 5
        assert data["avg_response_time"] == 2.5
        assert data["system_health"] == "warning"
        assert "timestamp" in data


class TestSessionInfo:
    """Test the SessionInfo class."""

    def test_initialization(self):
        """Test session info initialization."""
        session = SessionInfo(session_id="test-123", user_id="user1")

        assert session.session_id == "test-123"
        assert session.user_id == "user1"
        assert session.total_requests == 0
        assert session.total_errors == 0
        assert len(session.agents_used) == 0
        assert session.status == "active"
        assert session.expires_at > session.start_time

    def test_to_dict(self):
        """Test conversion to dictionary."""
        session = SessionInfo(
            session_id="test-456",
            user_id="user2",
            total_requests=10,
            total_errors=1,
            agents_used={"jarvis", "lilo"},
            status="completed"
        )

        data = session.to_dict()

        assert data["session_id"] == "test-456"
        assert data["user_id"] == "user2"
        assert data["total_requests"] == 10
        assert data["total_errors"] == 1
        assert data["agents_used"] == ["jarvis", "lilo"]
        assert data["status"] == "completed"
        assert "duration" in data
        assert "last_activity" in data


def test_global_instances():
    """Test the global operational interface instances."""
    assert isinstance(web_dashboard, WebDashboard)
    assert isinstance(session_explorer, SessionExplorer)
    assert isinstance(admin_controls, AdminControls)


if __name__ == "__main__":
    pytest.main([__file__])
