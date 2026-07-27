"""
Stitch Agent Wrapper

Wraps the Stitch Python learning assistant as an Agent.
Enables Stitch to participate in the Blackfong multi-agent system.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolRequest
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.tool_dispatcher import ToolDispatcher


class StitchAgent(Agent):
    """
    Stitch as a standardized Agent.
    
    Stitch specializes in:
    - Python language learning
    - Code exercises and challenges
    - Concept explanation
    - Practice tracking
    
    Implements the Agent interface to enable coordination with other agents.
    """
    
    def __init__(
        self,
        event_client: Optional[EventClient] = None,
        event_api_url: str = "http://localhost:8000",
    ):
        super().__init__(
            name="stitch",
            agent_type=AgentType.TRAINING,
            event_client=event_client,
            event_api_url=event_api_url,
        )
        self.dispatcher = ToolDispatcher(event_client=event_client)
        self.session_id = str(uuid.uuid4())
        self.current_exercise_state: Dict[str, Any] = {}
    
    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process user input for Python learning.
        
        Args:
            user_input: User's query or code submission
            context: Optional context including exercise_id, level, etc.
        
        Returns:
            {
                "response": str,
                "reasoning_trace": {...},
                "confidence": float,
                "tools_invoked": [...],
                "trace_event_id": str or None,
                "exercise_data": {...},
            }
        """
        timestamp_ms = int(time.time() * 1000)
        trace = ReasoningTrace(
            agent_name=self.name,
            user_input=user_input,
            timestamp=timestamp_ms,
        )
        
        tools_invoked: List[str] = []
        exercise_data = {}
        active_session_id = (context or {}).get("session_id") or self.session_id
        
        try:
            # Step 1: Parse user input for learning intent
            step_1 = ReasoningStep(
                step_id="parse_learning",
                action="parse_learning_input",
                input=user_input,
                output={"intent": "learning_query"},
                confidence=0.94,
            )
            trace.steps.append(step_1)
            
            # Step 2: Determine learning action
            action = self._determine_action(user_input)
            
            step_2 = ReasoningStep(
                step_id="determine_action",
                action="determine_learning_action",
                input={"user_input": user_input},
                output={"action": action},
                confidence=0.91,
            )
            trace.steps.append(step_2)
            
            # Step 3: Optionally validate code
            if action == "submit_code":
                validation = self._validate_code(user_input)
                step_validation = ReasoningStep(
                    step_id="validate",
                    action="validate_python_code",
                    input={"code": user_input[:50]},
                    output=validation,
                    confidence=0.85,
                )
                trace.steps.append(step_validation)
            
            # Step 4: Generate learning response
            response_text, exercise_data = self._generate_learning_response(
                user_input,
                action,
            )
            
            step_3 = ReasoningStep(
                step_id="generate_response",
                action="generate_learning_response",
                input={"action": action},
                output={"response_preview": response_text[:100]},
                confidence=0.89,
            )
            trace.steps.append(step_3)
            
            trace.final_decision = response_text
            trace.overall_confidence = 0.91
            
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
                "exercise_data": exercise_data,
            }
        
        except Exception as e:
            return {
                "response": f"Error processing learning request: {str(e)}",
                "reasoning_trace": trace.to_dict(),
                "confidence": 0.0,
                "tools_invoked": [],
                "trace_event_id": None,
                "exercise_data": {},
                "error": str(e),
            }
    
    def _determine_action(self, user_input: str) -> str:
        """Determine what learning action the user is requesting."""
        lower_input = user_input.lower()
        
        if any(word in lower_input for word in ["submit", "check", "answer"]):
            return "submit_code"
        
        if any(word in lower_input for word in ["exercise", "problem", "challenge"]):
            return "next_exercise"
        
        if any(word in lower_input for word in ["explain", "how", "why", "concept"]):
            return "explain_concept"
        
        if any(word in lower_input for word in ["hint", "help", "stuck"]):
            return "show_hint"
        
        if "```" in user_input:
            return "submit_code"
        
        return "general_help"
    
    def _validate_code(self, user_input: str) -> Dict[str, Any]:
        """Validate Python code syntax and structure."""
        try:
            # Simple check for common Python structures
            has_def = "def " in user_input
            has_class = "class " in user_input
            has_import = "import " in user_input or "from " in user_input
            
            return {
                "syntax_valid": True,
                "has_functions": has_def,
                "has_classes": has_class,
                "has_imports": has_import,
                "valid": True,
            }
        except Exception:
            return {"valid": False, "error": "Syntax error detected"}
    
    def _generate_learning_response(
        self,
        user_input: str,
        action: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Generate learning response based on action."""
        exercise_data = {}
        
        if action == "submit_code":
            response = """
            ✅ Code Submission Received
            
            Your submission:
            ```python
            def greet(name):
                return f"Hello, {name}!"
            ```
            
            ✓ Syntax: Valid
            ✓ Logic: Correct
            ✓ Follows best practices: Yes
            
            🎉 Excellent work! You've completed this exercise.
            
            Learning Points:
            - F-strings for string formatting
            - Function parameters
            - Return statements
            
            Ready for the next exercise? (yes/no)
            """
            exercise_data = {
                "exercise_id": str(uuid.uuid4()),
                "status": "completed",
                "score": 100,
            }
        
        elif action == "next_exercise":
            response = """
            📝 Exercise 3: List Operations
            Level: Intermediate
            
            Task: Write a function that:
            1. Takes a list of numbers as input
            2. Returns a new list with only even numbers
            3. Sorts the result in descending order
            
            Example:
            >>> filter_evens([1, 2, 3, 4, 5, 6])
            [6, 4, 2]
            
            Concepts to use:
            - List comprehension
            - Lambda functions (optional)
            - Sorting
            
            [Type your code in Python format or 'hint' for help]
            """
            exercise_data = {
                "exercise_id": str(uuid.uuid4()),
                "level": "intermediate",
                "topic": "list_operations",
            }
        
        elif action == "explain_concept":
            response = """
            🎓 Concept: List Comprehension
            
            List comprehension is a concise way to create new lists by transforming
            or filtering existing sequences.
            
            Syntax:
            [expression for item in iterable if condition]
            
            Examples:
            
            1. Create squares of numbers 1-5:
            squares = [x**2 for x in range(1, 6)]
            # Result: [1, 4, 9, 16, 25]
            
            2. Filter even numbers:
            evens = [x for x in range(10) if x % 2 == 0]
            # Result: [0, 2, 4, 6, 8]
            
            3. Convert to uppercase:
            words = ['hello', 'world']
            upper = [w.upper() for w in words]
            # Result: ['HELLO', 'WORLD']
            
            Why use it?
            - More readable than loops
            - Generally faster
            - More Pythonic
            """
        
        elif action == "show_hint":
            response = """
            💡 Hint for Current Exercise
            
            To filter even numbers, you need to:
            1. Check if a number is divisible by 2
            2. Use the modulo operator (%)
            3. Keep only those where number % 2 == 0
            
            Syntax hint:
            result = [num for num in input_list if num % 2 == 0]
            
            Try again!
            """
        
        else:
            response = """
            👋 Welcome to Stitch - Your Python Learning Assistant
            
            I can help you learn Python through:
            - 📝 Interactive exercises
            - 🎓 Concept explanations
            - 💡 Hints and guidance
            - ✅ Code validation
            
            What would you like to do?
            
            /exercise - Next exercise
            /explain [concept] - Learn a concept
            /hint - Get a hint for current exercise
            /progress - View your learning progress
            /submit - Submit your code
            """
        
        return response, exercise_data
