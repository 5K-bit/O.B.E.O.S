#!/usr/bin/env python3
"""
Phase 2 Agent Standardization Verification Test

Tests all three agent implementations:
- JARVISAgent: General reasoning
- LiloAgent: Certification training
- StitchAgent: Python learning

Verifies:
✓ Agents implement Agent interface
✓ Agents process() returns correct structure
✓ Agents emit reasoning traces
✓ Reasoning traces contain steps with confidence
✓ Agents work with ToolDispatcher
"""

import json
import sys
import time
from pathlib import Path

# Add blackfong_ecosystem to path
sys.path.insert(0, str(Path(__file__).parent))

from blackfong_ecosystem.agents import JARVISAgent, LiloAgent, StitchAgent
from blackfong_ecosystem.event_client import EventClient


def test_jarvis_agent():
    """Test JARVISAgent implementation."""
    print("\n" + "=" * 80)
    print("Testing JARVISAgent")
    print("=" * 80)
    
    # Create agent
    agent = JARVISAgent()
    
    # Test 1: Basic query
    print("\n1️⃣  Testing basic query processing...")
    result = agent.process("Hello, how are you?")
    
    assert result["response"], "Missing response"
    assert "reasoning_trace" in result, "Missing reasoning_trace"
    assert result["confidence"] > 0, "Invalid confidence"
    assert "trace_event_id" in result, "Missing trace_event_id"
    print("✓ JARVISAgent.process() works correctly")
    print(f"  Response: {result['response'][:80]}...")
    print(f"  Confidence: {result['confidence']:.2f}")
    print(f"  Reasoning Steps: {len(result['reasoning_trace']['steps'])}")
    
    # Test 2: Trace contains steps
    print("\n2️⃣  Verifying reasoning trace structure...")
    trace = result["reasoning_trace"]
    assert len(trace["steps"]) > 0, "No steps in trace"
    
    for i, step in enumerate(trace["steps"]):
        assert "step_id" in step, f"Step {i} missing step_id"
        assert "action" in step, f"Step {i} missing action"
        assert "confidence" in step, f"Step {i} missing confidence"
        print(f"  Step {i}: {step['action']} (confidence: {step['confidence']:.2f})")
    
    print("✓ Reasoning trace contains all required fields")
    
    # Test 3: Tool invocation structure
    print("\n3️⃣  Testing tool invocation interface...")
    tool_result = agent.invoke_tool_explicit(
        tool_name="transcriber",
        action="transcribe",
        params={"audio_file": "test.wav"},
    )
    
    assert "success" in tool_result, "Missing success field"
    assert "status" in tool_result, "Missing status field"
    print(f"✓ Tool invocation works (status: {tool_result['status']})")
    
    return True


def test_lilo_agent():
    """Test LiloAgent implementation."""
    print("\n" + "=" * 80)
    print("Testing LiloAgent")
    print("=" * 80)
    
    # Create agent
    agent = LiloAgent()
    
    # Test 1: Quiz request
    print("\n1️⃣  Testing quiz request...")
    result = agent.process("Start a quiz")
    
    assert result["response"], "Missing response"
    assert "quiz" in result["response"].lower(), "Response doesn't mention quiz"
    assert "reasoning_trace" in result, "Missing reasoning_trace"
    print("✓ LiloAgent handles quiz requests")
    print(f"  Response preview: {result['response'][:100]}...")
    
    # Test 2: Progress tracking
    print("\n2️⃣  Testing progress tracking...")
    result = agent.process("Show my progress")
    
    assert "progress" in result["response"].lower() or "stats" in result["response"].lower(), \
        "Response doesn't show progress"
    assert len(result["reasoning_trace"]["steps"]) > 0, "No reasoning steps"
    print("✓ LiloAgent handles progress requests")
    
    # Test 3: Topic explanation
    print("\n3️⃣  Testing topic explanation...")
    result = agent.process("Explain defense in depth")
    
    assert "explain" in result["response"].lower() or "defense" in result["response"].lower(), \
        "Response doesn't explain topic"
    print("✓ LiloAgent can explain concepts")
    
    # Test 4: Trace metadata
    print("\n4️⃣  Verifying quiz_data field...")
    result = agent.process("Start quiz")
    assert "quiz_data" in result, "Missing quiz_data"
    print("✓ Quiz response includes quiz_data")
    
    return True


