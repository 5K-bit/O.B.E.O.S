"""
Test suite for Memory Lifecycle Management (Phase 5.4)

Tests tiered storage, trace summarization, retention policies, and relevance filtering.
"""

import time
import pytest
from unittest.mock import patch

from blackfong_ecosystem.memory_lifecycle_management import (
    MemoryLifecycleManager,
    MemoryItem,
    MemoryTier,
    TraceSummarizer,
    RelevanceFilter,
    RetentionPolicy,
    memory_lifecycle_manager
)


class TestMemoryLifecycleManager:
    """Test the MemoryLifecycleManager class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.manager = MemoryLifecycleManager()

    def test_initialization(self):
        """Test manager initialization."""
        assert len(self.manager.tiers) == 3  # hot, warm, cold
        assert "hot" in self.manager.tiers
        assert "warm" in self.manager.tiers
        assert "cold" in self.manager.tiers
        assert len(self.manager.memory_store) == 0
        assert not self.manager.management_active

    def test_store_memory_item(self):
        """Test storing a memory item."""
        item_id = self.manager.store_memory_item(
            content={"test": "data"},
            memory_type="event",
            tags={"test", "sample"},
            size_bytes=100
        )

        assert item_id in self.manager.memory_store
        item = self.manager.memory_store[item_id]

        assert item.content == {"test": "data"}
        assert item.memory_type == "event"
        assert item.tier == "hot"
        assert item.size_bytes == 100
        assert item.tags == {"test", "sample"}
        assert not item.compressed

        # Check tier storage
        assert item_id in self.manager.tier_storage["hot"]

        # Check statistics
        assert self.manager.stats["total_items"] == 1
        assert self.manager.stats["items_by_tier"]["hot"] == 1
        assert self.manager.stats["items_by_type"]["event"] == 1

    def test_retrieve_memory_item(self):
        """Test retrieving a memory item."""
        item_id = self.manager.store_memory_item(
            content="test content",
            memory_type="trace"
        )

        # Wait a tiny bit to ensure different timestamps
        time.sleep(0.001)

        # Retrieve item
        item = self.manager.retrieve_memory_item(item_id)

        assert item is not None
        assert item.id == item_id
        assert item.access_count == 1
        assert item.last_accessed >= item.created_at

    def test_transition_tier(self):
        """Test transitioning an item between tiers."""
        item_id = self.manager.store_memory_item(
            content="test",
            memory_type="event"
        )

        # Transition to warm
        success = self.manager.transition_tier(item_id, "warm")
        assert success

        item = self.manager.memory_store[item_id]
        assert item.tier == "warm"

        # Check tier storage
        assert item_id not in self.manager.tier_storage["hot"]
        assert item_id in self.manager.tier_storage["warm"]

        # Check compression was applied
        assert item.compressed

        # Check statistics
        assert self.manager.stats["tier_transitions"] == 1

    def test_cleanup_expired_items(self):
        """Test cleanup of expired items."""
        # Create an item and manually expire it
        item_id = self.manager.store_memory_item(
            content="test",
            memory_type="event"
        )

        item = self.manager.memory_store[item_id]
        item.expires_at = time.time() - 1  # Expire immediately

        # Run cleanup
        removed_count = self.manager.cleanup_expired_items()

        assert removed_count == 1
        assert item_id not in self.manager.memory_store
        assert item_id not in self.manager.tier_storage["hot"]

        # Check statistics
        assert self.manager.stats["total_items"] == 0
        assert self.manager.stats["expired_items"] == 1

    def test_get_memory_stats(self):
        """Test memory statistics generation."""
        # Add some test items
        self.manager.store_memory_item("content1", "event", size_bytes=100)
        self.manager.store_memory_item("content2", "trace", size_bytes=200)
        self.manager.store_memory_item("content3", "vector", size_bytes=300)

        stats = self.manager.get_memory_stats()

        assert stats["total_items"] == 3
        assert stats["total_size_mb"] > 0
        assert stats["tier_distribution"]["hot"] == 3
        assert stats["type_distribution"]["event"] == 1
        assert stats["type_distribution"]["trace"] == 1
        assert stats["type_distribution"]["vector"] == 1

    def test_management_loop(self):
        """Test background management functionality."""
        # Start management
        self.manager.start_lifecycle_management()
        assert self.manager.management_active
        assert self.manager.management_thread is not None
        assert self.manager.management_thread.is_alive()

        # Stop management
        self.manager.stop_lifecycle_management()
        assert not self.manager.management_active


class TestTraceSummarizer:
    """Test the TraceSummarizer class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.summarizer = TraceSummarizer()

    def test_should_summarize(self):
        """Test summarization decision logic."""
        # Recent trace should not be summarized
        recent_trace = {"created_at": time.time() - (10 * 24 * 60 * 60)}  # 10 days ago
        assert not self.summarizer.should_summarize(recent_trace, time.time())

        # Old trace should be summarized
        old_trace = {"created_at": time.time() - (40 * 24 * 60 * 60)}  # 40 days ago
        assert self.summarizer.should_summarize(old_trace, time.time())

    def test_summarize_trace(self):
        """Test trace summarization."""
        trace = {
            "trace_id": "test-123",
            "user_input": "Help me with Python programming",
            "final_decision": "Route to JARVIS agent",
            "outcome": "Successfully processed",
            "steps": [
                {"action": "Analyze input", "success": True},
                {"action": "Check keywords", "success": True},
                {"action": "Route to agent", "success": True},
                {"action": "Execute task", "success": True},
                {"action": "Return result", "success": True},
            ]
        }

        summary = self.summarizer.summarize_trace(trace)

        assert "test-123" in summary
        assert "Help me with Python programming" in summary
        assert "Route to JARVIS agent" in summary
        assert "Successfully processed" in summary
        assert "5 total (5 successful)" in summary

    def test_compress_trace(self):
        """Test trace compression."""
        trace = {
            "trace_id": "test-456",
            "user_input": "Test input",
            "final_decision": "Test decision",
            "outcome": "Test outcome",
            "created_at": time.time(),
            "steps": [{"action": "step1", "success": True}, {"action": "step2", "success": False}],
            "metrics": {
                "total_time": 5.5,
                "success_rate": 0.8,
                "decision_confidence": 0.9,
                "other_metric": "ignored"
            }
        }

        compressed = self.summarizer.compress_trace(trace)

        assert compressed["trace_id"] == "test-456"
        assert compressed["compressed"] is True
        assert compressed["original_step_count"] == 2
        assert "summary" in compressed
        assert "metrics" in compressed

        # Check that only essential metrics are kept
        assert "total_time" in compressed["metrics"]
        assert "success_rate" in compressed["metrics"]
        assert "decision_confidence" in compressed["metrics"]
        assert "other_metric" not in compressed["metrics"]


