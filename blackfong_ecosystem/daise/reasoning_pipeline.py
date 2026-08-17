from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

ModelCallable = Callable[[str, "ReasoningContext"], str]
ToolCallable = Callable[[str, "ReasoningContext"], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ReasoningContext:
    """Context supplied to a model. Retrieval is evidence, never the reasoning engine."""

    conversation: Sequence[Mapping[str, Any]] = ()
    memory: Sequence[Mapping[str, Any]] = ()
    system_state: Mapping[str, Any] = field(default_factory=dict)
    tool_results: Sequence[Mapping[str, Any]] = ()
    instructions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReasoningPipeline:
    """Explicit DAISE v1 lane: model -> context -> memory -> tools.

    The model is the response generator. Memory/retrieval only grounds the model and must
    never be returned as a substitute answer merely because retrieval succeeded.
    """

    model: ModelCallable
    tools: Mapping[str, ToolCallable] = field(default_factory=dict)

    def answer(
        self,
        user_input: str,
        *,
        conversation: Sequence[Mapping[str, Any]] = (),
        memory: Sequence[Mapping[str, Any]] = (),
        system_state: Mapping[str, Any] | None = None,
        requested_tools: Sequence[str] = (),
        instructions: Mapping[str, Any] | None = None,
    ) -> str:
        base = ReasoningContext(
            conversation=conversation,
            memory=memory,
            system_state=system_state or {},
            instructions=instructions or {},
        )
        results: list[Mapping[str, Any]] = []
        for name in requested_tools:
            tool = self.tools.get(name)
            if tool is None:
                results.append({"tool": name, "ok": False, "error": "tool_unavailable"})
                continue
            try:
                result = dict(tool(user_input, base))
                results.append({"tool": name, "ok": True, **result})
            except Exception as exc:
                results.append({"tool": name, "ok": False, "error": str(exc)})
        context = ReasoningContext(
            conversation=conversation,
            memory=memory,
            system_state=system_state or {},
            tool_results=tuple(results),
            instructions=instructions or {},
        )
        response = self.model(user_input, context)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("DAISE model lane returned no usable response")
        return response.strip()
