"""
Memory Lifecycle Management for O.B.E.O.S

Phase 5.4: Memory Lifecycle Management
Builds tiered storage, trace summarization, retention policies, and relevance filtering
for efficient memory management across Event Store, Indexed Memory, and Vector Memory.

Principles:
- Memory has a lifecycle: creation → active use → summarization → archival → deletion
- Relevance-based retention with automatic cleanup
- Tiered storage for cost/performance optimization
- Trace summarization to maintain context without full history
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import json

from blackfong_ecosystem.observability import log_action, LogLevel


@dataclass
class MemoryTier:
    """Configuration for a memory storage tier."""
    name: str
    max_age_days: int
    max_size_mb: float
    retention_priority: int  # Higher = more important to keep
    compression_enabled: bool = False
    access_cost: float = 1.0  # Relative cost to access this tier
    description: str = ""


@dataclass
class MemoryItem:
    """Represents a memory item with metadata for lifecycle management."""
    id: str
    content: Any
    memory_type: str  # 'event', 'trace', 'vector', 'indexed'
    created_at: float
    last_accessed: float
    access_count: int = 0
    size_bytes: int = 0
    relevance_score: float = 1.0
    tags: Set[str] = field(default_factory=set)
    tier: str = "hot"
    compressed: bool = False
    summary: Optional[str] = None
    expires_at: Optional[float] = None

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.created_at + (365 * 24 * 60 * 60)  # Default 1 year

    def is_expired(self) -> bool:
        """Check if memory item has expired."""
        return time.time() > self.expires_at

    def calculate_priority_score(self) -> float:
        """Calculate priority score for retention decisions."""
        # Factors: relevance, recency, access frequency, size efficiency
        recency_score = 1.0 / (1.0 + (time.time() - self.last_accessed) / (24 * 60 * 60))  # Days
        access_score = min(1.0, self.access_count / 100.0)  # Normalize access count
        size_efficiency = 1.0 / (1.0 + self.size_bytes / (1024 * 1024))  # MB penalty

        return (
            self.relevance_score * 0.4 +
            recency_score * 0.3 +
            access_score * 0.2 +
            size_efficiency * 0.1
        )


class TraceSummarizer:
    """
    Summarizes reasoning traces to maintain context without full history.

    Reduces storage requirements while preserving key insights.
    """

    def __init__(self):
        self.summarization_rules = {
            "max_steps": 10,  # Maximum steps to keep in detail
            "summary_threshold_days": 30,  # Summarize after this many days
            "compression_ratio": 0.1,  # Target compression ratio
        }

    def should_summarize(self, trace: Dict[str, Any], current_time: float) -> bool:
        """Determine if a trace should be summarized."""
        created_at = trace.get("created_at", current_time)
        age_days = (current_time - created_at) / (24 * 60 * 60)

        return age_days > self.summarization_rules["summary_threshold_days"]

    def summarize_trace(self, trace: Dict[str, Any]) -> str:
        """
        Create a summary of a reasoning trace.

        Preserves key decisions, outcomes, and learnings while reducing detail.
        """
        trace_id = trace.get("trace_id", "unknown")
        user_input = trace.get("user_input", "")
        final_decision = trace.get("final_decision", "")
        steps = trace.get("steps", [])

        # Extract key information
        total_steps = len(steps)
        successful_steps = sum(1 for step in steps if step.get("success", False))
        key_actions = [step.get("action", "") for step in steps[:3]]  # First 3 actions
        key_actions.extend([step.get("action", "") for step in steps[-2:]])  # Last 2 actions

        # Create summary
        summary_parts = [
            f"Trace {trace_id}: {user_input[:100]}{'...' if len(user_input) > 100 else ''}",
            f"Decision: {final_decision}",
            f"Steps: {total_steps} total ({successful_steps} successful)",
        ]

        if key_actions:
            summary_parts.append(f"Key actions: {' → '.join(key_actions[:5])}")

        # Add outcome if available
        outcome = trace.get("outcome", "")
        if outcome:
            summary_parts.append(f"Outcome: {outcome}")

        return " | ".join(summary_parts)

    def compress_trace(self, trace: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compress a trace by removing detailed step information.

        Keeps summary, key metrics, and essential metadata.
        """
        compressed = {
            "trace_id": trace.get("trace_id"),
            "user_input": trace.get("user_input", ""),
            "final_decision": trace.get("final_decision", ""),
            "outcome": trace.get("outcome", ""),
            "created_at": trace.get("created_at"),
            "compressed": True,
            "original_step_count": len(trace.get("steps", [])),
            "summary": self.summarize_trace(trace),
        }

        # Keep only essential metrics
        if "metrics" in trace:
            compressed["metrics"] = {
                k: v for k, v in trace["metrics"].items()
                if k in ["total_time", "success_rate", "decision_confidence"]
            }

        return compressed


