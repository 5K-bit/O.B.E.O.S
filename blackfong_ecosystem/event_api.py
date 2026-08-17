"""
Event API Server

Flask REST API for the O.B.E.O.S event backbone.

Legacy event publishing remains available for existing internal clients. External
OBEOS components should publish Event Contract v1 envelopes to /events/v1/publish.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import Flask, jsonify, request

from .contracts.event_v1 import EventEnvelope
from .event_ingestion import EventV1Ingestor
from .event_queue import EventQueue
from .events import Event
from .service_registry import Service, ServiceCapability, get_registry
from .storage import EventStore

# Initialize components
event_store = EventStore()
event_queue = EventQueue()
event_v1_ingestor = EventV1Ingestor(event_store=event_store, event_queue=event_queue)
registry = get_registry()


def create_app() -> Flask:
    """Create and configure the event API."""
    app = Flask(__name__)
    event_store.init_db()
    event_queue.init_db()

    @app.route("/health", methods=["GET"])
    def health() -> Dict[str, Any]:
        return jsonify({
            "status": "healthy",
            "service": "blackfong_control_plane",
            "event_contract": "1.0",
        })

    @app.route("/events/v1/publish", methods=["POST"])
    def publish_event_v1() -> tuple[Dict[str, Any], int]:
        """Ingest a canonical OBEOS Event Contract v1 envelope."""
        try:
            data = request.get_json() or {}
            envelope = EventEnvelope.from_dict(data)
            ingested = event_v1_ingestor.ingest(envelope)
            return jsonify({"success": True, **ingested.to_dict()}), 201
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"success": False, "contract_version": "1.0", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"success": False, "contract_version": "1.0", "error": str(exc)}), 500

    @app.route("/events/publish", methods=["POST"])
    def publish_event() -> tuple[Dict[str, Any], int]:
        """Legacy internal event endpoint retained during v1 migration."""
        try:
            data = request.get_json() or {}
            source = data.get("source", "user")
            event_type = data.get("event_type", "unknown")
            payload = data.get("payload", {})
            confidence = data.get("confidence", 1.0)
            parent_event = data.get("parent_event")
            session_id = data.get("session_id")
            correlation_id = data.get("correlation_id")
            request_id = data.get("request_id")
            tags = data.get("tags", [])
            event = Event.new(
                source=source,
                type=event_type,
                payload=payload,
                confidence=confidence,
                parent_event=parent_event,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                tags=tags,
            )
            event_store.add_event(event)
            queue_id = event_queue.enqueue(event.id)
            return jsonify({
                "success": True,
                "event_id": event.id,
                "queue_id": queue_id,
                "timestamp": event.timestamp,
                "contract_version": "legacy",
            }), 201
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/events/query", methods=["GET"])
    def query_events() -> tuple[Dict[str, Any], int]:
        try:
            event_type = request.args.get("event_type")
            source = request.args.get("source")
            limit = int(request.args.get("limit", 100))
            session_id = request.args.get("session_id")
            correlation_id = request.args.get("correlation_id")
            events = event_store.list_events(source=source, type=event_type, limit=limit)
            if session_id or correlation_id:
                filtered = []
                for event in events:
                    if session_id and event.metadata.get("session_id") != session_id:
                        continue
                    if correlation_id and event.metadata.get("correlation_id") != correlation_id:
                        continue
                    filtered.append(event)
                events = filtered
            return jsonify({
                "success": True,
                "count": len(events),
                "events": [event.to_dict() for event in events],
            }), 200
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/events/<event_id>", methods=["GET"])
    def get_event(event_id: str) -> tuple[Dict[str, Any], int]:
        try:
            event = event_store.get_event_by_id(event_id)
            if event:
                return jsonify({"success": True, "event": event.to_dict()}), 200
            return jsonify({"success": False, "error": f"Event {event_id} not found"}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/services/register", methods=["POST"])
    def register_service() -> tuple[Dict[str, Any], int]:
        try:
            data = request.get_json() or {}
            capabilities = []
            for cap_data in data.get("capabilities", []):
                capabilities.append(ServiceCapability(
                    name=cap_data.get("name", "unknown"),
                    description=cap_data.get("description", ""),
                    input_schema=cap_data.get("input_schema"),
                    output_schema=cap_data.get("output_schema"),
                    latency_ms=cap_data.get("latency_ms", 100),
                ))
            service = Service(
                name=data.get("name", "unknown"),
                service_type=data.get("type", "coordination"),
                endpoint=data.get("endpoint", ""),
                capabilities=capabilities,
                health_check_path=data.get("health_check", "/health"),
                version=data.get("version", "1.0"),
            )
            registry.register(service)
            return jsonify({"success": True, "service": service.to_dict()}), 201
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/services/capabilities", methods=["GET"])
    def list_capabilities() -> tuple[Dict[str, Any], int]:
        try:
            service_type = request.args.get("type")
            services = registry.list_by_type(service_type) if service_type else registry.list_services()
            return jsonify({"success": True, "services": [service.to_dict() for service in services]}), 200
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/events/queue/<event_id>/status", methods=["GET"])
    def get_queue_status(event_id: str) -> tuple[Dict[str, Any], int]:
        try:
            status = event_queue.get_status(event_id)
            if status:
                return jsonify({"success": True, "event_id": event_id, "status": status}), 200
            return jsonify({"success": False, "error": f"No queue entry for event {event_id}"}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    return app


if __name__ == "__main__":
    create_app().run(host="localhost", port=8000, debug=False)
