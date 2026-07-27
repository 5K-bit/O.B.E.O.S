#!/usr/bin/env python3
"""Quick manual verification for Phase 3 event emission."""

from blackfong_ecosystem.agents import JARVISAgent
from blackfong_ecosystem.event_client import EventClient


def main() -> int:
    print("1. Creating JARVIS agent...")
    agent = JARVISAgent()
    print("[OK] Agent created")

    print("\n2. Processing query...")
    result = agent.process("Hello, how are you?")
    print("[OK] Process completed")

    print("\n3. Results:")
    print(f"   Response: {result['response'][:60]}...")
    print(f"   Confidence: {result['confidence']:.2f}")
    print(f"   Trace Event ID: {result.get('trace_event_id', 'NONE')}")

    if result.get("trace_event_id"):
        print("\n4. Retrieving event from API...")
        client = EventClient()
        try:
            event = client.get_event(result["trace_event_id"])
            print("[OK] Event retrieved!")
            print(f"   Source: {event.get('source')}")
            print(f"   Type: {event.get('event_type')}")
            print(f"   ID: {event.get('id', '')[:16]}...")
        except Exception as exc:
            print(f"[WARN] Failed to retrieve event: {exc}")
    else:
        print("\n4. [WARN] No event ID returned")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
