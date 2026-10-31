"""
Unified O.B.E.O.S Orchestrator

Single entry point for all capabilities:
- Replaces JARVIS-MARK-5 as the intelligence layer
- Routes requests to appropriate agents
- Maintains backward compatibility
- Handles multiple modalities (text, voice, vision)

Architecture:
- O.B.E.O.S is the control plane (brain)
- Agents are specialists (reasoning, coding, vision, voice)
- Events provide full observability
- Legacy Jarvis surfaces become thin clients for D.A.I.S.E
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional, Literal
from enum import Enum

from blackfong_ecosystem.conversation_memory import conversation_memory_service
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.file_scope_service import FileScopeService
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.gps_service import GPSService
from blackfong_ecosystem.observability import log_action, LogLevel
from blackfong_ecosystem.orchestrator import Orchestrator
from blackfong_ecosystem.memory_lifecycle_management import memory_lifecycle_manager
from blackfong_ecosystem.system_awareness import SystemAwarenessService
from blackfong_ecosystem.model_routing import (
    normalize_response_mode,
    resolve_response_mode,
    should_use_fast_path,
)
from blackfong_ecosystem.tinyprogrammer_companion import tinyprogrammer_companion
from blackfong_ecosystem.voice_loop_controller import VoiceLoopController


class InputModality(str, Enum):
    """Input modality types"""
    TEXT = "text"
    VOICE = "voice"
    VISION = "vision"
    CODE = "code"


class UnifiedOrchestrator:
    """
    O.B.E.O.S as complete system brain.
    
    Provides:
    - Multi-modal input handling
    - Intelligent agent routing
    - Event-driven observability
    - Coordinated multi-agent workflows
    """

    DEFAULT_ANALYSIS_MODE = "fast"

    @staticmethod
    def _should_use_fast_path(user_input: str, context: Dict[str, Any]) -> bool:
        """Legacy compatibility shim for tests and older callers."""
        return should_use_fast_path(user_input, context)
    
    def __init__(self):
        self.orchestrator = Orchestrator()
        self.memory_service = conversation_memory_service
        self.file_workflow = FileWorkflowService(memory_service=self.memory_service)
        self.file_scope_service = FileScopeService(
            memory_service=self.memory_service,
            governed_roots=self.file_workflow.allowed_roots,
        )
        self.gps_service = GPSService(event_store=self.memory_service.event_store)
        self.awareness_service = SystemAwarenessService(
            memory_service=self.memory_service,
            tinyprogrammer=tinyprogrammer_companion,
            gps_service=self.gps_service,
        )
        self.daise = DAISE.create_default(
            event_store=self.memory_service.event_store,
            vector_store=self.memory_service.vector_store,
            memory_service=self.memory_service,
            file_workflow=self.file_workflow,
            file_scope_service=self.file_scope_service,
            awareness_service=self.awareness_service,
        )
        self.memory_chamber = self.daise.memory_chamber
        self.session_id = str(uuid.uuid4())
        self.confidence_validation: Dict[str, Dict[str, Any]] = {}
        
        # Voice loop controller (lazy initialization)
        self.voice_loop: Optional[VoiceLoopController] = None
        
        log_action(
            LogLevel.INFO,
            "unified_orchestrator",
            "initialized",
            session_id=self.session_id
        )
    
    def process_input(
        self,
        user_input: str,
        modality: InputModality = InputModality.TEXT,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process input from any modality and route intelligently.
        
        Args:
            user_input: The query or command
            modality: Input type (text, voice, vision, code)
            context: Additional context (e.g., code snippet, image path)
        
        Returns:
            Response dict with response, confidence, agent, trace_event_id
        """
        
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        # Log request
        log_action(
            LogLevel.INFO,
            "unified_orchestrator",
            "request_received",
            request_id=request_id,
            modality=modality.value,
            input_length=len(user_input)
        )
        
        session_id = self.session_id
        correlation_id = self.memory_service.create_correlation_id()

        try:
            # Normalize context and infer analysis mode when not provided
            context = dict(context or {})
            session_id = context.get("session_id") or self.session_id
            correlation_id = context.get("correlation_id") or self.memory_service.create_correlation_id()
            context["request_id"] = context.get("request_id") or request_id
            context["session_id"] = session_id
            context["correlation_id"] = correlation_id
            if "analysis_mode" not in context:
                context["analysis_mode"] = self._infer_analysis_mode(user_input)
            context["analysis_mode"] = normalize_response_mode(
                resolve_response_mode(
                    user_input,
                    context.get("analysis_mode"),
                    agent_override=context.get("agent_override"),
                )
            )
            context["response_mode"] = context["analysis_mode"]
            if "fast_path" not in context:
                context["fast_path"] = should_use_fast_path(user_input, context)

            # Pre-process based on modality
            processed_input = self._preprocess_input(user_input, modality, context)

            result = self.daise.process_request(
                user_input=processed_input,
                modality=modality.value,
                context=context,
                agents=self.orchestrator.agents,
                routing_keywords=self.orchestrator.agent_keywords,
            )

            final_result = self._postprocess_result(
                result,
                modality,
                context,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            final_result["fast_path"] = bool(context.get("fast_path"))

            latency_ms = (time.time() - start_time) * 1000
            final_result["execution_time_ms"] = latency_ms

            # Log completion
            log_action(
                LogLevel.INFO,
                "unified_orchestrator",
                "request_completed",
                request_id=request_id,
                agent=final_result.get("selected_agent") or final_result.get("agent"),
                latency_ms=latency_ms,
                confidence=final_result.get("confidence", 0.0)
            )
            
            return final_result
        
        except Exception as e:
            # Log error
            log_action(
                LogLevel.ERROR,
                "unified_orchestrator",
                "request_failed",
                request_id=request_id,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000
            )
            
            return {
                "response": f"Error processing request: {str(e)}",
                "error": str(e),
                "error_type": (getattr(e, "metadata", None) or {}).get("error_type"),
                "confidence": 0.0,
                "agent": "error_handler",
                "trace_event_id": None,
                "request_id": request_id,
                "session_id": session_id,
                "correlation_id": correlation_id,
                "query_event_id": None,
                "response_event_id": None,
            }
    
    def _preprocess_input(
        self,
        user_input: str,
        modality: InputModality,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Pre-process input based on modality."""
        
        if modality == InputModality.VOICE:
            # Voice is already transcribed, just use as-is
            return user_input
        
        elif modality == InputModality.VISION:
            # Enhance query with vision context
            if context and "analysis" in context:
                return f"{user_input}\n\n[Image Analysis: {context['analysis']}]"
            return user_input
        
        elif modality == InputModality.CODE:
            # Code queries get enhanced context
            if context and "code" in context:
                return f"{user_input}\n\n```\n{context['code']}\n```"
            return user_input
        
        else:  # TEXT
            return user_input
    
    def _select_agent(
        self,
        processed_input: str,
        modality: InputModality,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Determine which agent should handle the request."""
        agent_override = (context or {}).get("agent_override")
        if agent_override:
            normalized_override = str(agent_override).strip().lower()
            alias_map = {
                "daise": "jarvis",
                "d.a.i.s.e": "jarvis",
                "jarvis_agent": "jarvis",
                "coding_agent": "coding",
                "voice_agent": "voice",
            }
            normalized_override = alias_map.get(normalized_override, normalized_override)
            if normalized_override in self.orchestrator.agents:
                return normalized_override
        
        input_lower = processed_input.lower()
        
        # Strong routing for specific modalities
        if modality == InputModality.CODE:
            return "coding"
        elif modality == InputModality.VISION:
            return "vision"
        elif modality == InputModality.VOICE:
            return "voice"
        
        # Keyword-based routing for text
        if any(keyword in input_lower for keyword in 
               ["code", "python", "bug", "debug", "review", "function"]):
            return "coding"
        
        elif any(keyword in input_lower for keyword in 
                 ["certificate", "exam", "pcep", "study"]):
            return "lilo"
        
        elif any(keyword in input_lower for keyword in 
                 ["learn", "tutorial", "exercise"]):
            return "stitch"
        
        else:
            return "jarvis"  # Default to general reasoning
    
    def _route_to_agent(
        self,
        processed_input: str,
        agent_choice: str,
        context: Optional[Dict[str, Any]],
        modality: InputModality
    ) -> Dict[str, Any]:
        """Route request to selected agent."""
        
        if agent_choice in self.orchestrator.agents:
            agent = self.orchestrator.agents[agent_choice]
            return agent.process(processed_input, context=context)
        
        else:
            # Fallback to orchestrator's process_query
            return self.orchestrator.process_query(processed_input)
    
    def _postprocess_result(
        self,
        result: Dict[str, Any],
        modality: InputModality,
        context: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post-process result based on output modality."""

        self._register_confidence_validation(result, context)

        if result.get("confidence", 0) > 0.5:
            memory_lifecycle_manager.store_memory_item({
                "content": result.get("response", ""),
                "type": "query_response",
                "modality": modality.value,
                "confidence": result.get("confidence", 0),
                "task_type": result.get("task_type"),
                "task_types": result.get("task_types", []),
                "timestamp": time.time(),
            }, "query_response")

        result["modality"] = modality.value
        result["session_id"] = result.get("session_id") or session_id or self.session_id
        result["correlation_id"] = result.get("correlation_id") or correlation_id or (context or {}).get("correlation_id")
        result["request_id"] = result.get("request_id") or (context or {}).get("request_id")
        result["timestamp"] = time.time()
        result["event_payload"] = {
            "task_type": result.get("task_type"),
            "language": result.get("language"),
            "issues_detected": result.get("analysis", {}).get("issues_found", 0),
            "recommendations": result.get("analysis", {}).get("suggestions", []),
            "confidence": result.get("confidence", 0.0),
            "execution_time_ms": result.get("execution_time_ms", 0),
            "clarification_required": result.get("clarification_required", False),
            "analysis_mode": result.get("analysis_mode", "standard"),
            "selected_agent": result.get("selected_agent"),
            "consulted_agents": result.get("consulted_agents", []),
            "synthesized": result.get("synthesized", False),
            "provider": result.get("provider"),
            "model": result.get("model"),
            "backend_error": result.get("backend_error"),
            "decision_state": result.get("decision_state"),
            "policy_decision": result.get("policy_decision"),
        }

        return result

    def get_request_trace(self, request_id: str) -> Dict[str, Any]:
        """Return structured operator trace for a request."""
        return self.memory_service.get_request_trace(request_id)

    def _infer_analysis_mode(self, user_input: str) -> str:
        """Infer fast, standard, or deep response mode from the operator message."""
        lower = user_input.lower()
        if any(token in lower for token in ["deep", "thorough", "detailed", "comprehensive", "analyze deeply"]):
            return "deep"
        if any(token in lower for token in ["standard", "normal", "balanced"]):
            return "standard"
        if any(token in lower for token in ["quick", "fast", "summary", "brief"]):
            return "fast"
        return self.DEFAULT_ANALYSIS_MODE

    def _register_confidence_validation(
        self,
        result: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record predicted vs actual outcome for confidence calibration."""
        validation = (context or {}).get("validation") if context else None
        if not validation:
            return

        predicted_confidence = float(result.get("confidence", 0.0))
        actual_outcome = validation.get("outcome_correct")
        if actual_outcome is None:
            actual_outcome = validation.get("actual_outcome") in ("correct", "success", True)

        task_type = result.get("task_type") or result.get("agent", "unknown")
        bucket = self.confidence_validation.setdefault(task_type, {
            "total": 0,
            "correct": 0,
            "predicted_sum": 0.0,
            "records": [],
        })

        bucket["total"] += 1
        bucket["predicted_sum"] += predicted_confidence
        if actual_outcome:
            bucket["correct"] += 1

        bucket["records"].append({
            "predicted_confidence": predicted_confidence,
            "actual_outcome": bool(actual_outcome),
            "source": validation.get("source", "manual"),
            "timestamp": time.time(),
        })

    def get_confidence_accuracy(self, task_type: str) -> float:
        """Return rolling accuracy for a specific task type."""
        bucket = self.confidence_validation.get(task_type)
        if not bucket or bucket["total"] == 0:
            return 0.0
        return round(bucket["correct"] / bucket["total"], 3)

    def get_confidence_summary(self) -> Dict[str, Any]:
        """Return a summary of confidence calibration metrics."""
        return {
            task: {
                "accuracy": round(data["correct"] / data["total"], 3) if data["total"] else 0.0,
                "predicted_average": round(data["predicted_sum"] / data["total"], 3) if data["total"] else 0.0,
                "total": data["total"],
            }
            for task, data in self.confidence_validation.items()
        }

    def start_voice_loop(self) -> bool:
        """
        Start the continuous voice interaction loop.
        
        Returns:
            True if voice loop started successfully
        """
        if self.voice_loop and self.voice_loop.is_running:
            log_action(
                LogLevel.WARNING,
                "unified_orchestrator",
                "voice_loop_already_running"
            )
            return False
        
        try:
            # Initialize voice loop controller
            from blackfong_ecosystem.agents.voice_agent import VoiceAgent, VoiceMode
            
            voice_agent = VoiceAgent(voice_mode=VoiceMode.PUSH_TO_TALK)
            self.voice_loop = VoiceLoopController(
                unified_orchestrator=self,
                voice_agent=voice_agent
            )
            
            # Start the loop
            if self.voice_loop.start():
                log_action(
                    LogLevel.INFO,
                    "unified_orchestrator",
                    "voice_loop_started"
                )
                return True
            else:
                log_action(
                    LogLevel.ERROR,
                    "unified_orchestrator",
                    "voice_loop_start_failed"
                )
                return False
                
        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "unified_orchestrator",
                "voice_loop_initialization_error",
                error=str(e)
            )
            return False

    def stop_voice_loop(self) -> bool:
        """
        Stop the continuous voice interaction loop.
        
        Returns:
            True if voice loop stopped successfully
        """
        if not self.voice_loop:
            log_action(
                LogLevel.WARNING,
                "unified_orchestrator",
                "no_voice_loop_to_stop"
            )
            return True
        
        try:
            if self.voice_loop.stop():
                log_action(
                    LogLevel.INFO,
                    "unified_orchestrator",
                    "voice_loop_stopped"
                )
                self.voice_loop = None
                return True
            else:
                log_action(
                    LogLevel.ERROR,
                    "unified_orchestrator",
                    "voice_loop_stop_failed"
                )
                return False
                
        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "unified_orchestrator",
                "voice_loop_stop_error",
                error=str(e)
            )
            return False

    def get_voice_loop_status(self) -> Dict[str, Any]:
        """
        Get the status of the voice loop.
        
        Returns:
            Dict with voice loop status information
        """
        if not self.voice_loop:
            return {
                "active": False,
                "message": "Voice loop not initialized"
            }
        
        return {
            "active": True,
            **self.voice_loop.get_status()
        }

    def run_interactive_repl(self):
        """
        Interactive REPL replacing JARVIS-MARK-5.
        
        Commands:
        - query <text>: Process text query
        - code: Process code query
        - voice start: Start voice loop
        - voice stop: Stop voice loop
        - voice status: Show voice loop status
        - help: Show help
        - status: Show system status
        - exit: Quit
        """
        
        print("\n" + "="*70)
        print("O.B.E.O.S UNIFIED ORCHESTRATOR")
        print("="*70)
        print("Commands: query|code|voice|status|stress|help|exit\n")
        
        while True:
            try:
                cmd_input = input("obeos> ").strip()
                
                if not cmd_input:
                    continue
                
                elif cmd_input == "exit":
                    print("Shutting down...")
                    break
                
                elif cmd_input == "help":
                    self._print_help()
                
                elif cmd_input == "status":
                    self._print_status()
                
                elif cmd_input == "stress":
                    print("Starting stress test Level 1...")
                    from blackfong_ecosystem.stress_testing_suite import StressTestSuite
                    suite = StressTestSuite()
                    suite.run_level_1()
                
                elif cmd_input == "voice start":
                    print("Starting voice loop...")
                    if self.start_voice_loop():
                        print("✅ Voice loop started successfully")
                        print("🎙️  Say 'Hey Blackfong' to activate voice commands")
                    else:
                        print("❌ Failed to start voice loop")
                
                elif cmd_input == "voice stop":
                    print("Stopping voice loop...")
                    if self.stop_voice_loop():
                        print("✅ Voice loop stopped successfully")
                    else:
                        print("❌ Failed to stop voice loop")
                
                elif cmd_input == "voice status":
                    status = self.get_voice_loop_status()
                    print("\n🎙️  Voice Loop Status:")
                    if status["active"]:
                        print(f"  State: {status['state']}")
                        print(f"  Running: {status['is_running']}")
                        print(f"  Audio: {'✅' if status['audio_initialized'] else '❌'}")
                        print(f"  STT: {'✅' if status['stt_available'] else '❌'}")
                        print(f"  Wake Detection: {'✅' if status['wake_detector_available'] else '❌'}")
                        print(f"  Wake Words: {', '.join(status['wake_words'])}")
                    else:
                        print(f"  Status: {status['message']}")
                    print()
                
                elif cmd_input.startswith("query "):
                    query = cmd_input[6:].strip()
                    result = self.process_input(query, InputModality.TEXT)
                    print(f"\n{result['response']}\n")
                    print(f"[Agent: {result['agent']} | Confidence: {result['confidence']:.2f}]")
                
                elif cmd_input.startswith("code "):
                    query = cmd_input[5:].strip()
                    result = self.process_input(query, InputModality.CODE)
                    print(f"\n{result['response']}\n")
                    print(f"[Agent: {result['agent']} | Confidence: {result['confidence']:.2f}]")
                
                else:
                    # Default to query
                    result = self.process_input(cmd_input, InputModality.TEXT)
                    print(f"\n{result['response']}\n")
                    print(f"[Agent: {result['agent']} | Confidence: {result['confidence']:.2f}]")
            
            except KeyboardInterrupt:
                print("\n\nInterrupt received. Type 'exit' to quit.")
            except Exception as e:
                print(f"Error: {e}")
    
    def _print_help(self):
        """Print help information"""
        print("\nO.B.E.O.S Commands:")
        print("  query <text>    - Process a text query")
        print("  code <query>    - Process a code-related query")
        print("  voice start     - Start continuous voice interaction")
        print("  voice stop      - Stop voice interaction")
        print("  voice status    - Show voice loop status")
        print("  status          - Show system status")
        print("  stress          - Run Level 1 stress test")
        print("  help            - Show this help message")
        print("  exit            - Exit the system\n")
    
    def _print_status(self):
        """Print system status"""
        from blackfong_ecosystem.decision_quality_control import decision_quality_controller
        
        print("\n📊 System Status:")
        print(f"  Session ID: {self.session_id}")
        print(f"  Registered Agents: {len(self.orchestrator.agents)}")
        print(f"  Memory Items: {memory_lifecycle_manager.stats.get('total_items', 0)}")
        
        # Get quality metrics
        metrics = decision_quality_controller.get_quality_metrics()
        print(f"  Decision Quality Score: {metrics.get('quality_score', 0):.2f}")
        print(f"  Confidence Validation Buckets: {len(self.confidence_validation)}")
        if self.confidence_validation:
            summary = self.get_confidence_summary()
            print("  Confidence Summary:")
            for task_type, data in summary.items():
                print(f"    - {task_type}: accuracy={data['accuracy']:.2f}, avg_predicted={data['predicted_average']:.2f}, total={data['total']}")
        print()


# Global instance
unified_orchestrator = UnifiedOrchestrator()


if __name__ == "__main__":
    unified_orchestrator.run_interactive_repl()
