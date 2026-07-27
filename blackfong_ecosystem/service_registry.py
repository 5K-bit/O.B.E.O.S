"""
Service Registry

Centralized registry of available services and their capabilities.
Enables intelligent routing and tool discovery in the multi-agent system.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ServiceCapability:
    """Describes a single capability offered by a service."""
    
    def __init__(
        self,
        name: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        latency_ms: int = 100,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}
        self.latency_ms = latency_ms
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "latency_ms": self.latency_ms,
        }


class Service:
    """Represents a registered service with capabilities."""
    
    def __init__(
        self,
        name: str,
        service_type: str,
        endpoint: str,
        capabilities: Optional[List[ServiceCapability]] = None,
        health_check_path: str = "/health",
        version: str = "1.0",
    ):
        self.name = name
        self.service_type = service_type  # e.g., "perception", "ingestion", "reasoning", "coordination"
        self.endpoint = endpoint
        self.capabilities = capabilities or []
        self.health_check_path = health_check_path
        self.version = version
        self.is_available = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.service_type,
            "endpoint": self.endpoint,
            "version": self.version,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "health_check": self.health_check_path,
            "available": self.is_available,
        }
    
    def get_capability(self, name: str) -> Optional[ServiceCapability]:
        """Get a capability by name."""
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None


class ServiceRegistry:
    """
    Centralized registry for multi-agent system services.
    
    Provides:
    - service registration
    - capability discovery
    - routing based on required capability
    """
    
    def __init__(self):
        self._services: Dict[str, Service] = {}
    
    def register(self, service: Service) -> None:
        """Register a service."""
        self._services[service.name] = service
    
    def unregister(self, service_name: str) -> bool:
        """Unregister a service by name."""
        if service_name in self._services:
            del self._services[service_name]
            return True
        return False
    
    def get_service(self, service_name: str) -> Optional[Service]:
        """Get a service by name."""
        return self._services.get(service_name)
    
    def list_services(self) -> List[Service]:
        """List all registered services."""
        return list(self._services.values())
    
    def list_by_type(self, service_type: str) -> List[Service]:
        """List services by type (e.g., 'perception', 'reasoning')."""
        return [s for s in self._services.values() if s.service_type == service_type]
    
    def find_capability(self, capability_name: str) -> List[Service]:
        """Find all services offering a specific capability."""
        result = []
        for service in self._services.values():
            if service.get_capability(capability_name):
                result.append(service)
        return result
    
    def get_available_services(self) -> List[Service]:
        """Get all available services (health check passed)."""
        return [s for s in self._services.values() if s.is_available]
    
    def to_dict(self) -> Dict[str, Any]:
        """Export registry as dict."""
        return {
            "services": {name: service.to_dict() for name, service in self._services.items()}
        }


# Global registry instance
_registry: Optional[ServiceRegistry] = None
_ui_services: Optional["UIServiceContainer"] = None
_ui_services_by_memory_id: Dict[int, "UIServiceContainer"] = {}
_ui_services_lock = threading.RLock()


def get_registry() -> ServiceRegistry:
    """Get or initialize the global service registry."""
    global _registry
    if _registry is None:
        _registry = ServiceRegistry()
        _initialize_default_services(_registry)
    return _registry


@dataclass
class UIServiceContainer:
    """Shared runtime services used by all operator UI surfaces."""

    memory_service: Any
    event_store: Any
    memory_chamber: Any
    notification_service: Any
    operator_actions: Any
    file_workflow: Any
    file_scope_service: Any
    awareness_service: Any
    operator_summary: Any
    cognition_service: Any
    platform_service: Any
    docker_stack: Any
    tinyprogrammer: Any
    startup_coordinator: Any
    integration_registry: Any
    redamon_workflow: Any
    operator_chat: Any
    event_bus: Any


def _build_ui_services(*, memory_service: Any = None, orchestrator_instance: Any = None) -> UIServiceContainer:
    from blackfong_ecosystem.cognition import CognitionTelemetryService
    from blackfong_ecosystem.config import paths
    from blackfong_ecosystem.conversation_memory import conversation_memory_service
    from blackfong_ecosystem.docker_stack import DockerStackService
    from blackfong_ecosystem.external_integrations import ExternalIntegrationRegistry
    from blackfong_ecosystem.file_scope_service import FileScopeService
    from blackfong_ecosystem.file_workflow import FileWorkflowService
    from blackfong_ecosystem.memory_chamber import MemoryChamberService
    from blackfong_ecosystem.notification_service import NotificationService
    from blackfong_ecosystem.obeos_linux import OBEOSLinuxPlatformService
    from blackfong_ecosystem.operator_chat import OperatorChatService
    from blackfong_ecosystem.operator_actions import OperatorActionService
    from blackfong_ecosystem.operator_summary import OperatorSummaryService
    from blackfong_ecosystem.runtime_ownership import get_shared_memory_chamber
    from blackfong_ecosystem.redamon_mode import RedAmonWorkflowService
    from blackfong_ecosystem.startup_coordinator import StartupCoordinator
    from blackfong_ecosystem.system_awareness import SystemAwarenessService
    from blackfong_ecosystem.tinyprogrammer_companion import tinyprogrammer_companion
    from blackfong_ecosystem.ui_events import UIEventBus

    memory = memory_service or getattr(orchestrator_instance, "memory_service", None) or conversation_memory_service
    event_store = getattr(memory, "event_store", None)
    notifications = NotificationService(memory_service=memory)
    operator_actions = OperatorActionService(memory_service=memory)
    shared_file_workflow = getattr(orchestrator_instance, "file_workflow", None) if orchestrator_instance is not None else None
    file_workflow = shared_file_workflow or FileWorkflowService(memory_service=memory)
    shared_file_scope = getattr(orchestrator_instance, "file_scope_service", None) if orchestrator_instance is not None else None
    file_scope_service = shared_file_scope or FileScopeService(memory_service=memory, governed_roots=file_workflow.allowed_roots)
    tinyprogrammer_companion.memory_service = memory
    tinyprogrammer_companion.event_store = event_store
    tinyprogrammer_companion.notification_service = notifications
    integration_registry = ExternalIntegrationRegistry(workspace_root=paths.root)
    startup_coordinator = StartupCoordinator()
    shared_memory_chamber = getattr(orchestrator_instance, "memory_chamber", None)
    if shared_memory_chamber is None:
        shared_memory_chamber = getattr(getattr(orchestrator_instance, "daise", None), "memory_chamber", None)
    memory_chamber = shared_memory_chamber or get_shared_memory_chamber(
        event_store=event_store,
        vector_store=getattr(memory, "vector_store", None),
        workspace_root=paths.root,
        integration_registry=integration_registry,
        startup_coordinator=startup_coordinator,
        start_watch=False,
    )
    awareness_service = SystemAwarenessService(
        memory_service=memory,
        tinyprogrammer=tinyprogrammer_companion,
        startup_coordinator=startup_coordinator,
    )
    redamon_workflow = RedAmonWorkflowService(
        memory_service=memory,
        operator_actions=operator_actions,
        integration_registry=integration_registry,
    )
    operator_summary = OperatorSummaryService(
        memory_service=memory,
        notification_service_instance=notifications,
        operator_actions=operator_actions,
        tinyprogrammer=tinyprogrammer_companion,
        file_scope_service=file_scope_service,
        awareness_service=awareness_service,
        startup_coordinator=startup_coordinator,
        integration_registry=integration_registry,
        redamon_workflow=redamon_workflow,
        memory_chamber=memory_chamber,
    )
    cognition_service = CognitionTelemetryService(
        memory_service=memory,
        awareness_service=awareness_service,
        operator_actions=operator_actions,
        tinyprogrammer=tinyprogrammer_companion,
        memory_chamber=memory_chamber,
    )
    operator_chat = OperatorChatService(memory_service=memory, orchestrator=orchestrator_instance)
    return UIServiceContainer(
        memory_service=memory,
        event_store=event_store,
        memory_chamber=memory_chamber,
        notification_service=notifications,
        operator_actions=operator_actions,
        file_workflow=file_workflow,
        file_scope_service=file_scope_service,
        awareness_service=awareness_service,
        operator_summary=operator_summary,
        cognition_service=cognition_service,
        platform_service=OBEOSLinuxPlatformService(),
        docker_stack=DockerStackService(workspace_root=paths.root),
        tinyprogrammer=tinyprogrammer_companion,
        startup_coordinator=startup_coordinator,
        integration_registry=integration_registry,
        redamon_workflow=redamon_workflow,
        operator_chat=operator_chat,
        event_bus=UIEventBus(event_store=event_store),
    )


def get_ui_services(*, memory_service: Any = None, orchestrator_instance: Any = None) -> UIServiceContainer:
    """Return shared operator UI services for a memory/orchestrator context."""

    global _ui_services
    with _ui_services_lock:
        if memory_service is None and orchestrator_instance is None:
            if _ui_services is None:
                _ui_services = _build_ui_services()
            return _ui_services
        memory = memory_service or getattr(orchestrator_instance, "memory_service", None)
        key = id(memory) if memory is not None else id(orchestrator_instance)
        services = _ui_services_by_memory_id.get(key)
        if services is None:
            services = _build_ui_services(memory_service=memory_service, orchestrator_instance=orchestrator_instance)
            _ui_services_by_memory_id[key] = services
        return services


def reset_ui_services_for_tests() -> None:
    """Reset shared UI services between isolated tests."""

    global _ui_services
    from blackfong_ecosystem.runtime_ownership import reset_runtime_ownership_for_tests

    with _ui_services_lock:
        _ui_services = None
        _ui_services_by_memory_id.clear()
    reset_runtime_ownership_for_tests()


def _initialize_default_services(registry: ServiceRegistry) -> None:
    """Initialize registry with default known services."""
    
    # O.B.E.O.S control plane
    registry.register(
        Service(
            name="blackfong_control",
            service_type="coordination",
            endpoint="http://localhost:8000",
            capabilities=[
                ServiceCapability(
                    name="publish_event",
                    description="Publish an event to the control plane",
                    input_schema={"event": "Event"},
                    output_schema={"event_id": "str"},
                    latency_ms=10,
                ),
                ServiceCapability(
                    name="query_events",
                    description="Query events from memory",
                    input_schema={"query": "str", "filters": "dict"},
                    output_schema={"events": "List[Event]"},
                    latency_ms=50,
                ),
            ],
        )
    )
    
    # Transcriber
    registry.register(
        Service(
            name="transcriber",
            service_type="ingestion",
            endpoint="http://localhost:5001",
            capabilities=[
                ServiceCapability(
                    name="speech_to_text",
                    description="Transcribe audio to text",
                    input_schema={"audio": "binary", "language": "str"},
                    output_schema={"text": "str", "confidence": "float"},
                    latency_ms=300,
                ),
            ],
        )
    )
    
    # Vision
    registry.register(
        Service(
            name="vision_detector",
            service_type="perception",
            endpoint="http://localhost:5002",
            capabilities=[
                ServiceCapability(
                    name="object_detection",
                    description="Detect objects in images",
                    input_schema={"image": "binary", "confidence_threshold": "float"},
                    output_schema={"detections": "List[Detection]"},
                    latency_ms=100,
                ),
            ],
        )
    )

    registry.register(
        Service(
            name="securityplus",
            service_type="training",
            endpoint="local://comptTIA/securityplus-bot",
            capabilities=[
                ServiceCapability("get_status", "Read Security+ objectives, question bank, and progress state"),
                ServiceCapability("get_question", "Request a Security+ practice question"),
                ServiceCapability("get_progress", "Fetch Security+ learner analytics"),
            ],
        )
    )

    registry.register(
        Service(
            name="tinyprogrammer",
            service_type="creative_companion",
            endpoint="local://TinyProgrammer-main",
            capabilities=[
                ServiceCapability("get_status", "Read TinyProgrammer state or offline snapshot"),
                ServiceCapability("send_brief", "Send a D.A.I.S.E creative brief"),
                ServiceCapability("get_recent", "Read recent generated programs and reflections"),
            ],
        )
    )

    registry.register(
        Service(
            name="memory_chamber",
            service_type="visual_memory",
            endpoint="local://thoughtglass",
            capabilities=[
                ServiceCapability("create_artifact", "Persist visual cognitive artifacts into EventStore"),
                ServiceCapability("build_graph", "Project events, sessions, sensors, capsules, and TinyProgrammer into Thoughtglass"),
            ],
        )
    )

    registry.register(
        Service(
            name="sentinel",
            service_type="perception",
            endpoint="local://sentinel",
            capabilities=[
                ServiceCapability("emit_detection", "Normalize camera, audio, presence, and environment detections as canonical events"),
                ServiceCapability("capture_raw_artifact", "Attach event-triggered raw artifacts under the active archive profile"),
            ],
        )
    )

    registry.register(
        Service(
            name="frame_bridge",
            service_type="wearable",
            endpoint="local://brilliant-frame",
            capabilities=[
                ServiceCapability("display_text", "Send concise D.A.I.S.E and notification text to Brilliant Frame"),
                ServiceCapability("tap_to_talk", "Use explicit Frame taps to start and stop voice capture"),
                ServiceCapability("look_and_ask", "Capture an operator-requested Frame photo for vision context"),
                ServiceCapability("thoughtglass_gesture", "Map Frame taps and IMU motion into Thoughtglass gestures"),
                ServiceCapability("device_report", "Report Frame battery, BLE, firmware, and sensor readiness"),
            ],
        )
    )

    registry.register(
        Service(
            name="dream_layer",
            service_type="creative_cognition",
            endpoint="local://dream-layer",
            capabilities=[
                ServiceCapability("generate_style_profile", "Create an auditable D.A.I.S.E style influence profile"),
                ServiceCapability("apply_to_tinyprogrammer", "Send style, mood, tempo, theme, and brief controls to TinyProgrammer"),
            ],
        )
    )

    registry.register(
        Service(
            name="blackbox_archive",
            service_type="archive",
            endpoint="local://blackbox-archive",
            capabilities=[
                ServiceCapability("set_capture_profile", "Switch bounded, metadata-only, or explicitly approved continuous capture profile"),
                ServiceCapability("seal_capsule", "Write sealed capsule manifest, hashes, events, and artifact references"),
            ],
        )
    )

    registry.register(
        Service(
            name="vision",
            service_type="perception",
            endpoint="local://vision-tools",
            capabilities=[
                ServiceCapability("smoke", "Run bounded vision smoke checks"),
                ServiceCapability("detect", "Run bounded local object detection"),
                ServiceCapability("ingest_knowledge", "Ingest CV docs and labels into D.A.I.S.E knowledge"),
            ],
        )
    )
    
    # JARVIS
    registry.register(
        Service(
            name="jarvis",
            service_type="reasoning",
            endpoint="http://localhost:5003",
            capabilities=[
                ServiceCapability(
                    name="query_and_respond",
                    description="Process user query, reason, and respond",
                    input_schema={"query": "str", "context": "dict"},
                    output_schema={"response": "str", "reasoning_trace": "dict"},
                    latency_ms=500,
                ),
            ],
        )
    )
