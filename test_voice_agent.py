#!/usr/bin/env python3
"""
Test Voice Agent Integration

Basic test to verify VoiceAgent can be initialized and process commands.
"""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from blackfong_ecosystem.agents.voice_agent import VoiceAgent, VoiceMode

def test_voice_agent_initialization():
    """Test VoiceAgent initialization."""
    print("Testing VoiceAgent initialization...")

    try:
        # Initialize voice agent
        voice_agent = VoiceAgent(voice_mode=VoiceMode.PUSH_TO_TALK)
        print(f"✅ VoiceAgent initialized with session: {voice_agent.session_id}")
        print(f"   Voice mode: {voice_agent.voice_mode.value}")
        print(f"   Authenticated: {voice_agent.authenticated}")

        return voice_agent

    except Exception as e:
        print(f"❌ VoiceAgent initialization failed: {e}")
        return None

def test_voice_command_processing(voice_agent):
    """Test voice command processing."""
    print("\nTesting voice command processing...")

    test_commands = [
        "open vscode",
        "take screenshot",
        "system status",
        "hey blackfong open browser",
        "shutdown system",  # Should be blocked
    ]

    for command in test_commands:
        print(f"\nProcessing command: '{command}'")
        try:
            result = voice_agent.process(command)
            print(f"  Response: {result.get('response', 'No response')}")
            print(f"  Confidence: {result.get('confidence', 0):.2f}")
            print(f"  Authenticated: {result.get('authenticated', False)}")
            print(f"  Safety Level: {result.get('safety_level', 'unknown')}")

            if result.get('voice_response'):
                print(f"  Voice Response: {result['voice_response']}")

        except Exception as e:
            print(f"  ❌ Command processing failed: {e}")

def test_voice_memory(voice_agent):
    """Test voice memory functionality."""
    print("\nTesting voice memory...")

    try:
        history = voice_agent.get_voice_history()
        print(f"Voice history length: {len(history)}")

        voice_agent.clear_voice_memory()
        history_after_clear = voice_agent.get_voice_history()
        print(f"Voice history after clear: {len(history_after_clear)}")

    except Exception as e:
        print(f"❌ Voice memory test failed: {e}")

def main():
    """Run all voice agent tests."""
    print("=" * 60)
    print("VOICE AGENT INTEGRATION TEST")
    print("=" * 60)

    # Test initialization
    voice_agent = test_voice_agent_initialization()
    if not voice_agent:
        print("❌ Cannot continue without VoiceAgent")
        return

    # Test command processing
    test_voice_command_processing(voice_agent)

    # Test memory
    test_voice_memory(voice_agent)

    print("\n" + "=" * 60)
    print("VOICE AGENT TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()