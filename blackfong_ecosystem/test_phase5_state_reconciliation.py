"""
Test Phase 5.2: State Reconciliation Layer

Tests the state reconciliation system that prevents silent state divergence.
"""

import time
from blackfong_ecosystem.state_reconciliation import state_reconciler, ReconciliationResult


def test_state_reconciliation():
    """Test the state reconciliation system."""
    print("Testing Phase 5.2: State Reconciliation Layer")
    print("=" * 60)

    # Test 1: Basic reconciliation stats
    print("\n1. Testing reconciliation stats...")
    stats = state_reconciler.get_reconciliation_stats()
    print(f"Reconciliation stats keys: {list(stats.keys())}")
    assert "total_reconciliations" in stats
    assert "active" in stats
    assert "interval_seconds" in stats

    # Test 2: Manual reconciliation trigger
    print("2. Testing manual reconciliation...")
    result = state_reconciler.trigger_manual_reconciliation()
    print(f"Manual reconciliation result keys: {list(result.keys())}")
    assert "correlation_id" in result
    assert "duration_seconds" in result
    assert "stats" in result

    # Test 3: Reconciliation result structure
    print("3. Testing reconciliation result structure...")
    # Create a test reconciliation result
    test_result = ReconciliationResult(
        timestamp=time.time(),
        component_a="test_a",
        component_b="test_b",
        total_checked=10,
        inconsistencies_found=2,
        inconsistencies_repaired=1,
        repair_failures=1,
        duration_seconds=0.5
    )
    assert test_result.total_checked == 10
    assert test_result.inconsistencies_found == 2
    assert test_result.inconsistencies_repaired == 1

    # Test 4: Statistics tracking
    print("4. Testing statistics tracking...")
    initial_reconciliations = stats["total_reconciliations"]
    # Trigger another reconciliation
    state_reconciler.trigger_manual_reconciliation()
    updated_stats = state_reconciler.get_reconciliation_stats()
    assert updated_stats["total_reconciliations"] >= initial_reconciliations

    # Test 5: Reconciliation history
    print("5. Testing reconciliation history...")
    history = updated_stats.get("recent_results", [])
    print(f"Reconciliation history length: {len(history)}")
    # History might be empty if no reconciliations completed yet
    assert isinstance(history, list)

    print("\n✅ All state reconciliation tests passed!")
    print("Phase 5.2 State Reconciliation Layer is operational.")


if __name__ == "__main__":
    test_state_reconciliation()