class TestRelevanceFilter:
    """Test the RelevanceFilter class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.filter = RelevanceFilter()

    def test_calculate_relevance_no_context(self):
        """Test relevance calculation without context."""
        item = MemoryItem(
            id="test-1",
            content="test",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=5,
            size_bytes=1000
        )

        relevance = self.filter.calculate_relevance(item)

        assert 0.0 <= relevance <= 1.0

    def test_calculate_relevance_with_context(self):
        """Test relevance calculation with context."""
        item = MemoryItem(
            id="test-2",
            content="test",
            memory_type="trace",
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=10,
            size_bytes=500,
            tags={"python", "programming"}
        )

        context = {"tags": ["python", "coding"]}

        relevance = self.filter.calculate_relevance(item, context)

        # Should be higher due to tag overlap
        assert relevance > 0.5

    def test_should_retain(self):
        """Test retention decision."""
        item = MemoryItem(
            id="test-3",
            content="test",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            relevance_score=0.8
        )

        assert self.filter.should_retain(item, threshold=0.5)
        assert not self.filter.should_retain(item, threshold=0.9)


class TestRetentionPolicy:
    """Test the RetentionPolicy class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.policy = RetentionPolicy()

    def test_get_policy(self):
        """Test policy retrieval."""
        event_policy = self.policy.get_policy("event")
        assert event_policy["max_age_days"] == 365
        assert event_policy["min_relevance"] == 0.2

        vector_policy = self.policy.get_policy("vector")
        assert vector_policy["max_age_days"] == 90
        assert vector_policy["min_relevance"] == 0.4

    def test_should_expire(self):
        """Test expiration decision."""
        # Create an old, low-relevance item
        old_item = MemoryItem(
            id="old-1",
            content="old",
            memory_type="event",
            created_at=time.time() - (400 * 24 * 60 * 60),  # 400 days ago
            last_accessed=time.time() - (400 * 24 * 60 * 60),
            relevance_score=0.1
        )

        assert self.policy.should_expire(old_item)

        # Create a recent, high-relevance item
        recent_item = MemoryItem(
            id="recent-1",
            content="recent",
            memory_type="event",
            created_at=time.time() - (10 * 24 * 60 * 60),  # 10 days ago
            last_accessed=time.time(),
            relevance_score=0.9
        )

        assert not self.policy.should_expire(recent_item)

    def test_get_next_tier(self):
        """Test tier progression."""
        hot_item = MemoryItem(
            id="hot-1",
            content="hot",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            tier="hot"
        )

        next_tier = self.policy.get_next_tier(hot_item)
        assert next_tier == "warm"

        cold_item = MemoryItem(
            id="cold-1",
            content="cold",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            tier="cold"
        )

        next_tier = self.policy.get_next_tier(cold_item)
        assert next_tier is None


class TestMemoryItem:
    """Test the MemoryItem dataclass."""

    def test_creation(self):
        """Test MemoryItem creation."""
        item = MemoryItem(
            id="test-123",
            content={"data": "value"},
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=5,
            size_bytes=1024,
            relevance_score=0.8,
            tags={"tag1", "tag2"},
            tier="warm",
            compressed=True,
            summary="Test summary"
        )

        assert item.id == "test-123"
        assert item.content == {"data": "value"}
        assert item.memory_type == "event"
        assert item.access_count == 5
        assert item.relevance_score == 0.8
        assert item.tags == {"tag1", "tag2"}
        assert item.tier == "warm"
        assert item.compressed is True
        assert item.summary == "Test summary"

    def test_is_expired(self):
        """Test expiration check."""
        # Non-expired item
        item = MemoryItem(
            id="test-1",
            content="test",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            expires_at=time.time() + 3600  # Expires in 1 hour
        )

        assert not item.is_expired()

        # Expired item
        expired_item = MemoryItem(
            id="test-2",
            content="test",
            memory_type="event",
            created_at=time.time(),
            last_accessed=time.time(),
            expires_at=time.time() - 1  # Expired 1 second ago
        )

        assert expired_item.is_expired()

    def test_calculate_priority_score(self):
        """Test priority score calculation."""
        item = MemoryItem(
            id="test-1",
            content="test",
            memory_type="event",
            created_at=time.time() - 3600,  # 1 hour ago
            last_accessed=time.time(),
            access_count=20,
            size_bytes=1024,
            relevance_score=0.8
        )

        score = item.calculate_priority_score()

        assert 0.0 <= score <= 1.0


def test_global_instance():
    """Test the global memory lifecycle manager instance."""
    assert isinstance(memory_lifecycle_manager, MemoryLifecycleManager)
    assert len(memory_lifecycle_manager.tiers) == 3


if __name__ == "__main__":
    pytest.main([__file__])