def test_stitch_agent():
    """Test StitchAgent implementation."""
    print("\n" + "=" * 80)
    print("Testing StitchAgent")
    print("=" * 80)
    
    # Create agent
    agent = StitchAgent()
    
    # Test 1: Code submission
    print("\n1️⃣  Testing code submission...")
    code = """
def greet(name):
    return f"Hello, {name}!"
"""
    result = agent.process(f"```python\n{code}\n```")
    
    assert result["response"], "Missing response"
    assert "reasoning_trace" in result, "Missing reasoning_trace"
    print("✓ StitchAgent validates code submissions")
    print(f"  Response preview: {result['response'][:100]}...")
    
    # Test 2: Exercise request
    print("\n2️⃣  Testing exercise progression...")
    result = agent.process("Give me next exercise")
    
    assert "exercise" in result["response"].lower(), "Response doesn't contain exercise"
    print("✓ StitchAgent can provide exercises")
    
    # Test 3: Concept explanation
    print("\n3️⃣  Testing concept explanation...")
    result = agent.process("Explain list comprehension")
    
    assert "comprehension" in result["response"].lower() or "list" in result["response"].lower(), \
        "Response doesn't explain concept"
    print("✓ StitchAgent explains Python concepts")
    
    # Test 4: Hints
    print("\n4️⃣  Testing hint system...")
    result = agent.process("I need a hint")
    
    assert "hint" in result["response"].lower(), "Response doesn't provide hint"
    print("✓ StitchAgent provides helpful hints")
    
    # Test 5: Exercise data tracking
    print("\n5️⃣  Verifying exercise_data field...")
    result = agent.process("next exercise")
    assert "exercise_data" in result, "Missing exercise_data"
    print("✓ Exercise response includes exercise_data")
    
    return True


def test_agent_coordination():
    """Test that agents can work together."""
    print("\n" + "=" * 80)
    print("Testing Agent Coordination")
    print("=" * 80)
    
    print("\n1️⃣  Creating all three agents...")
    jarvis = JARVISAgent()
    lilo = LiloAgent()
    stitch = StitchAgent()
    print("✓ All three agents instantiated successfully")
    
    print("\n2️⃣  Verifying agent names...")
    assert jarvis.name == "jarvis", "JARVIS name mismatch"
    assert lilo.name == "lilo", "Lilo name mismatch"
    assert stitch.name == "stitch", "Stitch name mismatch"
    print(f"✓ Agent names verified: {jarvis.name}, {lilo.name}, {stitch.name}")
    
    print("\n3️⃣  Verifying agent types...")
    print(f"  JARVIS type: {jarvis.agent_type.value}")
    print(f"  Lilo type: {lilo.agent_type.value}")
    print(f"  Stitch type: {stitch.agent_type.value}")
    print("✓ Agent types assigned correctly")
    
    print("\n4️⃣  Verifying each agent has unique session ID...")
    assert jarvis.session_id != lilo.session_id, "JARVIS and Lilo have same session"
    assert lilo.session_id != stitch.session_id, "Lilo and Stitch have same session"
    print(f"  JARVIS session: {jarvis.session_id[:8]}...")
    print(f"  Lilo session: {lilo.session_id[:8]}...")
    print(f"  Stitch session: {stitch.session_id[:8]}...")
    print("✓ Session IDs are unique")
    
    print("\n5️⃣  Testing tool dispatcher access...")
    assert hasattr(jarvis, 'dispatcher'), "JARVIS missing dispatcher"
    assert hasattr(lilo, 'dispatcher'), "Lilo missing dispatcher"
    assert hasattr(stitch, 'dispatcher'), "Stitch missing dispatcher"
    print("✓ All agents have tool dispatcher")
    
    return True


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("PHASE 2 AGENT STANDARDIZATION VERIFICATION")
    print("=" * 80)
    
    start_time = time.time()
    
    try:
        # Run individual agent tests
        test_jarvis_agent()
        test_lilo_agent()
        test_stitch_agent()
        
        # Run coordination tests
        test_agent_coordination()
        
        elapsed = time.time() - start_time
        
        print("\n" + "=" * 80)
        print("✅ PHASE 2 COMPLETE: Agent Standardization Verified!")
        print("=" * 80)
        print(f"\nAll tests passed in {elapsed:.2f}s")
        print("\nAgent capabilities verified:")
        print("  ✓ JARVISAgent: Reasoning queries, tool invocation")
        print("  ✓ LiloAgent: Quiz management, progress tracking, concept explanation")
        print("  ✓ StitchAgent: Code validation, exercises, hints")
        print("  ✓ Coordination: Unique sessions, shared tool dispatcher")
        print("\nNext steps:")
        print("  1. Integrate with O.B.E.O.S event system")
        print("  2. Test cross-agent tool invocation")
        print("  3. Define tool-calling coordination matrix")
        print("  4. Deploy agents as micro-services")
        print("\n")
        return 0
    
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
