"""
Test suite for Decision Quality Control (Phase 5.3)

Tests confidence thresholding, secondary evaluation, multi-agent consensus,
and routing analytics functionality.
"""

import time
import pytest
from unittest.mock import patch

from blackfong_ecosystem.decision_quality_control import (
    DecisionQualityController,
    DecisionRecord,
    QualityThreshold,
    decision_quality_controller
)


class TestDecisionQualityController:
    """Test the DecisionQualityController class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.controller = DecisionQualityController(confidence_threshold=0.8)
        # Clear any existing decision history
        self.controller.decision_history.clear()
        self.controller.routing_accuracy_stats.clear()
        self.controller.agent_performance_stats.clear()

    def test_initialization(self):
        """Test controller initialization."""
        assert self.controller.confidence_threshold == 0.8
        assert len(self.controller.decision_history) == 0
        assert len(self.controller.quality_thresholds) == 4  # standard, complex, critical, fallback
        assert not self.controller.analytics_active

    def test_default_thresholds(self):
        """Test default quality thresholds are properly configured."""
        thresholds = self.controller.quality_thresholds

        # Standard routing
        standard = thresholds["standard_routing"]
        assert standard.min_confidence == 0.7
        assert not standard.requires_secondary_evaluation
        assert not standard.requires_multi_agent_consensus
        assert not standard.escalation_required

        # Complex query
        complex_query = thresholds["complex_query"]
        assert complex_query.min_confidence == 0.8
        assert complex_query.requires_secondary_evaluation
        assert not complex_query.requires_multi_agent_consensus
        assert not complex_query.escalation_required

        # Critical action
        critical = thresholds["critical_action"]
        assert critical.min_confidence == 0.9
        assert critical.requires_secondary_evaluation
        assert critical.requires_multi_agent_consensus
        assert critical.escalation_required

    def test_evaluate_decision_quality_high_confidence(self):
        """Test evaluation of high-confidence decision."""
        result = self.controller.evaluate_decision_quality(
            correlation_id="test-123",
            user_input="Hello world",
            selected_agent="jarvis",
            confidence_score=0.9
        )

        assert result["approved"] is True
        assert result["confidence_score"] == 0.9
        assert result["threshold_used"] == "standard_routing"
        assert result["min_confidence_required"] == 0.7
        assert not result["requires_secondary_evaluation"]
        assert not result["requires_multi_agent_consensus"]
        assert not result["escalation_required"]
        assert result["reason"] == "decision_approved"

        # Check decision record was stored
        assert len(self.controller.decision_history) == 1
        record = self.controller.decision_history[0]
        assert record.correlation_id == "test-123"
        assert record.selected_agent == "jarvis"
        assert record.confidence_score == 0.9

    def test_evaluate_decision_quality_low_confidence(self):
        """Test evaluation of low-confidence decision."""
        result = self.controller.evaluate_decision_quality(
            correlation_id="test-456",
            user_input="Complex query here",
            selected_agent="lilo",
            confidence_score=0.5
        )

        assert result["approved"] is False
        assert result["confidence_score"] == 0.5
        assert result["reason"] == "confidence_below_threshold_standard_routing"
        assert result["requires_secondary_evaluation"] is True
        assert result["requires_multi_agent_consensus"] is True
        assert result["escalation_required"] is True

    def test_evaluate_decision_quality_critical_action(self):
        """Test evaluation of critical action decision."""
        result = self.controller.evaluate_decision_quality(
            correlation_id="test-789",
            user_input="Delete all data",
            selected_agent="jarvis",
            confidence_score=0.95,
            decision_type="critical_action"
        )

        assert result["approved"] is True
        assert result["threshold_used"] == "critical_action"
        assert result["min_confidence_required"] == 0.9
        assert result["requires_secondary_evaluation"] is True
        assert result["requires_multi_agent_consensus"] is True
        assert result["escalation_required"] is True

    def test_secondary_evaluation(self):
        """Test secondary evaluation functionality."""
        # First create a decision record
        self.controller.evaluate_decision_quality(
            correlation_id="test-sec-123",
            user_input="Test query",
            selected_agent="jarvis",
            confidence_score=0.7
        )

        # Perform secondary evaluation
        result = self.controller.perform_secondary_evaluation(
            correlation_id="test-sec-123",
            user_input="Test query",
            primary_agent="jarvis",
            primary_confidence=0.7
        )

        assert "secondary_agent" in result
        assert "secondary_confidence" in result
        assert "analysis" in result
        assert "recommendation" in result

        # Check decision record was updated
        record = self.controller.decision_history[0]
        assert record.secondary_evaluation is not None
        assert "secondary_agent" in record.secondary_evaluation
        assert "analysis" in record.secondary_evaluation

    def test_multi_agent_consensus(self):
        """Test multi-agent consensus functionality."""
        # First create a decision record
        self.controller.evaluate_decision_quality(
            correlation_id="test-cons-123",
            user_input="Consensus test query",
            selected_agent="jarvis",
            confidence_score=0.8
        )

        # Perform multi-agent consensus
        result = self.controller.perform_multi_agent_consensus(
            correlation_id="test-cons-123",
            user_input="Consensus test query",
            agents_to_consult=["jarvis", "lilo", "stitch"]
        )

        assert "consensus_agent" in result
        assert "consensus_confidence" in result
        assert "average_confidence" in result
        assert "agreement_level" in result
        assert "agent_responses" in result

        # Check decision record was updated
        record = self.controller.decision_history[0]
        assert record.multi_agent_consensus is not None
        assert "consensus_agent" in record.multi_agent_consensus

    def test_record_execution_result(self):
        """Test recording execution results."""
        # Create a decision record
        self.controller.evaluate_decision_quality(
            correlation_id="test-exec-123",
            user_input="Execution test",
            selected_agent="jarvis",
            confidence_score=0.85
        )

        # Record successful execution
        self.controller.record_execution_result(
            correlation_id="test-exec-123",
            execution_success=True,
            execution_duration=2.5,
            user_feedback={"rating": 5, "comments": "Great response"}
        )

        # Check record was updated
        record = self.controller.decision_history[0]
        assert record.execution_success is True
        assert record.execution_duration == 2.5
        assert record.user_feedback["rating"] == 5

        # Check analytics were updated
        agent_stats = self.controller.agent_performance_stats["jarvis"]
        assert agent_stats["success"] == 1
        assert agent_stats["failure"] == 0
        assert agent_stats["total"] == 1

    def test_get_quality_metrics_empty(self):
        """Test quality metrics with no data."""
        metrics = self.controller.get_quality_metrics()
        assert metrics["error"] == "no_decision_data"

    def test_get_quality_metrics_with_data(self):
        """Test quality metrics with decision data."""
        # Add some test decisions
        self.controller.evaluate_decision_quality("test-1", "input1", "jarvis", 0.9)  # 1 record
        self.controller.evaluate_decision_quality("test-2", "input2", "lilo", 0.6)   # 2 records (original + fallback)
        self.controller.evaluate_decision_quality("test-3", "input3", "jarvis", 0.8)  # 1 record
        # Total: 4 records

        # Record execution results
        self.controller.record_execution_result("test-1", True, 1.0)
        self.controller.record_execution_result("test-2", False, 2.0)
        self.controller.record_execution_result("test-3", True, 1.5)

        metrics = self.controller.get_quality_metrics()

        assert metrics["total_decisions"] == 4  # Updated expectation
        assert metrics["decisions_with_execution_results"] == 3  # Only 3 have execution results
        assert metrics["successful_executions"] == 2
        assert metrics["failed_executions"] == 1
        assert metrics["success_rate"] == 2/3  # 2 out of 3 with results
        assert abs(metrics["average_confidence"] - 0.725) < 0.01  # (0.9 + 0.6 + 0.8 + 0.6) / 4
        assert metrics["threshold_violations"] == 2  # test-2 and its fallback were below 0.8
        assert metrics["threshold_compliance_rate"] == 0.5  # 2 violations out of 4 decisions

        # Check agent performance
        jarvis_perf = metrics["agent_performance"]["jarvis"]
        assert jarvis_perf["success_rate"] == 1.0  # 2 successes, 0 failures
        assert jarvis_perf["total_decisions"] == 2

        lilo_perf = metrics["agent_performance"]["lilo"]
        assert lilo_perf["success_rate"] == 0.0  # 0 successes, 1 failure
        assert lilo_perf["total_decisions"] == 1

    def test_analytics_loop(self):
        """Test background analytics functionality."""
        # Start analytics
        self.controller.start_analytics()
        assert self.controller.analytics_active
        assert self.controller.analytics_thread is not None
        assert self.controller.analytics_thread.is_alive()

        # Stop analytics
        self.controller.stop_analytics()
        assert not self.controller.analytics_active

        # Thread should stop (may take a moment for daemon thread)
        self.controller.analytics_thread.join(timeout=2)
        # Note: daemon threads may not stop immediately, so we just check the flag


class TestDecisionRecord:
    """Test the DecisionRecord dataclass."""

    def test_creation(self):
        """Test DecisionRecord creation."""
        record = DecisionRecord(
            correlation_id="test-123",
            user_input="Hello",
            selected_agent="jarvis",
            confidence_score=0.85,
            routing_timestamp=1234567890.0
        )

        assert record.correlation_id == "test-123"
        assert record.user_input == "Hello"
        assert record.selected_agent == "jarvis"
        assert record.confidence_score == 0.85
        assert record.routing_timestamp == 1234567890.0
        assert record.execution_timestamp is None
        assert record.execution_success is None
        assert record.execution_duration is None
        assert record.user_feedback == {}
        assert record.secondary_evaluation == {}
        assert record.multi_agent_consensus == {}

    def test_with_optional_fields(self):
        """Test DecisionRecord with optional fields set."""
        record = DecisionRecord(
            correlation_id="test-456",
            user_input="Complex query",
            selected_agent="lilo",
            confidence_score=0.75,
            routing_timestamp=1234567890.0,
            execution_timestamp=1234567891.0,
            execution_success=True,
            execution_duration=2.5,
            user_feedback={"rating": 4},
            secondary_evaluation={"method": "pattern_matching"},
            multi_agent_consensus={"consensus_agent": "jarvis"}
        )

        assert record.execution_timestamp == 1234567891.0
        assert record.execution_success is True
        assert record.execution_duration == 2.5
        assert record.user_feedback == {"rating": 4}
        assert record.secondary_evaluation == {"method": "pattern_matching"}
        assert record.multi_agent_consensus == {"consensus_agent": "jarvis"}


class TestQualityThreshold:
    """Test the QualityThreshold dataclass."""

    def test_creation(self):
        """Test QualityThreshold creation."""
        threshold = QualityThreshold(
            name="test_threshold",
            min_confidence=0.8,
            requires_secondary_evaluation=True,
            requires_multi_agent_consensus=False,
            escalation_required=True,
            description="Test threshold"
        )

        assert threshold.name == "test_threshold"
        assert threshold.min_confidence == 0.8
        assert threshold.requires_secondary_evaluation is True
        assert threshold.requires_multi_agent_consensus is False
        assert threshold.escalation_required is True
        assert threshold.description == "Test threshold"


def test_global_instance():
    """Test the global decision quality controller instance."""
    assert isinstance(decision_quality_controller, DecisionQualityController)
    assert decision_quality_controller.confidence_threshold == 0.8


if __name__ == "__main__":
    pytest.main([__file__])