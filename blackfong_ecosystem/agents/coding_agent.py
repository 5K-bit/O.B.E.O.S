"""
Specialized Coding Agent

Python-first agent for software development tasks.
Focuses on code review, debugging, optimization, testing, and architecture analysis.

Replaces/augments JARVIS-MARK-5 for development workflows.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolResponse
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.events import Event
from blackfong_ecosystem.storage import EventStore
from blackfong_ecosystem.observability import log_action, LogLevel


class CodingAgent(Agent):
    """
    Specialized agent for software development tasks.
    
    Python-first implementation with capabilities:
    - Code review and quality analysis
    - Bug detection and debugging
    - Performance optimization recommendations
    - Architecture and design critique
    - Test generation and analysis
    - Refactoring suggestions
    """

    TASK_KEYWORDS = {
        "code_review": ["review", "check", "analyze", "critique", "lint", "style", "quality"],
        "debugging": ["bug", "fix", "error", "issue", "wrong", "fail", "stack trace", "exception"],
        "optimization": ["optimize", "performance", "speed", "efficient", "slow", "bottleneck"],
        "design": ["design", "architecture", "pattern", "structure", "scale", "modular"],
        "testing": ["test", "unittest", "pytest", "coverage", "assert", "edge case"],
        "refactoring": ["refactor", "clean", "improve", "rewrite", "simplify", "DRY"],
        "general_coding": ["code", "function", "script", "program", "module"],
    }
    DEFAULT_ANALYSIS_MODE = "quick"
    CLARIFICATION_MARGIN = 0.12
    MIN_CLASSIFICATION_CONFIDENCE = 0.35

    def __init__(
        self,
        event_client: Optional[EventClient] = None,
        event_api_url: str = "http://localhost:8000",
    ):
        super().__init__(
            name="coding",
            agent_type=AgentType.REASONING,
            event_client=event_client,
            event_api_url=event_api_url,
        )
        self.session_id = str(uuid.uuid4())
        self.event_store = EventStore()
        
        log_action(
            LogLevel.INFO,
            "coding_agent",
            "initialized",
            session_id=self.session_id
        )
    
    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process coding-related queries with specialized analysis.
        
        Args:
            user_input: Coding task or question
            context: Optional context (code snippet, language, framework)
        
        Returns:
            {
                "response": str,
                "code_snippets": [...],
                "confidence": float,
                "analysis": {...},
                "trace_event_id": str,
                "agent": "coding"
            }
        """
        timestamp_ms = int(time.time() * 1000)
        trace = ReasoningTrace(
            agent_name=self.name,
            user_input=user_input,
            timestamp=timestamp_ms,
        )
        active_session_id = (context or {}).get("session_id") or self.session_id
        
        try:
            analysis_mode = (context or {}).get("analysis_mode", self.DEFAULT_ANALYSIS_MODE)
            validation = (context or {}).get("validation")
            correlation_id = (context or {}).get("correlation_id")
            request_id = (context or {}).get("request_id")

            # Step 1: Analyze task type with multi-label classification
            task_scores = self._classify_tasks(user_input)
            primary_task = task_scores[0]["task_type"]
            task_types = [task["task_type"] for task in task_scores if task["score"] > 0]
            classification_confidence = self._estimate_classification_confidence(task_scores)
            clarification_required = self._should_ask_for_clarification(task_scores)

            step_1 = ReasoningStep(
                step_id="analyze_task",
                action="classify_coding_task",
                input=user_input,
                output={
                    "primary_task": primary_task,
                    "task_types": task_types,
                    "task_scores": task_scores,
                },
                confidence=classification_confidence,
            )
            trace.steps.append(step_1)

            if clarification_required:
                clarification_question = self._generate_clarification_question(task_scores)
                step_clarification = ReasoningStep(
                    step_id="clarification_needed",
                    action="ask_for_clarification",
                    input={"task_scores": task_scores},
                    output={"question": clarification_question},
                    confidence=0.65,
                )
                trace.steps.append(step_clarification)
                overall_confidence = self._calculate_final_confidence(trace.steps)
                trace.final_confidence = overall_confidence
                trace.final_decision = clarification_question

                execution_time_ms = int(time.time() * 1000) - timestamp_ms
                trace_event = Event.new(
                    source="coding_agent",
                    type="reasoning_trace",
                    payload=self._build_event_payload(
                        primary_task,
                        task_types,
                        "unknown",
                        {},
                        overall_confidence,
                        execution_time_ms,
                        analysis_mode,
                        clarification_required,
                    ),
                    confidence=overall_confidence,
                    session_id=active_session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                self.event_store.add_event(trace_event)

                return {
                    "response": clarification_question,
                    "task_type": primary_task,
                    "task_types": task_types,
                    "language": "unknown",
                    "analysis": {},
                    "code_snippets": [],
                    "confidence": overall_confidence,
                    "clarification_required": True,
                    "trace_event_id": trace_event.id,
                    "agent": "coding",
                }

            # Step 2: Extract code context
            extracted_code = self._extract_code(context) if context else ""
            step_2 = ReasoningStep(
                step_id="extract_context",
                action="extract_code_context",
                input={"has_context": bool(context)},
                output={"code_length": len(extracted_code)},
                confidence=0.95,
            )
            trace.steps.append(step_2)
            
            # Step 3: Detect language
            language = self._detect_language(extracted_code)
            step_3 = ReasoningStep(
                step_id="detect_language",
                action="detect_programming_language",
                input={"code_provided": len(extracted_code) > 0},
                output={"detected_language": language},
                confidence=0.90,
            )
            trace.steps.append(step_3)
            
            # Step 4: Perform specialized analysis
            analysis = self._analyze_code(user_input, extracted_code, task_scores, language, analysis_mode)
            step_4 = ReasoningStep(
                step_id="analyze_code",
                action=f"perform_{primary_task}_analysis",
                input={"task_types": task_types, "language": language, "analysis_mode": analysis_mode},
                output={"analysis_summary": analysis.get("summary", "")},
                confidence=0.88,
            )
            trace.steps.append(step_4)

            # Step 4.5: Delegate to a specialist agent when multi-task analysis can benefit
            tool_interaction = self._delegate_to_specialist(task_types, user_input, language, analysis_mode)
            if isinstance(tool_interaction, ToolResponse):
                tool_interaction = tool_interaction.dict()
            if tool_interaction and tool_interaction.get("success"):
                analysis["delegation"] = {
                    "tool": tool_interaction.get("tool"),
                    "result": tool_interaction.get("result"),
                    "request_id": tool_interaction.get("request_id"),
                }

            # Step 5: Generate recommendations
            response = self._generate_coding_response(primary_task, task_types, analysis, language, analysis_mode)
            step_5 = ReasoningStep(
                step_id="generate_response",
                action="generate_coding_response",
                input={"analysis_complete": True},
                output={"response_generated": True},
                confidence=0.90,
            )
            trace.steps.append(step_5)
            
            # Calculate overall confidence
            overall_confidence = self._calculate_final_confidence(trace.steps)
            trace.final_confidence = overall_confidence
            trace.final_decision = response
            
            if validation is not None:
                self._record_confidence_validation(primary_task, overall_confidence, validation)

            execution_time_ms = int(time.time() * 1000) - timestamp_ms
            trace_event = Event.new(
                source="coding_agent",
                type="reasoning_trace",
                payload=self._build_event_payload(
                    primary_task,
                    task_types,
                    language,
                    analysis,
                    overall_confidence,
                    execution_time_ms,
                    analysis_mode,
                    clarification_required,
                ),
                confidence=overall_confidence,
                session_id=active_session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            self.event_store.add_event(trace_event)
            
            log_action(
                LogLevel.INFO,
                "coding_agent",
                "analysis_complete",
                task_type=primary_task,
                task_types=task_types,
                language=language,
                confidence=overall_confidence,
                analysis_mode=analysis_mode
            )
            
            return {
                "response": response,
                "task_type": primary_task,
                "task_types": task_types,
                "language": language,
                "analysis": analysis,
                "code_snippets": analysis.get("suggestions", []),
                "confidence": overall_confidence,
                "analysis_mode": analysis_mode,
                "clarification_required": clarification_required,
                "tools_invoked": [tool_interaction] if tool_interaction else [],
                "trace_event_id": trace_event.id,
                "agent": "coding",
            }
        
        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "coding_agent",
                "processing_error",
                error=str(e)
            )
            return {
                "response": f"Error processing coding query: {str(e)}",
                "confidence": 0.0,
                "trace_event_id": None,
                "agent": "coding",
            }
    
    def _classify_tasks(self, user_input: str) -> List[Dict[str, Any]]:
        """Classify one or more coding task types from the input."""
        input_lower = user_input.lower()
        task_scores: List[Dict[str, Any]] = []

        for task_type, keywords in self.TASK_KEYWORDS.items():
            if not keywords:
                continue
            matches = sum(1 for keyword in keywords if keyword in input_lower)
            score = matches / len(keywords)
            if score > 0:
                task_scores.append({
                    "task_type": task_type,
                    "score": round(score, 3),
                    "matches": matches,
                })

        if not task_scores:
            return [{"task_type": "general_coding", "score": 0.3, "matches": 0}]

        task_scores.sort(key=lambda record: record["score"], reverse=True)
        return task_scores

    def _estimate_classification_confidence(self, task_scores: List[Dict[str, Any]]) -> float:
        """Estimate confidence for task type classification."""
        top_score = task_scores[0]["score"] if task_scores else 0.0
        second_score = task_scores[1]["score"] if len(task_scores) > 1 else 0.0
        separation = top_score - second_score
        confidence = min(1.0, max(0.0, top_score + separation * 0.2))
        return round(max(self.MIN_CLASSIFICATION_CONFIDENCE, confidence), 2)

    def _should_ask_for_clarification(self, task_scores: List[Dict[str, Any]]) -> bool:
        """Decide whether to ask the user for clarification."""
        if len(task_scores) < 2:
            return False
        top_score = task_scores[0]["score"]
        second_score = task_scores[1]["score"]
        if top_score < 0.45 or (top_score - second_score) < self.CLARIFICATION_MARGIN:
            return True
        return False

    def _generate_clarification_question(self, task_scores: List[Dict[str, Any]]) -> str:
        """Generate a clarification prompt when the intent is ambiguous."""
        top_tasks = [task["task_type"].replace("_", " ") for task in task_scores[:2]]
        return (
            f"I see multiple possible intents here: {top_tasks[0]} and {top_tasks[1]}. "
            "Should I focus on debugging, optimization, or a general code review?"
        )

    def _classify_task(self, user_input: str) -> str:
        """Backward-compatible single-label classification."""
        task_scores = self._classify_tasks(user_input)
        return task_scores[0]["task_type"]

    def _extract_code(self, context: Dict[str, Any]) -> str:
        """Extract code snippets from context"""
        if isinstance(context, dict):
            return context.get("code", "")
        return str(context) if context else ""
    
    def _detect_language(self, code: str) -> str:
        """Detect programming language from code snippet"""
        if not code:
            return "unknown"
        
        code_lower = code.lower()
        
        if "import " in code_lower or "def " in code_lower or "class " in code_lower:
            return "python"
        elif "function" in code_lower or "const " in code_lower or "let " in code_lower:
            if "console.log" in code_lower:
                return "javascript"
            elif "=>  {" in code:
                return "typescript"
            return "javascript"
        elif "public class" in code_lower or "private void" in code_lower:
            if "System.out" in code_lower:
                return "java"
            return "csharp"
        elif "#include" in code or "using namespace" in code:
            return "cpp"
        elif "SELECT" in code or "UPDATE" in code or "INSERT" in code:
            return "sql"
        elif "<!DOCTYPE" in code or "<html" in code:
            return "html"
        else:
            return "unknown"
    
    def _analyze_code(
        self,
        user_input: str,
        code: str,
        task_scores: List[Dict[str, Any]],
        language: str,
        analysis_mode: str = DEFAULT_ANALYSIS_MODE,
    ) -> Dict[str, Any]:
        """Perform specialized code analysis based on multi-task classification."""
        primary_task = task_scores[0]["task_type"]
        task_types = [task["task_type"] for task in task_scores if task["score"] >= 0.1]

        analysis = {
            "primary_task": primary_task,
            "task_types": task_types,
            "language": language,
            "input_length": len(user_input),
            "code_length": len(code),
            "suggestions": [],
            "issues_found": 0,
            "severity_level": "low",
            "analysis_mode": analysis_mode,
            "task_breakdown": [],
        }

        severity_rank = {"low": 1, "medium": 2, "high": 3}
        max_severity = 1

        for task in task_scores:
            task_type = task["task_type"]
            task_analysis = self._analysis_for_task(task_type, code, language, analysis_mode)
            task_analysis["task_score"] = task["score"]
            task_analysis["task_type"] = task_type
            task_analysis["analysis_mode"] = analysis_mode
            analysis["task_breakdown"].append(task_analysis)

            suggestions = task_analysis.get("suggestions", [])
            analysis["suggestions"].extend(suggestions)
            analysis["issues_found"] += task_analysis.get("issues_found", 0)
            max_severity = max(max_severity, severity_rank.get(task_analysis.get("severity_level", "low"), 1))

        analysis["severity_level"] = (
            "high" if max_severity == 3 else "medium" if max_severity == 2 else "low"
        )
        analysis["summary"] = (
            f"Primary task is {primary_task.replace('_', ' ')} with {len(task_types)} possible task types."
        )
        analysis["suggestions"] = list(dict.fromkeys(analysis["suggestions"]))
        return analysis

    def _analysis_for_task(
        self,
        task_type: str,
        code: str,
        language: str,
        analysis_mode: str,
    ) -> Dict[str, Any]:
        """Dispatch analysis for a specific task type."""
        if task_type == "code_review":
            return self._review_code(code, language, analysis_mode)
        if task_type == "debugging":
            return self._debug_code(code, language, analysis_mode)
        if task_type == "optimization":
            return self._optimize_code(code, language, analysis_mode)
        if task_type == "design":
            return self._analyze_design(code, language, analysis_mode)
        if task_type == "testing":
            return self._testing_analysis(code, language, analysis_mode)
        if task_type == "refactoring":
            return self._refactoring_analysis(code, language, analysis_mode)
        return {
            "analysis_type": "general_coding",
            "suggestions": [
                "Review the code carefully and identify the most urgent improvement area.",
            ],
            "issues_found": 0,
            "severity_level": "low",
        }

    def _calculate_final_confidence(self, steps: List[ReasoningStep]) -> float:
        """Aggregate step confidences into a final confidence score."""
        if not steps:
            return 0.0
        return round(sum(step.confidence for step in steps) / len(steps), 2)

    def _build_event_payload(
        self,
        primary_task: str,
        task_types: List[str],
        language: str,
        analysis: Dict[str, Any],
        confidence: float,
        execution_time_ms: int,
        analysis_mode: str,
        clarification_required: bool,
    ) -> Dict[str, Any]:
        """Create a disciplined event payload for reasoning traces."""
        return {
            "task_type": primary_task,
            "task_types": task_types,
            "language": language,
            "issues_detected": analysis.get("issues_found", 0),
            "recommendations": analysis.get("suggestions", []),
            "confidence": confidence,
            "execution_time_ms": execution_time_ms,
            "analysis_mode": analysis_mode,
            "clarification_required": clarification_required,
            "severity_level": analysis.get("severity_level", "unknown"),
            "summary": analysis.get("summary", ""),
        }

    def _record_confidence_validation(
        self,
        task_type: str,
        predicted_confidence: float,
        validation: Dict[str, Any],
    ) -> None:
        """Store predicted vs actual confidence validation records."""
        actual_outcome = validation.get("outcome_correct")
        if actual_outcome is None:
            actual_outcome = validation.get("actual_outcome") in ("correct", "success", True)

        if not hasattr(self, "confidence_validation"):
            self.confidence_validation = defaultdict(lambda: {
                "total": 0,
                "correct": 0,
                "predicted_sum": 0.0,
                "records": [],
            })

        bucket = self.confidence_validation[task_type]
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

    def _delegate_to_specialist(
        self,
        task_types: List[str],
        user_input: str,
        language: str,
        analysis_mode: str,
    ) -> Dict[str, Any]:
        """Delegate work to another specialist agent when appropriate."""
        if "testing" in task_types:
            tool_response = self.invoke_tool(
                tool_name="stitch",
                action="generate_test_plan",
                params={
                    "user_input": user_input,
                    "language": language,
                    "analysis_mode": analysis_mode,
                },
            )
            return {
                "success": tool_response.success,
                "tool": "stitch",
                "result": tool_response.result,
                "request_id": tool_response.request_id,
                "error": tool_response.error,
            }
        if "debugging" in task_types:
            tool_response = self.invoke_tool(
                tool_name="jarvis",
                action="summarize_debug_findings",
                params={"user_input": user_input, "language": language},
            )
            return {
                "success": tool_response.success,
                "tool": "jarvis",
                "result": tool_response.result,
                "request_id": tool_response.request_id,
                "error": tool_response.error,
            }
        if "security" in task_types:
            tool_response = self.invoke_tool(
                tool_name="lilo",
                action="explain_security_implications",
                params={"user_input": user_input, "language": language},
            )
            return {
                "success": tool_response.success,
                "tool": "lilo",
                "result": tool_response.result,
                "request_id": tool_response.request_id,
                "error": tool_response.error,
            }
        return {"success": True, "tool": None, "result": None, "request_id": None, "error": None}

    def _review_code(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Perform code review analysis"""
        suggestions = [
            f"Add type hints/annotations for better code clarity ({language})",
            "Break down complex logic into smaller, testable functions",
            "Add comprehensive docstrings explaining intent and behavior",
            "Consider applying SOLID principles to improve maintainability",
            f"Review naming conventions according to {language} guidelines",
        ]
        if analysis_mode == "deep":
            suggestions.append("Perform dependency and complexity analysis for high-risk code paths")

        return {
            "review_type": "static_analysis",
            "suggestions": suggestions,
            "issues_found": 4,
            "quality_score": 0.72,
            "severity_level": "medium",
        }
    
    def _debug_code(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Perform debugging analysis"""
        suggestions = [
            "Add null/None checks before accessing object properties",
            "Verify error handling for edge cases and exceptions",
            "Check for off-by-one errors in loops and array indexing",
            "Add logging/print statements to trace execution flow",
            "Validate assumptions about input values and types",
        ]
        if analysis_mode == "deep":
            suggestions.append("Simulate failure scenarios and compare actual vs expected outputs")

        return {
            "debug_type": "static_analysis",
            "suggestions": suggestions,
            "issues_found": 3,
            "priority": "high",
            "severity_level": "high",
        }
    
    def _optimize_code(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Perform performance optimization analysis"""
        suggestions = [
            "Cache repeated computations using memoization or database caches",
            f"Use {language} idioms for better performance (list comp, etc.)",
            "Profile code to identify actual bottlenecks",
            "Consider algorithm complexity (Big O analysis)",
            "Optimize database queries (avoid N+1 problems)",
        ]
        if analysis_mode == "deep":
            suggestions.append("Review memory allocation and object lifecycle for performance hotspots")

        return {
            "optimization_type": "performance",
            "suggestions": suggestions,
            "estimated_improvement": "20-40%",
            "complexity_analysis": "review needed",
            "severity_level": "low",
        }
    
    def _analyze_design(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Analyze architectural design"""
        suggestions = [
            "Apply Single Responsibility Principle (one job per class/function)",
            "Consider using dependency injection for better testability",
            "Separate concerns into different layers/modules",
            "Review coupling between components",
            "Consider design patterns appropriate for the problem",
        ]
        if analysis_mode == "deep":
            suggestions.append("Review system boundaries and integration points for future scalability")

        return {
            "design_type": "architecture",
            "suggestions": suggestions,
            "patterns_observed": ["mvc", "observer", "factory"],
            "design_score": 0.68,
            "severity_level": "medium",
        }
    
    def _testing_analysis(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Analyze testing coverage and quality"""
        suggestions = [
            "Add unit tests for all public functions/methods",
            "Increase code coverage to at least 80%",
            "Add integration tests for critical workflows",
            "Test edge cases and error conditions",
            "Use test fixtures for better test organization",
        ]
        if analysis_mode == "deep":
            suggestions.append("Correlate tests with recent bug history and risk areas")

        return {
            "testing_type": "coverage_analysis",
            "suggestions": suggestions,
            "coverage_estimate": "35%",
            "test_framework": self._guess_test_framework(language),
            "priority": "high",
            "severity_level": "high",
        }
    
    def _refactoring_analysis(self, code: str, language: str, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> Dict[str, Any]:
        """Analyze refactoring opportunities"""
        suggestions = [
            "Extract common logic into reusable functions",
            "Remove code duplication (DRY principle)",
            "Simplify complex conditionals",
            "Improve variable and function naming",
            "Update deprecated patterns and APIs",
        ]
        if analysis_mode == "deep":
            suggestions.append("Identify legacy patterns that should be replaced with modern idioms")

        return {
            "refactoring_type": "code_quality",
            "suggestions": suggestions,
            "refactoring_effort": "medium",
            "estimated_impact": "better maintainability",
            "severity_level": "low",
        }
    
    def _guess_test_framework(self, language: str) -> str:
        """Guess appropriate test framework for language"""
        if language == "python":
            return "pytest or unittest"
        elif language in ["javascript", "typescript"]:
            return "jest or mocha"
        elif language == "java":
            return "junit or testng"
        elif language == "csharp":
            return "xunit or nunit"
        else:
            return "language-specific testing framework"
    
    def _generate_coding_response(
        self,
        task_type: str,
        task_types: List[str],
        analysis: Dict[str, Any],
        language: str,
        analysis_mode: str,
    ) -> str:
        """Generate a detailed coding response"""
        suggestions = analysis.get("suggestions", [])

        response_parts = [
            f"=== CODING ANALYSIS ===",
            f"Primary Task: {task_type.title()}",
            f"Task Types: {', '.join(task_types).replace('_', ' ')}",
            f"Language: {language.upper()}",
            f"Analysis Mode: {analysis_mode.title()}",
            f"",
            f"Issues Found: {analysis.get('issues_found', 0)}",
            f"Severity: {analysis.get('severity_level', 'unknown').upper()}",
        ]
        
        if "quality_score" in analysis:
            response_parts.append(f"Quality Score: {analysis['quality_score']:.0%}")
        
        response_parts.extend([
            f"",
            "=== RECOMMENDATIONS ===",
        ])
        
        for i, suggestion in enumerate(suggestions, 1):
            response_parts.append(f"{i}. {suggestion}")
        
        if "estimated_improvement" in analysis:
            response_parts.append(f"")
            response_parts.append(f"Estimated Improvement: {analysis['estimated_improvement']}")
        
        if "test_framework" in analysis:
            response_parts.append(f"")
            response_parts.append(f"Recommended Test Framework: {analysis['test_framework']}")
        
        return "\n".join(response_parts)
