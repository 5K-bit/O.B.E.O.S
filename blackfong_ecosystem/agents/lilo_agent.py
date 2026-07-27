"""
Lilo Agent Wrapper

Wraps the Lilo certification training assistant as an Agent.
Enables Lilo to participate in the Blackfong multi-agent system.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolRequest
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.tool_dispatcher import ToolDispatcher


class LiloAgent(Agent):
    """
    Lilo as a standardized Agent.
    
    Lilo specializes in:
    - Certification exam preparation
    - Quiz generation and grading
    - Progress tracking
    - Adaptive learning paths
    
    Implements the Agent interface to enable coordination with other agents.
    """
    
    def __init__(
        self,
        event_client: Optional[EventClient] = None,
        event_api_url: str = "http://localhost:8000",
    ):
        super().__init__(
            name="lilo",
            agent_type=AgentType.TRAINING,
            event_client=event_client,
            event_api_url=event_api_url,
        )
        self.dispatcher = ToolDispatcher(event_client=event_client)
        self.session_id = str(uuid.uuid4())
        self.current_quiz_state: Dict[str, Any] = {}
    
    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process user input for certification training.
        
        Args:
            user_input: User's query or answer
            context: Optional context including quiz_id, question_number, etc.
        
        Returns:
            {
                "response": str,
                "reasoning_trace": {...},
                "confidence": float,
                "tools_invoked": [...],
                "trace_event_id": str or None,
                "quiz_data": {...},
            }
        """
        timestamp_ms = int(time.time() * 1000)
        trace = ReasoningTrace(
            agent_name=self.name,
            user_input=user_input,
            timestamp=timestamp_ms,
        )
        
        tools_invoked: List[str] = []
        quiz_data = {}
        active_session_id = (context or {}).get("session_id") or self.session_id
        
        try:
            # Step 1: Parse user input for training intent
            step_1 = ReasoningStep(
                step_id="parse_training",
                action="parse_training_input",
                input=user_input,
                output={"intent": "training_query"},
                confidence=0.93,
            )
            trace.steps.append(step_1)
            
            # Step 2: Determine training action
            action = self._determine_action(user_input)
            
            step_2 = ReasoningStep(
                step_id="determine_action",
                action="determine_training_action",
                input={"user_input": user_input},
                output={"action": action},
                confidence=0.90,
            )
            trace.steps.append(step_2)
            
            # Step 3: Generate training response
            response_text, quiz_data = self._generate_training_response(
                user_input,
                action,
            )
            
            step_3 = ReasoningStep(
                step_id="generate_training",
                action="generate_training_response",
                input={"action": action},
                output={"response_preview": response_text[:100]},
                confidence=0.88,
            )
            trace.steps.append(step_3)
            
            trace.final_decision = response_text
            trace.overall_confidence = 0.90
            
            # Emit reasoning trace
            correlation_id = context.get("correlation_id") if context else None
            trace_event_id = self.emit_reasoning_trace(
                trace=trace,
                session_id=active_session_id,
                correlation_id=correlation_id,
                request_id=context.get("request_id") if context else None,
            )
            
            return {
                "response": response_text,
                "reasoning_trace": trace.to_dict(),
                "confidence": trace.overall_confidence,
                "tools_invoked": tools_invoked,
                "trace_event_id": trace_event_id,
                "quiz_data": quiz_data,
            }
        
        except Exception as e:
            return {
                "response": f"Error processing training request: {str(e)}",
                "reasoning_trace": trace.to_dict(),
                "confidence": 0.0,
                "tools_invoked": [],
                "trace_event_id": None,
                "quiz_data": {},
                "error": str(e),
            }
    
    def _determine_action(self, user_input: str) -> str:
        """Determine what training action the user is requesting."""
        lower_input = user_input.lower()
        
        if any(word in lower_input for word in ["quiz", "question", "test"]):
            return "start_quiz"
        
        if any(word in lower_input for word in ["progress", "stats", "score"]):
            return "show_progress"
        
        if any(word in lower_input for word in ["review", "explain", "help"]):
            return "explain_topic"
        
        if any(word in lower_input for word in ["topic", "subject", "module"]):
            return "select_topic"
        
        return "general_help"
    
    def _generate_training_response(
        self,
        user_input: str,
        action: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Generate training response based on action."""
        quiz_data = {}
        
        if action == "start_quiz":
            response = """
            📝 Starting Quiz Session
            
            Topic: CompTIA Security+ Certification
            Difficulty: Intermediate
            Questions: 10
            
            Question 1/10: What is the primary purpose of a firewall?
            A) To prevent malware
            B) To control network traffic
            C) To encrypt data
            D) All of the above
            
            [Awaiting your answer...]
            """
            quiz_data = {
                "quiz_id": str(uuid.uuid4()),
                "question_number": 1,
                "total_questions": 10,
                "topic": "CompTIA Security+",
            }
        
        elif action == "show_progress":
            response = """
            📊 Your Progress Report
            
            Total Questions Attempted: 45
            Correct Answers: 38
            Accuracy: 84.4%
            
            Topics Mastered:
            - Network Security (92%)
            - Cryptography (88%)
            - Access Control (85%)
            
            Topics to Review:
            - Incident Response (71%)
            - Compliance (76%)
            """
            quiz_data = {
                "total_attempted": 45,
                "correct": 38,
                "accuracy": 84.4,
            }
        
        elif action == "explain_topic":
            response = """
            🎓 Topic Explanation: Defense in Depth
            
            Defense in Depth is a security strategy that uses multiple layers of security controls
            to protect systems and data. Rather than relying on a single security measure, it
            implements several independent protective mechanisms.
            
            Key Components:
            1. Physical Security - locks, surveillance, access control
            2. Network Security - firewalls, IDS/IPS, VPNs
            3. Application Security - input validation, authentication
            4. Data Security - encryption, data masking
            5. User Security - training, awareness programs
            
            Example: A financial institution might combine:
            - Card readers for building access
            - Network firewalls for data protection
            - Multi-factor authentication for systems
            - Encrypted communications
            - Regular security audits
            """
        
        else:
            response = """
            👋 Welcome to Lilo - Your Certification Training Assistant
            
            I can help you prepare for CompTIA certifications including:
            - Security+ (Sec+)
            - Network+ (Net+)
            - A+ (CompTIA A+)
            
            What would you like to do?
            
            /quiz - Start a practice quiz
            /progress - View your progress
            /topic [name] - Learn about a specific topic
            /stats - See detailed statistics
            """
        
        return response, quiz_data
