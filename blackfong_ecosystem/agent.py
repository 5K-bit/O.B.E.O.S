"""
Agent Interface and Contract

Defines the standard interface for all agents in the Blackfong system.
All agents must implement this contract to participate in coordinated reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .events import Event
from .storage import EventStore


class AgentType(str, Enum):
    """Classification of agent types."""
    REASONING = "reasoning"      # General reasoning (JARVIS)
    TRAINING = "training"         # Learning/training agents (lilo, stitch)
    PERCEPTION = "perception"     # Vision/sensing agents
    UTILITY = "utility"           # Tool/utility agents


class ToolStatus(str, Enum):
    """Status of a tool invocation."""
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"


class ToolRequest(BaseModel):
    """Standardized tool request format."""
    request_id: str              # unique request identifier
    tool_name: str               # which service/tool
    action: str                  # what action to perform
    params: Dict[str, Any] = {}  # parameters for the action
    context: Dict[str, Any] = {} # session/correlation context
    
    class Config:
        frozen = True


class ToolResponse(BaseModel):
    """Standardized tool response format."""
    request_id: str              # echo back for tracing
    success: bool                # did it succeed?
    result: Any = None           # the output
    error: Optional[str] = None  # error message if failed
    status: ToolStatus = ToolStatus.SUCCESS
    
    class Config:
        frozen = True


@dataclass
class ReasoningStep:
    """A single step in agent reasoning."""
    step_id: str
    action: str                  # what action was taken
    input: Any                   # what was the input
    output: Any                  # what was the result
    confidence: float = 1.0      # how confident (0-1)
    tool_invoked: Optional[str] = None  # if a tool was called
    tool_response: Optional[ToolResponse] = None


@dataclass
class ReasoningTrace:
    """Complete trace of agent reasoning for auditability."""
    agent_name: str
    user_input: str
    timestamp: int               # epoch ms
    steps: List[ReasoningStep] = field(default_factory=list)
    final_decision: Optional[str] = None
    overall_confidence: float = 1.0
    alternative_actions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for event storage."""
        return {
            "agent_name": self.agent_name,
            "user_input": self.user_input,
            "timestamp": self.timestamp,
            "steps": [
                {
                    "step_id": s.step_id,
                    "action": s.action,
                    "confidence": s.confidence,
                    "tool_invoked": s.tool_invoked,
                } for s in self.steps
            ],
            "final_decision": self.final_decision,
            "overall_confidence": self.overall_confidence,
            "alternative_actions": self.alternative_actions,
        }


class Agent:
    """
    Base class for all agents in the Blackfong system.
    
    Every agent must:
    1. Have a name and type
    2. Implement process() for handling user input
    3. Emit reasoning traces to O.B.E.O.S
    4. Use standardized tool requests/responses
    5. Maintain a reference to the event client
    """
    
    def __init__(
        self,
        name: str,
        agent_type: AgentType,
        event_client=None,  # Optional EventClient reference
        event_api_url: str = "http://localhost:8000",
    ):
        self.name = name
        self.agent_type = agent_type
        self.event_api_url = event_api_url
        
        # Auto-create EventClient if not provided
        if event_client is None:
            try:
                from .event_client import EventClient
                self.event_client = EventClient(api_url=event_api_url)
            except Exception as e:
                # Event client creation failed - agent can still function without it
                print(f"Warning: Could not create EventClient for agent {name}: {e}")
                self.event_client = None
        else:
            self.event_client = event_client
    
    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process user input and return a response.
        
        Args:
            user_input: User's query or command
            context: Optional context (session_id, correlation_id, etc.)
        
        Returns:
            {
                "response": str,           # The response to the user
                "reasoning_trace": {...},  # Trace of reasoning
                "confidence": float,       # Overall confidence
                "tools_invoked": [...]     # List of tools used
            }
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement process()")
    
    def invoke_tool(
        self,
        tool_name: str,
        action: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResponse:
        """
        Invoke a tool via the O.B.E.O.S event system.
        
        Args:
            tool_name: Name of the service/tool
            action: Action to perform
            params: Parameters for the action
            context: Session/correlation context
        
        Returns:
            ToolResponse with result or error
        """
        if not self.event_client:
            return ToolResponse(
                request_id="no-client",
                success=False,
                error="Event client not configured",
            )
        
        try:
            import uuid
            request_id = str(uuid.uuid4())
            
            tool_request = ToolRequest(
                request_id=request_id,
                tool_name=tool_name,
                action=action,
                params=params,
                context=context or {},
            )
            
            # TODO: Send tool request as event, wait for response
            # For now, return a stub response
            return ToolResponse(
                request_id=request_id,
                success=True,
                result={"status": "tool_invoked", "tool": tool_name},
            )
        except Exception as e:
            return ToolResponse(
                request_id="error",
                success=False,
                error=str(e),
                status=ToolStatus.FAILED,
            )
    
    def emit_reasoning_trace(
        self,
        trace: ReasoningTrace,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Emit reasoning trace as an event to O.B.E.O.S.
        
        Args:
            trace: ReasoningTrace to emit
            session_id: Optional session grouping
            correlation_id: Optional correlation ID for workflows
        
        Returns:
            Event ID or None if client not configured
        """
        try:
            store = EventStore()
            store.init_db()
            event = Event.new(
                source=self.name,  # type: ignore[arg-type]
                type="reasoning_trace",
                payload=trace.to_dict(),
                confidence=trace.overall_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                tags=["reasoning", self.agent_type.value],
            )
            store.add_event(event)
            return event.id
        except Exception as e:
            print(f"Error emitting reasoning trace: {e}")
            return None
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} type={self.agent_type.value}>"