class RelevanceFilter:
    """
    Filters memory items based on relevance to current context and needs.

    Uses multiple signals to determine what should be retained.
    """

    def __init__(self):
        self.relevance_factors = {
            "temporal_decay": 0.3,  # Recent items more relevant
            "access_frequency": 0.25,  # Frequently accessed items more relevant
            "semantic_similarity": 0.25,  # Semantically similar to current context
            "domain_importance": 0.2,  # Domain-specific importance
        }

    def calculate_relevance(
        self,
        item: MemoryItem,
        current_context: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate relevance score for a memory item.

        Returns score between 0.0 and 1.0.
        """
        if current_context is None:
            current_context = {}

        scores = {}

        # Temporal decay - recent items are more relevant
        age_hours = (time.time() - item.created_at) / 3600
        scores["temporal_decay"] = max(0.1, 1.0 / (1.0 + age_hours / 24))  # Half-life of 1 day

        # Access frequency - frequently accessed items are more relevant
        scores["access_frequency"] = min(1.0, item.access_count / 10.0)

        # Semantic similarity - compare to current context
        context_tags = set(current_context.get("tags", []))
        item_tags = item.tags
        if context_tags and item_tags:
            overlap = len(context_tags & item_tags)
            union = len(context_tags | item_tags)
            scores["semantic_similarity"] = overlap / union if union > 0 else 0.0
        else:
            scores["semantic_similarity"] = 0.5  # Neutral if no tags

        # Domain importance - certain types of memory are more important
        importance_map = {
            "event": 0.8,  # Events are important for audit trails
            "trace": 0.9,  # Traces show reasoning patterns
            "vector": 0.6,  # Vector embeddings are computationally expensive
            "indexed": 0.7,  # Indexed data supports search
        }
        scores["domain_importance"] = importance_map.get(item.memory_type, 0.5)

        # Weighted combination
        relevance_score = sum(
            scores[factor] * weight
            for factor, weight in self.relevance_factors.items()
        )

        return min(1.0, max(0.0, relevance_score))

    def should_retain(self, item: MemoryItem, threshold: float = 0.3) -> bool:
        """Determine if a memory item should be retained."""
        return item.relevance_score >= threshold


class RetentionPolicy:
    """
    Defines retention policies for different types of memory.

    Combines time-based, size-based, and relevance-based retention rules.
    """

    def __init__(self):
        self.policies = {
            "event": {
                "max_age_days": 365,  # Keep events for 1 year
                "min_relevance": 0.2,
                "tier_progression": ["hot", "warm", "cold"],
            },
            "trace": {
                "max_age_days": 180,  # Keep traces for 6 months
                "min_relevance": 0.3,
                "tier_progression": ["hot", "warm", "cold"],
            },
            "vector": {
                "max_age_days": 90,  # Keep vectors for 3 months
                "min_relevance": 0.4,
                "tier_progression": ["hot", "warm"],
            },
            "indexed": {
                "max_age_days": 270,  # Keep indexed data for 9 months
                "min_relevance": 0.25,
                "tier_progression": ["hot", "warm", "cold"],
            },
        }

    def get_policy(self, memory_type: str) -> Dict[str, Any]:
        """Get retention policy for a memory type."""
        return self.policies.get(memory_type, self.policies["event"])

    def should_expire(self, item: MemoryItem) -> bool:
        """Determine if a memory item should expire."""
        policy = self.get_policy(item.memory_type)

        # Check age
        age_days = (time.time() - item.created_at) / (24 * 60 * 60)
        if age_days > policy["max_age_days"]:
            return True

        # Check relevance
        if item.relevance_score < policy["min_relevance"]:
            return True

        return False

    def get_next_tier(self, item: MemoryItem) -> Optional[str]:
        """Get the next tier for a memory item."""
        policy = self.get_policy(item.memory_type)
        current_index = policy["tier_progression"].index(item.tier)

        if current_index < len(policy["tier_progression"]) - 1:
            return policy["tier_progression"][current_index + 1]

        return None


class MemoryLifecycleManager:
    """
    Manages the complete lifecycle of memory items across all storage tiers.

    Orchestrates summarization, tiering, retention, and cleanup operations.
    """

    def __init__(self):
        self.tiers = self._setup_tiers()
        self.trace_summarizer = TraceSummarizer()
        self.relevance_filter = RelevanceFilter()
        self.retention_policy = RetentionPolicy()

        # Memory storage (in production, this would integrate with actual storage)
        self.memory_store: Dict[str, MemoryItem] = {}
        self.tier_storage: Dict[str, Dict[str, MemoryItem]] = defaultdict(dict)

        # Background management
        self.management_active = False
        self.management_thread: Optional[threading.Thread] = None

        # Statistics
        self.stats = {
            "total_items": 0,
            "items_by_tier": defaultdict(int),
            "items_by_type": defaultdict(int),
            "summarized_traces": 0,
            "expired_items": 0,
            "tier_transitions": 0,
        }

    def _setup_tiers(self) -> Dict[str, MemoryTier]:
        """Setup default memory tiers."""
        return {
            "hot": MemoryTier(
                name="hot",
                max_age_days=7,
                max_size_mb=100.0,
                retention_priority=10,
                compression_enabled=False,
                access_cost=1.0,
                description="High-performance, frequently accessed memory"
            ),
            "warm": MemoryTier(
                name="warm",
                max_age_days=30,
                max_size_mb=500.0,
                retention_priority=5,
                compression_enabled=True,
                access_cost=2.0,
                description="Medium-performance, occasionally accessed memory"
            ),
            "cold": MemoryTier(
                name="cold",
                max_age_days=365,
                max_size_mb=2000.0,
                retention_priority=1,
                compression_enabled=True,
                access_cost=5.0,
                description="Low-performance, rarely accessed archival memory"
            ),
        }

    def store_memory_item(
        self,
        content: Any,
        memory_type: str,
        tags: Optional[Set[str]] = None,
        size_bytes: int = 0
    ) -> str:
        """
        Store a new memory item in the lifecycle management system.

        Returns the memory item ID.
        """
        item_id = f"{memory_type}_{int(time.time())}_{hash(str(content))}"

        item = MemoryItem(
            id=item_id,
            content=content,
            memory_type=memory_type,
            created_at=time.time(),
            last_accessed=time.time(),
            size_bytes=size_bytes or len(str(content).encode('utf-8')),
            tags=tags or set(),
        )

        # Calculate initial relevance
        item.relevance_score = self.relevance_filter.calculate_relevance(item)

        # Store in hot tier initially
        self.memory_store[item_id] = item
        self.tier_storage["hot"][item_id] = item

        # Update statistics
        self.stats["total_items"] += 1
        self.stats["items_by_tier"]["hot"] += 1
        self.stats["items_by_type"][memory_type] += 1

        log_action(
            LogLevel.INFO,
            "memory_lifecycle",
            "item_stored",
            item_id=item_id,
            memory_type=memory_type,
            tier="hot",
            relevance_score=item.relevance_score
        )

        return item_id

    def retrieve_memory_item(self, item_id: str) -> Optional[MemoryItem]:
        """
        Retrieve a memory item, updating access statistics.
        """
        if item_id not in self.memory_store:
            return None

        item = self.memory_store[item_id]
        item.last_accessed = time.time()
        item.access_count += 1

        # Update relevance based on access
        item.relevance_score = self.relevance_filter.calculate_relevance(item)

        log_action(
            LogLevel.DEBUG,
            "memory_lifecycle",
            "item_accessed",
            item_id=item_id,
            access_count=item.access_count,
            relevance_score=item.relevance_score
        )

        return item

    def summarize_trace(self, trace_id: str) -> bool:
        """
        Summarize a reasoning trace if it meets summarization criteria.

        Returns True if summarization was performed.
        """
        item = self.memory_store.get(trace_id)
        if not item or item.memory_type != "trace":
            return False

        trace_data = item.content
        if not isinstance(trace_data, dict):
            return False

        if not self.trace_summarizer.should_summarize(trace_data, time.time()):
            return False

        # Create summary
        summary = self.trace_summarizer.summarize_trace(trace_data)

        # Compress the trace
        compressed_trace = self.trace_summarizer.compress_trace(trace_data)

        # Update the memory item
        item.summary = summary
        item.content = compressed_trace
        item.compressed = True
        item.size_bytes = len(json.dumps(compressed_trace).encode('utf-8'))

        self.stats["summarized_traces"] += 1

        log_action(
            LogLevel.INFO,
            "memory_lifecycle",
            "trace_summarized",
            trace_id=trace_id,
            original_size=len(json.dumps(trace_data).encode('utf-8')),
            compressed_size=item.size_bytes
        )

        return True

    def transition_tier(self, item_id: str, new_tier: str) -> bool:
        """
        Transition a memory item to a different tier.

        Returns True if transition was successful.
        """
        if item_id not in self.memory_store:
            return False

        item = self.memory_store[item_id]
        old_tier = item.tier

        if old_tier == new_tier:
            return False

        # Check if new tier exists
        if new_tier not in self.tiers:
            return False

        # Remove from old tier
        if item_id in self.tier_storage[old_tier]:
            del self.tier_storage[old_tier][item_id]
            self.stats["items_by_tier"][old_tier] -= 1

        # Add to new tier
        item.tier = new_tier
        self.tier_storage[new_tier][item_id] = item
        self.stats["items_by_tier"][new_tier] += 1

        # Apply compression if enabled for new tier
        if self.tiers[new_tier].compression_enabled and not item.compressed:
            # Simple compression simulation - reduce size by 50%
            item.size_bytes = int(item.size_bytes * 0.5)
            item.compressed = True

        self.stats["tier_transitions"] += 1

        log_action(
            LogLevel.INFO,
            "memory_lifecycle",
            "tier_transition",
            item_id=item_id,
            old_tier=old_tier,
            new_tier=new_tier,
            compressed=item.compressed
        )

        return True

    def cleanup_expired_items(self) -> int:
        """
        Remove expired memory items based on retention policies.

        Returns the number of items removed.
        """
        expired_items = []
        current_time = time.time()

        for item_id, item in self.memory_store.items():
            if item.is_expired() or self.retention_policy.should_expire(item):
                expired_items.append(item_id)

        # Remove expired items
        for item_id in expired_items:
            item = self.memory_store[item_id]
            tier = item.tier

            # Remove from storage
            del self.memory_store[item_id]
            if item_id in self.tier_storage[tier]:
                del self.tier_storage[tier][item_id]

            # Update statistics
            self.stats["total_items"] -= 1
            self.stats["items_by_tier"][tier] -= 1
            self.stats["items_by_type"][item.memory_type] -= 1
            self.stats["expired_items"] += 1

        if expired_items:
            log_action(
                LogLevel.INFO,
                "memory_lifecycle",
                "cleanup_completed",
                expired_count=len(expired_items),
                remaining_items=self.stats["total_items"]
            )

        return len(expired_items)

    def optimize_tier_distribution(self) -> Dict[str, int]:
        """
        Optimize memory distribution across tiers based on access patterns and policies.

        Returns statistics about transitions performed.
        """
        transitions = {"promotions": 0, "demotions": 0}

        # Check for items that should move to warmer tiers
        for tier_name, tier_items in self.tier_storage.items():
            tier_config = self.tiers[tier_name]

            for item_id, item in list(tier_items.items()):
                age_days = (time.time() - item.created_at) / (24 * 60 * 60)

                # Check if item should move to next tier
                if age_days > tier_config.max_age_days:
                    next_tier = self.retention_policy.get_next_tier(item)
                    if next_tier:
                        if self.transition_tier(item_id, next_tier):
                            transitions["demotions"] += 1

                # Check if frequently accessed items should be promoted
                elif item.access_count > 50 and tier_name != "hot":
                    if self.transition_tier(item_id, "hot"):
                        transitions["promotions"] += 1

        return transitions

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get comprehensive memory lifecycle statistics."""
        total_size = sum(item.size_bytes for item in self.memory_store.values())
        tier_sizes = {}

        for tier_name, items in self.tier_storage.items():
            tier_sizes[tier_name] = sum(item.size_bytes for item in items.values())

        return {
            "total_items": self.stats["total_items"],
            "total_size_mb": total_size / (1024 * 1024),
            "tier_distribution": dict(self.stats["items_by_tier"]),
            "type_distribution": dict(self.stats["items_by_type"]),
            "tier_sizes_mb": {tier: size / (1024 * 1024) for tier, size in tier_sizes.items()},
            "summarized_traces": self.stats["summarized_traces"],
            "expired_items": self.stats["expired_items"],
            "tier_transitions": self.stats["tier_transitions"],
            "average_relevance": (
                sum(item.relevance_score for item in self.memory_store.values()) /
                len(self.memory_store) if self.memory_store else 0
            ),
        }

    def start_lifecycle_management(self) -> None:
        """Start background lifecycle management."""
        if self.management_active:
            return

        self.management_active = True
        self.management_thread = threading.Thread(
            target=self._lifecycle_management_loop,
            daemon=True
        )
        self.management_thread.start()

        log_action(LogLevel.INFO, "memory_lifecycle", "management_started")

    def stop_lifecycle_management(self) -> None:
        """Stop background lifecycle management."""
        self.management_active = False
        if self.management_thread:
            self.management_thread.join(timeout=5)

        log_action(LogLevel.INFO, "memory_lifecycle", "management_stopped")

    def _lifecycle_management_loop(self) -> None:
        """Background lifecycle management loop."""
        while self.management_active:
            try:
                # Perform maintenance tasks
                expired_count = self.cleanup_expired_items()
                transitions = self.optimize_tier_distribution()

                # Summarize old traces
                trace_items = [
                    item_id for item_id, item in self.memory_store.items()
                    if item.memory_type == "trace" and not item.compressed
                ]

                summarized_count = 0
                for item_id in trace_items[:10]:  # Limit to 10 per cycle
                    if self.summarize_trace(item_id):
                        summarized_count += 1

                # Log periodic summary
                if expired_count > 0 or transitions["promotions"] > 0 or transitions["demotions"] > 0 or summarized_count > 0:
                    log_action(
                        LogLevel.INFO,
                        "memory_lifecycle",
                        "maintenance_cycle",
                        expired=expired_count,
                        promotions=transitions["promotions"],
                        demotions=transitions["demotions"],
                        summarized=summarized_count,
                        total_items=self.stats["total_items"]
                    )

                # Sleep for 1 hour
                time.sleep(3600)

            except Exception as e:
                log_action(
                    LogLevel.ERROR,
                    "memory_lifecycle",
                    "management_loop_error",
                    error_message=str(e)
                )
                time.sleep(300)


# Global memory lifecycle manager instance
memory_lifecycle_manager = MemoryLifecycleManager()