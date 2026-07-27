"""
Event API Server

Flask REST API for O.B.E.O.S event backbone.

Provides:
- /events/publish: ingest events from external systems
- /events/query: query events by type, source, timestamp
- /events/{id}: get event by ID
- /services/register: register service capabilities
- /services/capabilities: list available services
"""

from __future__ import annotations

import json
from typing import Any, Dict

from flask import Flask, jsonify, request

from .config import paths
from .event_queue import EventQueue
from .events import Event
from .service_registry import Service, ServiceCapability, get_registry
from .storage import EventStore

# Initialize components
event_store = EventStore()
event_queue = EventQueue()
registry = get_registry()


def create_app() -> Flask:
    """Create and configure the Flask app."""
    app = Flask(__name__)
    
    # Initialize databases on first run
    event_store.init_db()
    event_queue.init_db()
    
    # Health check
    @app.route("/health", methods=["GET"])
    def health() -> Dict[str, str]:
        return jsonify({"status": "healthy", "service": "blackfong_control_plane"})
    
    # Event publishing endpoint
    @app.route("/events/publish", methods=["POST"])
    def publish_event() -> tuple[Dict[str, Any], int]:
        """
        Publish an event to the control plane.
        
        Expected JSON:
        {
          "source": "transcriber" | "vision" | ...,
          "event_type": "transcription" | "object_detection" | ...,
          "payload": {...},
          "confidence": 0.95,
          "parent_event": "uuid" (optional),
          "session_id": "uuid" (optional),
          "correlation_id": "uuid" (optional),
          "tags": ["tag1", "tag2"] (optional)
        }
        """
        try:
            data = request.get_json() or {}
            
            # Extract fields with defaults
            source = data.get("source", "user")
            event_type = data.get("event_type", "unknown")
            payload = data.get("payload", {})
            confidence = data.get("confidence", 1.0)
            parent_event = data.get("parent_event")
            session_id = data.get("session_id")
            correlation_id = data.get("correlation_id")
            tags = data.get("tags", [])
            
            # Create event with metadata
            event = Event.new(
                source=source,  # type: ignore
                type=event_type,
                payload=payload,
                confidence=confidence,
                parent_event=parent_event,
                session_id=session_id,
                correlation_id=correlation_id,
                tags=tags,
            )
            
            # Store in event store
            event_store.add_event(event)
            
            # Enqueue for processing
            queue_id = event_queue.enqueue(event.id)
            
            return (
                jsonify({
                    "success": True,
                    "event_id": event.id,
                    "queue_id": queue_id,
                    "timestamp": event.timestamp,
                }),
                201,
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # Event query endpoint
    @app.route("/events/query", methods=["GET"])
    def query_events() -> tuple[Dict[str, Any], int]:
        """
        Query events with optional filters.
        
        Query parameters:
        - event_type: filter by event type
        - source: filter by source
        - limit: max results (default: 100)
        - session_id: filter by session (metadata)
        - correlation_id: filter by correlation (metadata)
        """
        try:
            event_type = request.args.get("event_type")
            source = request.args.get("source")
            limit = int(request.args.get("limit", 100))
            session_id = request.args.get("session_id")
            correlation_id = request.args.get("correlation_id")
            
            # Query events
            events = event_store.list_events(
                source=source,
                type=event_type,
                limit=limit,
            )
            
            # Filter by metadata if provided
            if session_id or correlation_id:
                filtered = []
                for e in events:
                    if session_id and e.metadata.get("session_id") != session_id:
                        continue
                    if correlation_id and e.metadata.get("correlation_id") != correlation_id:
                        continue
                    filtered.append(e)
                events = filtered
            
            return jsonify({
                "success": True,
                "count": len(events),
                "events": [e.to_dict() for e in events],
            }), 200
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # Get event by ID
    @app.route("/events/<event_id>", methods=["GET"])
    def get_event(event_id: str) -> tuple[Dict[str, Any], int]:
        """Get a specific event by ID."""
        try:
            event = event_store.get_event_by_id(event_id)
            if event:
                return jsonify({
                    "success": True,
                    "event": event.to_dict(),
                }), 200
            else:
                return jsonify({
                    "success": False,
                    "error": f"Event {event_id} not found",
                }), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # Service registration
    @app.route("/services/register", methods=["POST"])
    def register_service() -> tuple[Dict[str, Any], int]:
        """
        Register a service.
        
        Expected JSON:
        {
          "name": "my_service",
          "type": "perception" | "ingestion" | "reasoning" | "coordination",
          "endpoint": "http://...",
          "version": "1.0",
          "health_check": "/health",
          "capabilities": [
            {
              "name": "capability_name",
              "description": "...",
              "input_schema": {...},
              "output_schema": {...},
              "latency_ms": 100
            }
          ]
        }
        """
        try:
            data = request.get_json() or {}
            
            # Build capabilities
            capabilities = []
            for cap_data in data.get("capabilities", []):
                cap = ServiceCapability(
                    name=cap_data.get("name", "unknown"),
                    description=cap_data.get("description", ""),
                    input_schema=cap_data.get("input_schema"),
                    output_schema=cap_data.get("output_schema"),
                    latency_ms=cap_data.get("latency_ms", 100),
                )
                capabilities.append(cap)
            
            # Create and register service
            service = Service(
                name=data.get("name", "unknown"),
                service_type=data.get("type", "coordination"),
                endpoint=data.get("endpoint", ""),
                capabilities=capabilities,
                health_check_path=data.get("health_check", "/health"),
                version=data.get("version", "1.0"),
            )
            
            registry.register(service)
            
            return jsonify({
                "success": True,
                "service": service.to_dict(),
            }), 201
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # List services and capabilities
    @app.route("/services/capabilities", methods=["GET"])
    def list_capabilities() -> tuple[Dict[str, Any], int]:
        """List all registered services and their capabilities."""
        try:
            service_type = request.args.get("type")
            
            if service_type:
                services = registry.list_by_type(service_type)
            else:
                services = registry.list_services()
            
            return jsonify({
                "success": True,
                "services": [s.to_dict() for s in services],
            }), 200
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # Get queue status
    @app.route("/events/queue/<event_id>/status", methods=["GET"])
    def get_queue_status(event_id: str) -> tuple[Dict[str, Any], int]:
        """Check the queue processing status of an event."""
        try:
            status = event_queue.get_status(event_id)
            if status:
                return jsonify({
                    "success": True,
                    "event_id": event_id,
                    "status": status,
                }), 200
            else:
                return jsonify({
                    "success": False,
                    "error": f"No queue entry for event {event_id}",
                }), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="localhost", port=8000, debug=False)
