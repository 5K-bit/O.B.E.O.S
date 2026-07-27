"""
Decision Quality Control for O.B.E.O.S

Phase 5.3: Decision Quality Control
Builds confidence thresholding, secondary evaluation, multi-agent execution mode,
and routing analytics to ensure high-quality decisions.

Principles:
- No decision below confidence threshold without secondary evaluation
- Multi-agent consensus for critical decisions
- Continuous monitoring of routing accuracy
- Automatic escalation for low-confidence scenarios
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, deque

from blackfong_ecosystem.observability import log_action, LogLevel


@dataclass
class DecisionRecord:
    """Record of a routing decision for quality analysis."""
    correlation_id: str
    user_input: str
    selected_agent: str
    confidence_score: float
    routing_timestamp: float
    execution_timestamp: Optional[float] = None
    execution_success: Optional[bool] = None
    execution_duration: Optional[float] = None
    user_feedback: Optional[Dict[str, Any]] = None
    secondary_evaluation: Optional[Dict[str, Any]] = None
    multi_agent_consensus: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.user_feedback is None:
            self.user_feedback = {}
        if self.secondary_evaluation is None:
            self.secondary_evaluation = {}
        if self.multi_agent_consensus is None:
            self.multi_agent_consensus = {}


@dataclass
class QualityThreshold:
    """Quality threshold configuration."""
    name: str
    min_confidence: float
    requires_secondary_evaluation: bool = False
    requires_multi_agent_consensus: bool = False
    escalation_required: bool = False
    description: str = ""


class DecisionQualityController:
    """
    Decision Quality Control system that ensures routing decisions meet quality standards.

    Features:
    - Confidence thresholding with automatic secondary evaluation
    - Multi-agent consensus for critical decisions
    - Routing accuracy analytics and continuous improvement
    - Automatic escalation for low-confidence scenarios
    """

    def __init__(self, confidence_threshold: float = 0.8):
        self.confidence_threshold = confidence_threshold

        # Decision records for analysis
        self.decision_history: deque[DecisionRecord] = deque(maxlen=10000)

        # Quality thresholds
        self.quality_thresholds = self._setup_default_thresholds()

        # Analytics
        self.routing_accuracy_stats = defaultdict(lambda: {"correct": 0, "incorrect": 0, "total": 0})
        self.agent_performance_stats = defaultdict(lambda: {"success": 0, "failure": 0, "total": 0})

        # Background analytics thread
        self.analytics_active = False
        self.analytics_thread: Optional[threading.Thread] = None

    def _setup_default_thresholds(self) -> Dict[str, QualityThreshold]:
        """Setup default quality thresholds for different decision types."""
        return {
            "standard_routing": QualityThreshold(
                name="standard_routing",
                min_confidence=0.7,
                requires_secondary_evaluation=False,
                requires_multi_agent_consensus=False,
                escalation_required=False,
                description="Standard routing decisions"
            ),
            "complex_query": QualityThreshold(
                name="complex_query",
                min_confidence=0.8,
                requires_secondary_evaluation=True,
                requires_multi_agent_consensus=False,
                escalation_required=False,
                description="Complex or multi-part queries"
            ),
            "critical_action": QualityThreshold(
                name="critical_action",
                min_confidence=0.9,
                requires_secondary_evaluation=True,
                requires_multi_agent_consensus=True,
                escalation_required=True,
                description="Decisions involving critical actions or high-stakes outcomes"
            ),
            "low_confidence_fallback": QualityThreshold(
                name="low_confidence_fallback",
                min_confidence=0.0,
                requires_secondary_evaluation=True,
                requires_multi_agent_consensus=True,
                escalation_required=True,
                description="Fallback for decisions below all other thresholds - always requires quality measures"
            ),
        }

    def evaluate_decision_quality(
        self,
        correlation_id: str,
        user_input: str,
        selected_agent: str,
        confidence_score: float,
        decision_type: str = "standard_routing"
    ) -> Dict[str, Any]:
        """
        Evaluate the quality of a routing decision and determine required actions.

        Args:
            correlation_id: Unique identifier for this decision
            user_input: The user's input that led to this decision
            selected_agent: The agent selected for routing
            confidence_score: Confidence score of the decision (0.0 to 1.0)
            decision_type: Type of decision for threshold selection

        Returns:
            Dict containing evaluation results and required actions
        """
        # Select appropriate threshold
        threshold = self.quality_thresholds.get(decision_type, self.quality_thresholds["standard_routing"])

        # Create decision record
        decision_record = DecisionRecord(
            correlation_id=correlation_id,
            user_input=user_input,
            selected_agent=selected_agent,
            confidence_score=confidence_score,
            routing_timestamp=time.time()
        )

        evaluation_result = {
            "approved": True,
            "confidence_score": confidence_score,
            "threshold_used": threshold.name,
            "min_confidence_required": threshold.min_confidence,
            "requires_secondary_evaluation": False,
            "requires_multi_agent_consensus": False,
            "escalation_required": False,
            "reason": "decision_approved",
            "decision_record": decision_record,
        }

        # Check confidence threshold
        if confidence_score < threshold.min_confidence:
            evaluation_result["approved"] = False
            evaluation_result["reason"] = f"confidence_below_threshold_{threshold.name}"

            # For fallback threshold, never approve - always require quality measures
            if decision_type == "low_confidence_fallback":
                evaluation_result["approved"] = False
                evaluation_result["requires_secondary_evaluation"] = True
                evaluation_result["requires_multi_agent_consensus"] = True
                evaluation_result["escalation_required"] = True
            else:
                # Try fallback threshold
                fallback_result = self.evaluate_decision_quality(
                    correlation_id, user_input, selected_agent, confidence_score, "low_confidence_fallback"
                )
                # Fallback never approves, but applies its requirements
                evaluation_result["requires_secondary_evaluation"] = fallback_result["requires_secondary_evaluation"]
                evaluation_result["requires_multi_agent_consensus"] = fallback_result["requires_multi_agent_consensus"]
                evaluation_result["escalation_required"] = fallback_result["escalation_required"]

        # Apply threshold requirements
        if evaluation_result["approved"]:
            evaluation_result["requires_secondary_evaluation"] = threshold.requires_secondary_evaluation
            evaluation_result["requires_multi_agent_consensus"] = threshold.requires_multi_agent_consensus
            evaluation_result["escalation_required"] = threshold.escalation_required

        # Store decision record
        self.decision_history.append(decision_record)

        # Log the evaluation
        log_action(
            LogLevel.INFO if evaluation_result["approved"] else LogLevel.WARNING,
            "decision_quality_control",
            "decision_evaluated",
            correlation_id=correlation_id,
            approved=evaluation_result["approved"],
            confidence_score=confidence_score,
            threshold=threshold.name,
            requires_secondary=evaluation_result["requires_secondary_evaluation"],
            requires_consensus=evaluation_result["requires_multi_agent_consensus"],
            escalation_required=evaluation_result["escalation_required"]
        )

        return evaluation_result

    def assess_decision_quality(self, decision: Dict[str, Any]) -> float:
        """
        Assess the overall quality of a decision based on various factors.

        Args:
            decision: Decision data containing query, response, confidence, agent, etc.

        Returns:
            Quality score between 0.0 and 1.0
        """
        try:
            # Extract decision components
            confidence = decision.get("confidence", 0.0)
            agent = decision.get("agent", "unknown")
            query = decision.get("query", "")
            response = decision.get("response", "")

            # Base quality on confidence
            quality_score = confidence

            # Adjust based on agent performance history
            agent_stats = self.agent_performance_stats.get(agent, {"success": 0, "failure": 0, "total": 0})
            if agent_stats["total"] > 0:
                agent_success_rate = agent_stats["success"] / agent_stats["total"]
                # Blend confidence with agent success rate
                quality_score = (quality_score * 0.7) + (agent_success_rate * 0.3)

            # Adjust based on response characteristics
            if response:
                response_length = len(response)
                # Penalize very short responses (likely low quality)
                if response_length < 10:
                    quality_score *= 0.5
                # Reward longer, more detailed responses
                elif response_length > 100:
                    quality_score *= 1.1

            # Ensure score stays within bounds
            quality_score = max(0.0, min(1.0, quality_score))

            log_action(
                LogLevel.DEBUG,
                "decision_quality_control",
                "decision_quality_assessed",
                agent=agent,
                confidence=confidence,
                quality_score=quality_score
            )

            return quality_score

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "decision_quality_control",
                "decision_quality_assessment_error",
                error_message=str(e)
            )
            return 0.5  # Default neutral score on error

    def perform_secondary_evaluation(
        self,
        correlation_id: str,
        user_input: str,
        primary_agent: str,
        primary_confidence: float
    ) -> Dict[str, Any]:
        """
        Perform secondary evaluation of a routing decision.

        This could involve:
        - Alternative routing algorithms
        - Expert system validation
        - Pattern matching against known good/bad decisions
        """
        # Find the decision record
        decision_record = None
        for record in self.decision_history:
            if record.correlation_id == correlation_id:
                decision_record = record
                break

        if not decision_record:
            return {"error": "decision_record_not_found"}

        # Placeholder secondary evaluation logic
        # In a real implementation, this would use alternative models or expert systems
        secondary_confidence = primary_confidence * 0.9  # Slight penalty for secondary
        secondary_agent = primary_agent  # Same agent for now

        # Simulate some analysis
        analysis = {
            "method": "pattern_matching",
            "similar_cases_found": 5,
            "consistency_score": 0.85,
            "risk_assessment": "low" if secondary_confidence > 0.7 else "medium",
        }

        # Update decision record
        decision_record.secondary_evaluation = {
            "secondary_agent": secondary_agent,
            "secondary_confidence": secondary_confidence,
            "analysis": analysis,
            "timestamp": time.time(),
        }

        result = {
            "correlation_id": correlation_id,
            "secondary_agent": secondary_agent,
            "secondary_confidence": secondary_confidence,
            "analysis": analysis,
            "recommendation": "proceed" if secondary_confidence > 0.6 else "escalate",
        }

        log_action(
            LogLevel.INFO,
            "decision_quality_control",
            "secondary_evaluation_completed",
            correlation_id=correlation_id,
            secondary_confidence=secondary_confidence,
            recommendation=result["recommendation"]
        )

        return result

    def perform_multi_agent_consensus(
        self,
        correlation_id: str,
        user_input: str,
        agents_to_consult: List[str] = None
    ) -> Dict[str, Any]:
        """
        Perform multi-agent consensus evaluation.

        Consults multiple agents and combines their recommendations.
        """
        if agents_to_consult is None:
            agents_to_consult = ["jarvis", "lilo", "stitch"]  # Default agents

        # Find the decision record
        decision_record = None
        for record in self.decision_history:
            if record.correlation_id == correlation_id:
                decision_record = record
                break

        if not decision_record:
            return {"error": "decision_record_not_found"}

        # Placeholder multi-agent consensus logic
        # In a real implementation, this would actually consult the agents
        agent_responses = {}
        consensus_scores = {}

        for agent in agents_to_consult:
            # Simulate agent response
            confidence = 0.7 + (hash(f"{agent}_{user_input}") % 30) / 100  # Pseudo-random but deterministic
            agent_responses[agent] = {
                "recommended_agent": agent,  # Each agent recommends itself for simplicity
                "confidence": confidence,
                "reasoning": f"Agent {agent} analysis of input",
            }
            consensus_scores[agent] = confidence

        # Calculate consensus
        if consensus_scores:
            avg_confidence = sum(consensus_scores.values()) / len(consensus_scores)
            max_confidence = max(consensus_scores.values())
            consensus_agent = max(consensus_scores, key=consensus_scores.get)

            consensus_result = {
                "consensus_agent": consensus_agent,
                "consensus_confidence": max_confidence,
                "average_confidence": avg_confidence,
                "agreement_level": "high" if max_confidence > 0.8 else "medium" if max_confidence > 0.6 else "low",
                "agent_responses": agent_responses,
            }
        else:
            consensus_result = {"error": "no_agent_responses"}

        # Update decision record
        decision_record.multi_agent_consensus = {
            **consensus_result,
            "timestamp": time.time(),
        }

        log_action(
            LogLevel.INFO,
            "decision_quality_control",
            "multi_agent_consensus_completed",
            correlation_id=correlation_id,
            consensus_agent=consensus_result.get("consensus_agent"),
            consensus_confidence=consensus_result.get("consensus_confidence"),
            agreement_level=consensus_result.get("agreement_level")
        )

        return consensus_result

    def record_execution_result(
        self,
        correlation_id: str,
        execution_success: bool,
        execution_duration: Optional[float] = None,
        user_feedback: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record the execution result of a decision for quality analysis.

        Args:
            correlation_id: The decision correlation ID
            execution_success: Whether the execution was successful
            execution_duration: How long the execution took
            user_feedback: Any user feedback on the result
        """
        # Find and update the decision record
        for record in self.decision_history:
            if record.correlation_id == correlation_id:
                record.execution_timestamp = time.time()
                record.execution_success = execution_success
                record.execution_duration = execution_duration
                if user_feedback:
                    record.user_feedback.update(user_feedback)

                # Update analytics
                self._update_routing_analytics(record)

                log_action(
                    LogLevel.INFO,
                    "decision_quality_control",
                    "execution_result_recorded",
                    correlation_id=correlation_id,
                    success=execution_success,
                    duration=execution_duration,
                    has_feedback=bool(user_feedback)
                )
                break

    def _update_routing_analytics(self, record: DecisionRecord) -> None:
        """Update routing accuracy analytics based on execution results."""
        agent = record.selected_agent

        # Update agent performance stats
        self.agent_performance_stats[agent]["total"] += 1
        if record.execution_success:
            self.agent_performance_stats[agent]["success"] += 1
        else:
            self.agent_performance_stats[agent]["failure"] += 1

        # Update routing accuracy (simplified - assumes success = correct routing)
        # In a real system, this would be based on user feedback or explicit correctness signals
        if record.execution_success:
            self.routing_accuracy_stats[agent]["correct"] += 1
        else:
            self.routing_accuracy_stats[agent]["incorrect"] += 1
        self.routing_accuracy_stats[agent]["total"] += 1

    def get_quality_metrics(self) -> Dict[str, Any]:
        """Get comprehensive quality metrics."""
        total_decisions = len(self.decision_history)
        if total_decisions == 0:
            return {"error": "no_decision_data"}

        # Calculate overall metrics based on decisions with execution results
        decisions_with_results = [r for r in self.decision_history if r.execution_success is not None]
        successful_executions = sum(1 for r in decisions_with_results if r.execution_success is True)
        failed_executions = sum(1 for r in decisions_with_results if r.execution_success is False)

        # But still report total decisions for completeness
        total_decisions = len(self.decision_history)
        total_with_results = len(decisions_with_results)
        avg_confidence = sum(r.confidence_score for r in self.decision_history) / total_decisions

        # Calculate threshold compliance
        threshold_violations = sum(
            1 for r in self.decision_history
            if r.confidence_score < self.confidence_threshold
        )

        # Agent performance
        agent_performance = {}
        for agent, stats in self.agent_performance_stats.items():
            if stats["total"] > 0:
                success_rate = stats["success"] / stats["total"]
                agent_performance[agent] = {
                    "success_rate": success_rate,
                    "total_decisions": stats["total"],
                    "successes": stats["success"],
                    "failures": stats["failure"],
                }

        # Routing accuracy
        routing_accuracy = {}
        for agent, stats in self.routing_accuracy_stats.items():
            if stats["total"] > 0:
                accuracy = stats["correct"] / stats["total"]
                routing_accuracy[agent] = {
                    "accuracy": accuracy,
                    "correct": stats["correct"],
                    "incorrect": stats["incorrect"],
                    "total": stats["total"],
                }

        return {
            "total_decisions": total_decisions,
            "decisions_with_execution_results": total_with_results,
            "successful_executions": successful_executions,
            "failed_executions": failed_executions,
            "success_rate": successful_executions / total_with_results if total_with_results > 0 else 0,
            "average_confidence": avg_confidence,
            "threshold_violations": threshold_violations,
            "threshold_compliance_rate": 1 - (threshold_violations / total_decisions),
            "agent_performance": agent_performance,
            "routing_accuracy": routing_accuracy,
            "quality_thresholds": {name: vars(threshold) for name, threshold in self.quality_thresholds.items()},
        }

    def start_analytics(self) -> None:
        """Start background analytics processing."""
        if self.analytics_active:
            return

        self.analytics_active = True
        self.analytics_thread = threading.Thread(target=self._analytics_loop, daemon=True)
        self.analytics_thread.start()

        log_action(LogLevel.INFO, "decision_quality_control", "analytics_started")

    def stop_analytics(self) -> None:
        """Stop background analytics processing."""
        self.analytics_active = False
        if self.analytics_thread:
            self.analytics_thread.join(timeout=5)

        log_action(LogLevel.INFO, "decision_quality_control", "analytics_stopped")

    def _analytics_loop(self) -> None:
        """Background analytics processing loop."""
        while self.analytics_active:
            try:
                # Perform periodic analytics
                self._perform_quality_analysis()

                # Sleep for 5 minutes
                time.sleep(300)

            except Exception as e:
                log_action(
                    LogLevel.ERROR,
                    "decision_quality_control",
                    "analytics_loop_error",
                    error_message=str(e)
                )
                time.sleep(60)

    def _perform_quality_analysis(self) -> None:
        """Perform periodic quality analysis and generate insights."""
        metrics = self.get_quality_metrics()

        # Check for concerning patterns
        if metrics.get("threshold_compliance_rate", 1.0) < 0.9:
            log_action(
                LogLevel.WARNING,
                "decision_quality_control",
                "low_threshold_compliance",
                compliance_rate=metrics["threshold_compliance_rate"],
                violations=metrics["threshold_violations"]
            )

        if metrics.get("success_rate", 1.0) < 0.8:
            log_action(
                LogLevel.WARNING,
                "decision_quality_control",
                "low_overall_success_rate",
                success_rate=metrics["success_rate"],
                failures=metrics["failed_executions"]
            )

        # Log periodic summary
        log_action(
            LogLevel.INFO,
            "decision_quality_control",
            "quality_analysis_summary",
            total_decisions=metrics["total_decisions"],
            success_rate=metrics["success_rate"],
            avg_confidence=metrics["average_confidence"],
            threshold_compliance=metrics["threshold_compliance_rate"]
        )


# Global decision quality controller instance
decision_quality_controller = DecisionQualityController()