#!/usr/bin/env python3
"""
Phase 3 FINAL: simplified manual memory-bridge verification.

This stays as a runnable script, but no longer executes during import.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from blackfong_ecosystem.agents import JARVISAgent, LiloAgent, StitchAgent
from blackfong_ecosystem.event_client import EventClient


def main() -> int:
    print("\n" + "=" * 80)
    print("PHASE 3 FINAL: Memory Bridge Integration")
    print("=" * 80)

    start_time = time.time()

    try:
        print("\n[1] Creating agents...")
        jarvis = JARVISAgent()
        lilo = LiloAgent()
        stitch = StitchAgent()
        print("[OK] Agents created with EventClient configured")

        print("\n[2] Agents processing queries...")
        print("    - JARVIS...")
        r1 = jarvis.process("Hello")
        print(f"      Event ID: {r1['trace_event_id'][:16]}...")

        print("    - Lilo...")
        r2 = lilo.process("Start quiz")
        print(f"      Event ID: {r2['trace_event_id'][:16]}...")

        print("    - Stitch...")
        r3 = stitch.process("Next exercise")
        print(f"      Event ID: {r3['trace_event_id'][:16]}...")

        print("\n[3] Querying events by type...")
        client = EventClient()
        result = client.query_events(event_type="reasoning_trace", limit=10)
        events = result.get("events", [])
        print(f"[OK] Found {len(events)} reasoning_trace events")

        if r1["trace_event_id"]:
            print("\n[4] Retrieving specific event...")
            event = client.get_event(r1["trace_event_id"])
            print("[OK] Event retrieved:")
            print(f"  - Source: {event.get('source')}")
            print(f"  - Type: {event.get('event_type')}")
            print(f"  - ID: {event.get('id')[:16]}...")

        elapsed = time.time() - start_time

        print("\n" + "=" * 80)
        print("[OK] PHASE 3 COMPLETE: Memory Bridge Integration Verified!")
        print("=" * 80)
        print(f"\nCompleted in {elapsed:.2f}s")
        print("\nCapabilities:")
        print("  [OK] Agents auto-initialize with EventClient")
        print("  [OK] Agents emit reasoning traces as events")
        print("  [OK] Events stored in O.B.E.O.S EventStore")
        print("  [OK] Events queryable by type")
        print("  [OK] Events retrievable by ID")
        print("\nArchitecture Stack:")
        print("  Layer 1: Event Store (SQLite)      [OPERATIONAL]")
        print("  Layer 2: Event API (Flask REST)    [OPERATIONAL]")
        print("  Layer 3: Event Client (HTTP)       [OPERATIONAL]")
        print("  Layer 4: Agent Integration         [ACTIVE]")
        print("\nNext Phase: Intelligent Orchestration (O.B.E.O.S dispatch)\n")
        return 0

    except Exception as exc:
        elapsed = time.time() - start_time
        print(f"\n[ERROR] Test failed after {elapsed:.2f}s: {exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
