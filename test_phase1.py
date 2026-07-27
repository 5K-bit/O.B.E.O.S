#!/usr/bin/env python
"""
Phase 1 Test: Event Backbone Verification

Tests the core event loop:
1. Publish an event
2. Retrieve by ID
3. Query by type
4. Query by session
"""

import requests
import json

API_URL = "http://localhost:8000"

def main():
    print("=" * 60)
    print("PHASE 1: Testing Event Backbone")
    print("=" * 60)
    
    # Test 1: Health
    print("\n[1] Health Check...")
    try:
        resp = requests.get(f"{API_URL}/health", timeout=5)
        status = resp.json()["status"]
        print(f"✓ API Status: {status}")
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False
    
    # Test 2: Publish event
    print("\n[2] Publishing transcription event...")
    event_data = {
        "source": "transcriber",
        "event_type": "transcription",
        "payload": {
            "audio_file": "test_audio.wav",
            "transcript": "Hello world, this is a test transcription",
            "duration_ms": 3000,
            "language": "en"
        },
        "confidence": 0.98,
        "session_id": "test_session_001",
        "tags": ["test", "demo"]
    }
    try:
        resp = requests.post(f"{API_URL}/events/publish", json=event_data, timeout=5)
        result = resp.json()
        event_id = result["event_id"]
        print(f"✓ Event published: {event_id}")
    except Exception as e:
        print(f"✗ Publish failed: {e}")
        return False
    
    # Test 3: Get event by ID
    print("\n[3] Retrieving event by ID...")
    try:
        resp = requests.get(f"{API_URL}/events/{event_id}", timeout=5)
        event = resp.json()["event"]
        print(f"✓ Retrieved: {event['id'][:8]}...")
        print(f"  Transcript: {event['payload']['transcript'][:50]}...")
    except Exception as e:
        print(f"✗ Get by ID failed: {e}")
        return False
    
    # Test 4: Query by type
    print("\n[4] Querying transcription events...")
    try:
        resp = requests.get(f"{API_URL}/events/query?event_type=transcription&limit=10", timeout=5)
        result_data = resp.json()
        count = result_data["count"]
        print(f"✓ Found {count} transcription event(s)")
    except Exception as e:
        print(f"✗ Query by type failed: {e}")
        return False
    
    # Test 5: Query by session
    print("\n[5] Querying by session ID...")
    try:
        resp = requests.get(f"{API_URL}/events/query?session_id=test_session_001", timeout=5)
        result_data = resp.json()
        count = result_data["count"]
        print(f"✓ Found {count} event(s) in session")
    except Exception as e:
        print(f"✗ Query by session failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ PHASE 1 COMPLETE: Event backbone verified!")
    print("=" * 60)
    print("\nNext: Integrate transcriber to publish events automatically")
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
