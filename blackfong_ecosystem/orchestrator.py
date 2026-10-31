"""
O.B.E.O.S Orchestration Engine

Phase 4: Intelligent Orchestration
Coordinates multi-agent workflows by analyzing events and routing work to appropriate agents.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

try:
    import chromadb
except Exception:
    chromadb = None
from blackfong_ecosystem.agent import AgentType
from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.agents.lilo_agent import LiloAgent
from blackfong_ecosystem.agents.stitch_agent import StitchAgent
from blackfong_ecosystem.agents.coding_agent import CodingAgent
from blackfong_ecosystem.config import paths
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.decision_quality_control import decision_quality_controller


class LazyVoiceAgent:
    """Delay heavy voice initialization until voice work is actually requested."""

    def __init__(self, event_client: EventClient):
        self._event_client = event_client
        self._agent: Optional[Any] = None

    def _get_agent(self):
        if self._agent is None:
            from blackfong_ecosystem.agents.voice_agent import VoiceAgent

            self._agent = VoiceAgent(event_client=self._event_client)
        return self._agent

    def process(self, *args, **kwargs):
        return self._get_agent().process(*args, **kwargs)

    def __getattr__(self, item: str):
        return getattr(self._get_agent(), item)


class SemanticSearch:
    """Semantic search over reasoning traces using ChromaDB."""

    def __init__(self, persist_directory: Optional[str] = None):
        self.client = None
        self.collection = None
        self.unavailable_reason = None
        if chromadb is None:
            self.unavailable_reason = "ChromaDB is unavailable; semantic trace search is disabled."
            return
        persist_directory = persist_directory or str(paths.vector_db_path)
        client_kwargs: Dict[str, Any] = {"path": persist_directory}
        try:
            from chromadb.config import Settings

            telemetry_impl = "blackfong_ecosystem.storage.BlackfongNullProductTelemetry"
            client_kwargs["settings"] = Settings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl=telemetry_impl,
                chroma_telemetry_impl=telemetry_impl,
            )
        except Exception:
            pass
        self.client = chromadb.PersistentClient(**client_kwargs)
        self.collection = self.client.get_or_create_collection(
            name="reasoning_traces",
            metadata={"description": "Agent reasoning traces for semantic search"}
        )

    def store_reasoning_trace(self, trace_event_id: str, trace_data: Dict[str, Any]) -> None:
        """Store a reasoning trace for semantic search."""
        # Create searchable content from the trace
        content_parts = [
            f"Agent: {trace_data.get('agent_name', 'unknown')}",
            f"User Input: {trace_data.get('user_input', '')}",
            f"Final Decision: {trace_data.get('final_decision', '')}",
        ]

        # Add reasoning steps
        for step in trace_data.get('steps', []):
            content_parts.append(f"Step {step.get('step_id', '')}: {step.get('action', '')}")

        searchable_content = " | ".join(content_parts)

        # Store in ChromaDB
        if self.collection is None:
            return
        self.collection.add(
            documents=[searchable_content],
            metadatas=[{
                "event_id": trace_event_id,
                "timestamp": trace_data.get('timestamp', 0),
                "confidence": trace_data.get('overall_confidence', 0.0),
                "user_input": trace_data.get('user_input', ''),
                "final_decision": trace_data.get('final_decision', ''),
            }],
            ids=[trace_event_id]
        )

    def search_similar_traces(
        self,
        query: str,
        agent_name: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for similar reasoning traces."""
        where_clause = {}
        if agent_name:
            where_clause["agent_name"] = agent_name

        if self.collection is None:
            return []
        results = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where=where_clause if where_clause else None
        )

        # Format results
        search_results = []
        for i, doc in enumerate(results['documents'][0]):
            metadata = results['metadatas'][0][i]
            search_results.append({
                "event_id": metadata["event_id"],
                "agent_name": metadata["agent_name"],
                "user_input": metadata["user_input"],
                "final_decision": metadata["final_decision"],
                "confidence": metadata["confidence"],
                "timestamp": metadata["timestamp"],
                "similarity_score": results['distances'][0][i] if 'distances' in results else None,
            })

        return search_results


