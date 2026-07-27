from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

@dataclass
class InterrogationResult:
    confidence: float
    tier: str
    escalated: bool
    flag: str | None
    reasons: List[str]
    questions: List[str]
    instability_score: float
    rule_ids_triggered: List[str]

class Mobray:
    RULE_WEIGHTS = {
        "A": 0.5,
        "B": 0.3,
        "C": 0.4,
    }

    def rule_A_unsupported_certainty(self, confidence: float, recent_events: List[Dict[str, Any]]) -> bool:
        return confidence >= 0.85 and len(recent_events) == 0

    def rule_B_context_overfit(self, confidence: float, semantic_context: List[Any], recent_events: List[Dict[str, Any]]) -> bool:
        return confidence >= 0.8 and len(semantic_context) > 3 and len(recent_events) == 0

    def rule_C_negation_conflict(self, hypothesis: str, recent_events: List[Dict[str, Any]], confidence: float) -> bool:
        negation_tokens = {"not", "no", "never", "absent"}
        words = hypothesis.lower().split()
        has_negation = any(token in words for token in negation_tokens)
        if not has_negation or not recent_events:
            return False
        negation_in_events = any(
            any(token in str(event.get("payload", "")).lower().split() for token in negation_tokens)
            for event in recent_events
        )
        return not negation_in_events and confidence >= 0.75

    def interrogate(self, hypothesis: str, context: Dict[str, Any], confidence: float) -> InterrogationResult:
        recent_events = context.get("recent_events", [])
        semantic_context = context.get("semantic_context", [])

        # Determine tier
        if confidence < 0.4:
            tier = "LIGHT"
            question_count = 2
        elif confidence < 0.7:
            tier = "STANDARD"
            question_count = 4
        elif confidence < 0.85:
            tier = "DEEP"
            question_count = 6
        else:
            tier = "CRITICAL"
            question_count = 8

        # Check rules
        rule_ids = []
        reasons = []
        questions = []
        if self.rule_A_unsupported_certainty(confidence, recent_events):
            rule_ids.append("A")
            reasons.append("Unsupported certainty: high confidence with no recent events")
            questions.append("What evidence supports this high certainty?")
        if self.rule_B_context_overfit(confidence, semantic_context, recent_events):
            rule_ids.append("B")
            reasons.append("Context overfit: reliance on semantic context without recent events")
            questions.append("Is semantic context being over-relied upon?")
        if self.rule_C_negation_conflict(hypothesis, recent_events, confidence):
            rule_ids.append("C")
            reasons.append("Negation conflict: hypothesis negation not reflected in events")
            questions.append("How does the negation reconcile with recent events?")

        # Generate questions based on tier
        while len(questions) < question_count:
            n = len(questions) + 1
            questions.append(f"[{tier}] Structural probe #{n}")

        escalated = bool(rule_ids)
        flag = "hypothesis_unstable" if escalated else None

        # Instability score
        base = 0.0
        for rid in rule_ids:
            base += self.RULE_WEIGHTS.get(rid, 0.0)
        instability_score = min(1.0, base)

        return InterrogationResult(
            confidence=confidence,
            tier=tier,
            escalated=escalated,
            flag=flag,
            reasons=reasons,
            questions=questions,
            instability_score=instability_score,
            rule_ids_triggered=rule_ids,
        )