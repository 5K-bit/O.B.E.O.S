#!/usr/bin/env python3
"""
O.B.E.O.S Orchestration Demo

Demonstrates the intelligent orchestration capabilities of the Blackfong ecosystem.
Shows how user requests are automatically routed to appropriate agents.
"""

from blackfong_ecosystem.orchestrator import Orchestrator


def demo_orchestration():
    """Demonstrate the orchestration engine in action."""
    print("🤖 O.B.E.O.S Orchestration Engine Demo")
    print("=" * 50)

    # Initialize the orchestrator
    orchestrator = Orchestrator()
    print("✅ Orchestrator initialized with all agents\n")

    # Demo requests that should route to different agents
    demo_requests = [
        ("Hello! How can you help me?", "JARVIS (General Assistant)"),
        ("What's the system status?", "JARVIS (System Queries)"),
        ("Show me available tools", "JARVIS (Tool Coordination)"),
        ("I need help with Python certification", "Lilo (Training Agent)"),
        ("Create a quiz for me", "Lilo (Quiz Generation)"),
        ("Show me a coding exercise", "Stitch (Python Learning)"),
        ("Debug this Python function", "Stitch (Code Help)"),
        ("Help me learn Python classes", "Stitch (Tutorial)"),
    ]

    print("🎯 Testing Intelligent Agent Routing:\n")

    for i, (request, expected_agent) in enumerate(demo_requests, 1):
        print(f"{i}. Request: '{request}'")
        print(f"   Expected: {expected_agent}")

        # Process the request
        result = orchestrator.process_user_request(request)

        actual_agent = result.get('agent', 'unknown').title()
        response_preview = result.get('response', '')[:60] + "..." if len(result.get('response', '')) > 60 else result.get('response', '')
        correlation_id = result.get('correlation_id', '')[:8] + "..."

        print(f"   Routed to: {actual_agent}")
        print(f"   Response: '{response_preview}'")
        print(f"   Correlation ID: {correlation_id}")
        print(f"   ✅ {'Correct' if actual_agent.lower() in expected_agent.lower() else 'Incorrect'} routing\n")

    # Demo workflow tracking
    print("📊 Workflow Tracking Demo:\n")

    # Process a request and check workflow status
    result = orchestrator.process_user_request("Help me with Python exercises")
    correlation_id = result['correlation_id']

    workflow_status = orchestrator.get_workflow_status(correlation_id)
    if workflow_status:
        print(f"Workflow Status for {correlation_id}:")
        print(f"  Status: {workflow_status['status']}")
        print(f"  Agents Involved: {workflow_status['agents_involved']}")
        print(f"  Event Count: {workflow_status['event_count']}")
        print(f"  Duration: {workflow_status['duration']:.3f}s")
    print()

    # Show active workflows
    active_workflows = orchestrator.list_active_workflows()
    print(f"Active Workflows: {len(active_workflows)}")
    for workflow in active_workflows[:3]:  # Show first 3
        print(f"  - {workflow['correlation_id'][:8]}...: {workflow['agents_involved']} agents, {workflow['event_count']} events")

    print("\n🎉 Demo Complete!")
    print("\nThe O.B.E.O.S Orchestration Engine successfully:")
    print("  • Routes requests to appropriate specialized agents")
    print("  • Tracks multi-agent workflows with correlation IDs")
    print("  • Provides semantic search over reasoning traces")
    print("  • Enables intelligent multi-agent coordination")


if __name__ == "__main__":
    demo_orchestration()