class WorkflowTracker:
    """Tracks multi-agent workflows using correlation IDs."""

    def __init__(self):
        self.active_workflows: Dict[str, Dict[str, Any]] = {}
        self.workflow_events: Dict[str, List[Dict[str, Any]]] = {}

    def start_workflow(self, correlation_id: str, initial_event: Dict[str, Any]) -> None:
        """Start a new workflow."""
        self.active_workflows[correlation_id] = {
            "started_at": time.time(),
            "initial_event": initial_event,
            "agents_involved": set(),
            "status": "active",
        }
        self.workflow_events[correlation_id] = [initial_event]

    def add_workflow_event(self, correlation_id: str, event: Dict[str, Any]) -> None:
        """Add an event to an existing workflow."""
        if correlation_id in self.workflow_events:
            self.workflow_events[correlation_id].append(event)
            # Track which agents are involved
            if "source" in event:
                self.active_workflows[correlation_id]["agents_involved"].add(event["source"])

    def get_workflow_summary(self, correlation_id: str) -> Optional[Dict[str, Any]]:
        """Get summary of a workflow."""
        if correlation_id not in self.active_workflows:
            return None

        workflow = self.active_workflows[correlation_id]
        events = self.workflow_events.get(correlation_id, [])

        return {
            "correlation_id": correlation_id,
            "status": workflow["status"],
            "started_at": workflow["started_at"],
            "duration": time.time() - workflow["started_at"],
            "agents_involved": list(workflow["agents_involved"]),
            "event_count": len(events),
            "initial_event": workflow["initial_event"],
            "last_event": events[-1] if events else None,
        }

    def complete_workflow(self, correlation_id: str) -> None:
        """Mark a workflow as completed."""
        if correlation_id in self.active_workflows:
            self.active_workflows[correlation_id]["status"] = "completed"
            self.active_workflows[correlation_id]["completed_at"] = time.time()


