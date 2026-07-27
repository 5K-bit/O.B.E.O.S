#!/usr/bin/env python3
"""
Voice Loop Integration Test

Tests the continuous voice interaction loop with the unified orchestrator.
This demonstrates the real-time microphone pipeline and voice processing.
"""

import sys
import os
import time
import threading

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from blackfong_ecosystem.unified_orchestrator import unified_orchestrator, InputModality
from blackfong_ecosystem.voice_loop_controller import VoiceLoopController, VoiceLoopState


def test_voice_loop_integration():
    """Test the voice loop integration with unified orchestrator."""
    print("=" * 60)
    print("VOICE LOOP INTEGRATION TEST")
    print("=" * 60)

    try:
        # Test voice loop status (should be inactive)
        status = unified_orchestrator.get_voice_loop_status()
        print(f"Initial voice loop status: {status}")

        # Start voice loop
        print("\nStarting voice loop...")
        success = unified_orchestrator.start_voice_loop()
        if success:
            print("✅ Voice loop started successfully")

            # Check status
            status = unified_orchestrator.get_voice_loop_status()
            print(f"Voice loop status: {status}")

            # Let it run for a few seconds
            print("\n🎙️  Voice loop is running...")
            print("Say 'Hey Blackfong open browser' to test (or wait 10 seconds)")

            # Wait a bit
            time.sleep(3)

            # Stop voice loop
            print("\nStopping voice loop...")
            success = unified_orchestrator.stop_voice_loop()
            if success:
                print("✅ Voice loop stopped successfully")
            else:
                print("❌ Failed to stop voice loop")

        else:
            print("❌ Failed to start voice loop")

        # Final status check
        status = unified_orchestrator.get_voice_loop_status()
        print(f"Final voice loop status: {status}")

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("VOICE LOOP TEST COMPLETE")
    print("=" * 60)


def test_manual_voice_command():
    """Test processing a voice command manually."""
    print("\nTesting manual voice command processing...")

    try:
        # Test voice command processing
        result = unified_orchestrator.process_input(
            "hey blackfong open browser",
            modality=InputModality.VOICE
        )

        print(f"Response: {result.get('response', 'No response')}")
        print(f"Confidence: {result.get('confidence', 0):.2f}")
        print(f"Agent: {result.get('agent', 'unknown')}")

        if result.get('voice_response'):
            print(f"Voice Response: {result['voice_response']}")

    except Exception as e:
        print(f"❌ Manual voice command test failed: {e}")


def demo_voice_loop_states():
    """Demonstrate voice loop state management."""
    print("\nDemonstrating voice loop states...")

    try:
        # Create a voice loop controller for demo
        from blackfong_ecosystem.agents.voice_agent import VoiceAgent, VoiceMode

        voice_agent = VoiceAgent(voice_mode=VoiceMode.PUSH_TO_TALK)
        voice_loop = VoiceLoopController(
            unified_orchestrator=unified_orchestrator,
            voice_agent=voice_agent
        )

        # Demo state callbacks
        def on_state_change(state):
            print(f"🔄 Voice loop state changed to: {state.value}")

        voice_loop.on_state_change = on_state_change

        print("Voice loop states:")
        for state in VoiceLoopState:
            print(f"  - {state.value}")

        print(f"Initial state: {voice_loop.state.value}")

        # Note: We don't actually start it here to avoid audio issues in test

    except Exception as e:
        print(f"❌ State demo failed: {e}")


if __name__ == "__main__":
    # Run tests
    test_manual_voice_command()
    demo_voice_loop_states()
    test_voice_loop_integration()