class Orchestrator:
    """
    O.B.E.O.S Orchestration Engine

    Monitors events, analyzes content, and coordinates multi-agent workflows.
    """

    def __init__(
        self,
        event_api_url: str = "http://localhost:8000",
        chroma_persist_dir: Optional[str] = None
    ):
        self.event_client = EventClient(api_url=event_api_url)
        self.semantic_search = SemanticSearch(persist_directory=chroma_persist_dir)
        self.workflow_tracker = WorkflowTracker()

        # Initialize agents
        self.agents = {
            "jarvis": JARVISAgent(event_client=self.event_client),
            "lilo": LiloAgent(event_client=self.event_client),
            "stitch": StitchAgent(event_client=self.event_client),
            "coding": CodingAgent(event_client=self.event_client),
            "voice": LazyVoiceAgent(event_client=self.event_client),
        }

        # Agent routing rules
        self.routing_rules = {
            AgentType.REASONING: ["jarvis"],
            AgentType.TRAINING: ["lilo", "stitch"],
        }

        # Keywords for agent selection
        self.agent_keywords = {
            "jarvis": [
                "help", "assist", "general", "question", "analyze", "explain",
                "status", "system", "tools", "capabilities", "how", "what", "why"
            ],
            "lilo": [
                "certification", "exam", "test", "quiz", "study", "learn",
                "pcep", "python", "certificate", "training", "practice"
            ],
            "stitch": [
                "code", "programming", "exercise", "python", "tutorial",
                "learn", "practice", "coding", "debug", "function", "class"
            ],
            "coding": [
                "review", "debug", "optimize", "refactor", "clean", "bug",
                "test", "unittest", "pytest", "architecture", "design pattern",
                "performance", "quality", "lint", "format"
            ],
            "voice": [
                "open", "launch", "start", "run", "vscode", "code", "editor",
                "browser", "chrome", "firefox", "web", "terminal", "cmd", "command",
                "screenshot", "capture", "screen", "status", "system", "info"
            ]
        }

        self.monitoring = False

    def _route_request(self, user_input: str) -> Optional[str]:
        """Determine which agent should handle the request based on content."""
        lower_input = user_input.lower()

        # Score each agent based on keyword matches
        scores = {}
        for agent_name, keywords in self.agent_keywords.items():
            score = sum(1 for keyword in keywords if keyword in lower_input)
            scores[agent_name] = score

        # Return the agent with the highest score, or jarvis as default
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)
        else:
            return "jarvis"  # Default to general reasoning

    def _calculate_routing_confidence(self, user_input: str, selected_agent: str) -> float:
        """
        Calculate confidence score for routing decision.

        This is a simplified implementation. In production, this would use
        machine learning models for more accurate confidence scoring.
        """
        lower_input = user_input.lower()

        # Get keyword match score
        keywords = self.agent_keywords.get(selected_agent, [])
        keyword_matches = sum(1 for keyword in keywords if keyword in lower_input)

        # Calculate confidence based on keyword matches
        if not keywords:
            base_confidence = 0.5  # Default confidence for agents without keywords
        else:
            match_ratio = keyword_matches / len(keywords)
            base_confidence = min(0.9, 0.3 + (match_ratio * 0.6))  # Scale from 0.3 to 0.9

        # Jarvis is the default general-chat path, so generic conversation
        # should not be treated as low-confidence specialist routing.
        if selected_agent == "jarvis":
            greeting_markers = ["hello", "hi", "hey", "how are you", "help", "status", "what", "why", "how"]
            if keyword_matches == 0:
                base_confidence = max(base_confidence, 0.72)
            if any(marker in lower_input for marker in greeting_markers):
                base_confidence = max(base_confidence, 0.78)

        # Boost confidence for exact matches or clear intent
        if any(keyword in lower_input for keyword in ["help", "assist", "please"]):
            base_confidence = min(0.95, base_confidence + 0.1)

        # Reduce confidence for ambiguous inputs
        if len(user_input.split()) < 3:
            base_confidence *= 0.8  # Short inputs are less confident

        return round(base_confidence, 2)

    def search_reasoning_history(
        self,
        query: str,
        agent_name: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search reasoning traces semantically."""
        return self.semantic_search.search_similar_traces(query, agent_name, limit)

    def get_workflow_status(self, correlation_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a workflow."""
        return self.workflow_tracker.get_workflow_summary(correlation_id)

    def list_active_workflows(self) -> List[Dict[str, Any]]:
        """List all active workflows."""
        return [
            self.workflow_tracker.get_workflow_summary(corr_id)
            for corr_id in self.workflow_tracker.active_workflows.keys()
            if self.workflow_tracker.active_workflows[corr_id]["status"] == "active"
        ]

    def process_user_request(
        self,
        user_input: str,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Direct API for processing user requests.

        Returns the orchestrated response.
        """
        # Create correlation ID if not provided
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Determine target agent
        target_agent = self._route_request(user_input)

        if not target_agent or target_agent not in self.agents:
            return {
                "error": "No suitable agent found for request",
                "correlation_id": correlation_id,
            }

        # Get routing confidence (simplified - in real implementation this would come from ML model)
        # For now, use keyword matching score as confidence proxy
        confidence_score = self._calculate_routing_confidence(user_input, target_agent)

        # Evaluate decision quality
        quality_evaluation = decision_quality_controller.evaluate_decision_quality(
            correlation_id=correlation_id,
            user_input=user_input,
            selected_agent=target_agent,
            confidence_score=confidence_score,
            decision_type="standard_routing"
        )

        # Handle quality requirements
        final_agent = target_agent
        secondary_evaluation_result = None
        consensus_result = None

        if not quality_evaluation["approved"]:
            # Decision not approved - apply quality measures
            if quality_evaluation["requires_secondary_evaluation"]:
                secondary_evaluation_result = decision_quality_controller.perform_secondary_evaluation(
                    correlation_id=correlation_id,
                    user_input=user_input,
                    primary_agent=target_agent,
                    primary_confidence=confidence_score
                )
                # Use secondary recommendation if confidence is higher
                if (secondary_evaluation_result.get("secondary_confidence", 0) >
                    confidence_score * 1.2):  # 20% improvement threshold
                    final_agent = secondary_evaluation_result["secondary_agent"]

            if quality_evaluation["requires_multi_agent_consensus"]:
                consensus_result = decision_quality_controller.perform_multi_agent_consensus(
                    correlation_id=correlation_id,
                    user_input=user_input,
                    agents_to_consult=["jarvis", "lilo", "stitch"]
                )
                # Use consensus agent if different from current selection
                if consensus_result.get("consensus_agent"):
                    final_agent = consensus_result["consensus_agent"]

        # Process with final selected agent
        context = {
            "correlation_id": correlation_id,
            "session_id": session_id,
            "quality_evaluation": quality_evaluation,
            "secondary_evaluation": secondary_evaluation_result,
            "consensus_result": consensus_result,
        }

        start_time = time.time()
        result = self.agents[final_agent].process(user_input, context)
        execution_duration = time.time() - start_time

        # Record execution result for quality analysis
        execution_success = result.get("success", True)  # Assume success unless explicitly failed
        decision_quality_controller.record_execution_result(
            correlation_id=correlation_id,
            execution_success=execution_success,
            execution_duration=execution_duration,
            user_feedback=result.get("user_feedback")
        )

        # Start workflow tracking
        request_event = {
            "id": str(uuid.uuid4()),
            "event_type": "user_request",
            "payload": {"user_input": user_input},
            "correlation_id": correlation_id,
        }
        self.workflow_tracker.start_workflow(correlation_id, request_event)

        return {
            "response": result.get("response"),
            "agent": final_agent,
            "confidence": confidence_score,
            "correlation_id": correlation_id,
            "trace_event_id": result.get("trace_event_id"),
            "quality_evaluation": quality_evaluation,
            "secondary_evaluation": secondary_evaluation_result,
            "consensus_result": consensus_result,
            "execution_success": execution_success,
            "execution_duration": execution_duration,
        }


# Alias for backward compatibility
Orchestrator.process_query = lambda self, query: self.process_user_request(query)


# Global orchestrator instance
try:
    orchestrator = Orchestrator()
except Exception as e:
    print(f"Warning: Could not initialize global orchestrator: {e}")
    orchestrator = None
