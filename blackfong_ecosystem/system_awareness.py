from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import time
from typing import Any, Dict, Iterable, List, Optional

from .capability_registry import capability_snapshot
from .config import chat_settings, paths
from .model_routing import model_chain_for_agent
from .perception_semantics import PerceptionGate
from .policy_engine import PHASE_57_EVENT_TYPES, PolicyEngine


SCHEMA_VERSION = "1.0"


class SystemAwarenessService:
    """Canonical local awareness model for diagnostics, sensors, and explanations."""

    def __init__(
        self,
        *,
        memory_service: Any = None,
        tinyprogrammer: Any = None,
        gps_service: Any = None,
        policy_engine: Optional[PolicyEngine] = None,
        startup_coordinator: Any = None,
        emit_min_interval: float = 30.0,
    ) -> None:
        self.memory_service = memory_service
        self.tinyprogrammer = tinyprogrammer
        self.gps_service = gps_service
        self.policy_engine = policy_engine or PolicyEngine()
        self.startup_coordinator = startup_coordinator
        self.perception_gate = PerceptionGate()
        self.emit_min_interval = emit_min_interval
        self._last_emit: Dict[str, float] = {}

    def build_snapshot(
        self,
        *,
        request_states: Optional[Dict[str, Dict[str, Any]]] = None,
        request_threads: Optional[Dict[str, Any]] = None,
        backend_bootstrap: Optional[Dict[str, Any]] = None,
        voice_status: Optional[Dict[str, Any]] = None,
        sensor_mode: str = "off",
        emit: bool = False,
    ) -> Dict[str, Any]:
        timestamp = time.time()
        backend = self.observe_backend(backend_bootstrap=backend_bootstrap)
        memory = self.observe_memory()
        routing = self.observe_routing()
        latency = self.observe_latency()
        services = self.observe_services(backend_bootstrap=backend_bootstrap, voice_status=voice_status)
        environment = self.observe_environment()
        queue_pressure = self.observe_queue_pressure(
            request_states=request_states,
            request_threads=request_threads,
            backend_bootstrap=backend_bootstrap,
        )
        sensors = self.observe_sensors(voice_status=voice_status, sensor_mode=sensor_mode)
        capabilities = capability_snapshot()
        compatibility = self.observe_schema_compatibility()

        observed = {
            "backend": backend,
            "memory": memory,
            "routing": routing,
            "latency": latency,
            "services": services,
            "environment": environment,
            "queue": queue_pressure,
            "capabilities": capabilities,
            "sensors": sensors,
        }
        contradictions = self.detect_contradictions(observed)
        health = self.calculate_health(observed, contradictions)
        inferred = self.infer_observations(observed, contradictions)
        diagnostic_summary = self.generate_diagnostic_summary(observed, inferred, health, contradictions)
        startup = None
        if self.startup_coordinator is not None:
            try:
                startup = self.startup_coordinator.refresh_from_runtime(
                    awareness_snapshot={
                        "observed": observed,
                        "health": health,
                        "sensors": sensors,
                    },
                    tiny_status=self.tinyprogrammer.get_status(require_stream=getattr(self.tinyprogrammer, "auto_stream", True))
                    if self.tinyprogrammer is not None
                    else {},
                )
            except Exception:
                startup = self.startup_coordinator.snapshot()

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": timestamp,
            "observed": observed,
            "inferred": inferred,
            "confidence": {
                "observed": 0.93,
                "inferred": inferred.get("confidence", 0.72),
            },
            "freshness": self._freshness_for(
                [
                    "backend",
                    "memory",
                    "routing",
                    "latency",
                    "services",
                    "environment",
                    "queue",
                    "capabilities",
                    "sensors",
                ],
                timestamp,
            ),
            "health": health,
            "provenance": self._provenance(
                source="event_store+local_probe",
                collection_method="canonical_awareness_snapshot",
            ),
            "policy": self.policy_engine.manifest(),
            "compatibility": compatibility,
            "sensors": sensors,
            "startup": startup or {},
            "diagnostic_summary": diagnostic_summary,
        }

        if emit:
            self.emit_snapshot_events(snapshot)
        return snapshot

    def performance_slice(self, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot = snapshot or self.build_snapshot()
        observed = snapshot.get("observed", {})
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": snapshot.get("timestamp"),
            "backend": observed.get("backend", {}),
            "latency": observed.get("latency", {}),
            "queue": observed.get("queue", {}),
            "memory": observed.get("memory", {}),
            "diagnostic_summary": snapshot.get("diagnostic_summary", {}),
            "provenance": snapshot.get("provenance", {}),
        }

    def environment_slice(self, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot = snapshot or self.build_snapshot()
        observed = snapshot.get("observed", {})
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": snapshot.get("timestamp"),
            "environment": observed.get("environment", {}),
            "services": observed.get("services", {}),
            "backend": observed.get("backend", {}),
            "health": snapshot.get("health", {}),
            "provenance": snapshot.get("provenance", {}),
        }

    def explain_why(
        self,
        topic: str = "last decision",
        *,
        request_id: Optional[str] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = snapshot or self.build_snapshot()
        topic_lower = topic.lower().strip()
        evidence: List[Dict[str, Any]] = []
        answer = "I do not have enough evidence to explain that yet."
        confidence = 0.45

        if request_id and self.memory_service is not None:
            trace = self.memory_service.get_request_trace(request_id)
            evidence.append({"kind": "request_trace", "request_id": request_id, "trace": trace})
            selection = self._last_trace_event(trace, "agent_selection")
            routing = self._last_trace_event(trace, "routing_decision")
            latency_event = self._last_trace_event(trace, "latency_observation")
            if "decision" in topic_lower or "selected" in topic_lower or "routing" in topic_lower:
                selected = (selection or {}).get("payload", {}).get("selected_agent")
                routed = (routing or {}).get("payload", {}).get("suggested_agent")
                answer = f"D.A.I.S.E routed toward {routed or 'unknown'} and selected {selected or routed or 'unknown'} based on routing relevance, agent confidence, and trust score."
                confidence = 0.82

        observed = snapshot.get("observed", {})
        if any(token in topic_lower for token in ("slow", "latency", "performance", "response time")):
            queue = observed.get("queue", {})
            latency = observed.get("latency", {})
            backend = observed.get("backend", {})
            if request_id and "latency_event" in locals() and latency_event:
                payload = latency_event.get("payload", {})
                prompt_metrics = payload.get("prompt_metrics") or {}
                prompt_tokens = (
                    payload.get("prompt_eval_count")
                    or prompt_metrics.get("approx_prompt_tokens")
                    or prompt_metrics.get("prompt_context_tokens_estimate")
                    or "unknown"
                )
                backend_duration = payload.get("ollama_total_duration_ms") or payload.get("wall_time_ms")
                fallback_reason = payload.get("fallback_reason") or payload.get("timeout_reason")
                answer = (
                    f"That request took {float(payload.get('execution_time_ms', 0.0) or 0.0) / 1000:.1f}s. "
                    f"I observed provider={payload.get('provider', 'unknown')}, model={payload.get('model', 'unknown')}, "
                    f"prompt tokens={prompt_tokens}, backend duration={backend_duration or 'unknown'}ms, "
                    f"attempts={payload.get('ollama_attempts', 'unknown')}. "
                    f"{'Fallback reason: ' + str(fallback_reason) if fallback_reason else 'No fallback reason was recorded.'}"
                )
            else:
                answer = (
                    f"The current bottleneck appears to be {snapshot.get('diagnostic_summary', {}).get('top_bottleneck', 'unknown')}. "
                    f"Average response time is {latency.get('average_response_time_s', 0)}s, pending requests are {queue.get('pending_requests', 0)}, "
                    f"and backend state is {backend.get('status', 'unknown')}."
                )
            evidence.append({"kind": "performance", "latency": latency, "queue": queue, "backend": backend})
            confidence = 0.78
        elif any(token in topic_lower for token in ("backend", "ollama", "model")):
            backend = observed.get("backend", {})
            answer = f"I observed backend status as {backend.get('status', 'unknown')} for {backend.get('provider', 'unknown')} using model {backend.get('configured_model', 'unknown')}."
            evidence.append({"kind": "backend", "backend": backend})
            confidence = 0.84
        elif any(token in topic_lower for token in ("speak", "hear", "see", "sensor", "camera", "microphone")):
            sensors = snapshot.get("sensors", {})
            answer = (
                f"Sensor awareness says speak={sensors.get('speaker', {}).get('can_speak_state', 'unknown')}, "
                f"hear={sensors.get('microphone', {}).get('can_hear_state', 'unknown')}, "
                f"see={sensors.get('camera', {}).get('can_see_state', 'unknown')}. "
                "This is capability/readiness only, not active capture unless explicitly enabled."
            )
            evidence.append({"kind": "sensors", "sensors": sensors})
            confidence = 0.86
        elif any(token in topic_lower for token in ("limits", "policy", "governance")):
            answer = "Policy precedence is safety, integrity, governance, performance, then user convenience. Safety wins if policies conflict."
            evidence.append({"kind": "policy", "policy": snapshot.get("policy", {})})
            confidence = 0.9

        return {
            "schema_version": SCHEMA_VERSION,
            "topic": topic or "last decision",
            "request_id": request_id,
            "answer": answer,
            "confidence": confidence,
            "evidence": evidence[:4],
            "provenance": self._provenance(
                source="awareness_snapshot+event_store",
                collection_method="provenance_backed_explanation",
                derived_from=[request_id] if request_id else [],
            ),
        }

    def observe_backend(self, *, backend_bootstrap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        status = "unknown"
        error = None
        installed_models: List[str] = []
        configured_model = chat_settings.ollama_model
        provider = chat_settings.backend.lower()
        model_available: Optional[bool] = None
        probe_started = time.time()

        if provider == "ollama":
            try:
                import httpx

                with httpx.Client(timeout=1.2) as client:
                    response = client.get(f"{chat_settings.ollama_url.rstrip('/')}/api/tags")
                    response.raise_for_status()
                    data = response.json()
                installed_models = [
                    str(model.get("name") or "")
                    for model in data.get("models", [])
                    if model.get("name")
                ]
                configured_models = {
                    chat_settings.ollama_model,
                    chat_settings.jarvis_model,
                    chat_settings.coding_model,
                    chat_settings.lilo_model,
                    chat_settings.stitch_model,
                    chat_settings.voice_model,
                }
                model_available = any(model in installed_models for model in configured_models)
                status = "available" if model_available else "degraded"
                error = None if model_available else "Ollama is reachable, but no configured O.B.E.O.S model was found in /api/tags."
            except Exception as exc:
                status = "unavailable"
                error = str(exc)

        bootstrap = dict(backend_bootstrap or {})
        runtime_health = (
            self.memory_service.recent_backend_health()
            if self.memory_service is not None
            else {}
        )
        response_profiles = {
            "fast": {
                "model_chain": model_chain_for_agent("jarvis", "fast"),
                "timeout_s": chat_settings.fast_timeout,
                "num_ctx": chat_settings.fast_num_ctx,
                "num_predict": chat_settings.fast_num_predict,
            },
            "standard": {
                "model_chain": model_chain_for_agent("jarvis", "standard"),
                "timeout_s": chat_settings.standard_timeout,
                "num_ctx": chat_settings.standard_num_ctx,
                "num_predict": chat_settings.standard_num_predict,
            },
            "deep": {
                "model_chain": model_chain_for_agent("jarvis", "deep"),
                "timeout_s": chat_settings.deep_timeout,
                "num_ctx": chat_settings.deep_num_ctx,
                "num_predict": chat_settings.deep_num_predict,
            },
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "status": status,
            "configured_model": configured_model,
            "agent_models": {
                "jarvis": chat_settings.jarvis_model,
                "coding": chat_settings.coding_model,
                "lilo": chat_settings.lilo_model,
                "stitch": chat_settings.stitch_model,
                "voice": chat_settings.voice_model,
            },
            "model_available": model_available,
            "installed_models": installed_models[:20],
            "installed_model_count": len(installed_models),
            "base_url": chat_settings.ollama_url,
            "response_profiles": response_profiles,
            "recent_timeout_count": runtime_health.get("recent_timeout_count"),
            "recent_fallback_count": runtime_health.get("recent_fallback_count"),
            "degraded_reason": runtime_health.get("degraded_reason"),
            "recovery_reason": runtime_health.get("recovery_reason"),
            "timeout_window_seconds": runtime_health.get("timeout_window_seconds"),
            "allow_recovery_probe": runtime_health.get("allow_recovery_probe"),
            "repeated_recent_failures": runtime_health.get("repeated_recent_failures"),
            "probe_ms": round((time.time() - probe_started) * 1000, 2),
            "bootstrap": bootstrap,
            "error": error,
            "freshness": self._freshness(time.time()),
            "provenance": self._provenance(source="local_ollama_probe", collection_method="http_get_api_tags"),
        }

    def observe_memory(self) -> Dict[str, Any]:
        if self.memory_service is None:
            stats = {"status": "unavailable"}
        else:
            try:
                stats = dict(self.memory_service.get_memory_stats())
                stats["status"] = "available"
            except Exception as exc:
                stats = {"status": "degraded", "error": str(exc)}
        stats.update(
            {
                "schema_version": SCHEMA_VERSION,
                "freshness": self._freshness(time.time()),
                "provenance": self._provenance(source="event_store", collection_method="memory_stats"),
            }
        )
        return stats

    def observe_routing(self) -> Dict[str, Any]:
        if self.memory_service is None:
            return {"schema_version": SCHEMA_VERSION, "status": "unknown", "recent_decisions": 0}
        try:
            events = self.memory_service.event_store.list_events_by_type("routing_decision", limit=50)
            confidences = [float(event.payload.get("routing_confidence", 0.0)) for event in events]
            avg_confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "available",
                "recent_decisions": len(events),
                "average_routing_confidence": avg_confidence,
                "latest_selected": events[0].payload.get("suggested_agent") if events else None,
                "freshness": self._freshness(time.time()),
                "provenance": self._provenance(
                    source="event_store",
                    collection_method="routing_decision_rollup",
                    derived_from=[event.id for event in events[:5]],
                ),
            }
        except Exception as exc:
            return {"schema_version": SCHEMA_VERSION, "status": "degraded", "error": str(exc)}

    def observe_latency(self) -> Dict[str, Any]:
        if self.memory_service is None:
            return {"schema_version": SCHEMA_VERSION, "status": "unknown"}
        try:
            responses = self.memory_service.event_store.list_events_by_type("agent_response", limit=50)
            latency_observations = self.memory_service.event_store.list_events_by_type("latency_observation", limit=50)
            timings = [float(event.payload.get("execution_time_ms", 0.0)) for event in responses if float(event.payload.get("execution_time_ms", 0.0)) > 0]
            avg_s = round(sum(timings) / len(timings) / 1000, 3) if timings else 0.0
            latest_s = round(timings[0] / 1000, 3) if timings else 0.0
            errors = [event for event in responses if event.payload.get("success") is False]
            prompt_counts = [
                float(event.payload.get("prompt_eval_count") or (event.payload.get("prompt_metrics") or {}).get("approx_prompt_tokens") or 0.0)
                for event in latency_observations
            ]
            prompt_counts = [count for count in prompt_counts if count > 0]
            ollama_durations = [
                float(event.payload.get("ollama_total_duration_ms") or 0.0)
                for event in latency_observations
                if float(event.payload.get("ollama_total_duration_ms") or 0.0) > 0
            ]
            timeouts = [
                event for event in latency_observations
                if event.payload.get("timeout_reason") or "timed out" in str(event.payload.get("fallback_reason", "")).lower()
            ]
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "available",
                "sample_size": len(timings),
                "average_response_time_s": avg_s,
                "latest_response_time_s": latest_s,
                "error_count": len(errors),
                "fallback_count": len([event for event in responses if event.payload.get("backend_error")]),
                "timeout_count": len(timeouts),
                "average_prompt_tokens": round(sum(prompt_counts) / len(prompt_counts), 1) if prompt_counts else 0.0,
                "max_prompt_tokens": max(prompt_counts) if prompt_counts else 0.0,
                "average_ollama_duration_s": round(sum(ollama_durations) / len(ollama_durations) / 1000, 3) if ollama_durations else 0.0,
                "recent_latency_observations": len(latency_observations),
                "freshness": self._freshness(time.time()),
                "provenance": self._provenance(
                    source="event_store",
                    collection_method="agent_response_and_latency_observation_rollup",
                    derived_from=[event.id for event in responses[:3]] + [event.id for event in latency_observations[:3]],
                ),
            }
        except Exception as exc:
            return {"schema_version": SCHEMA_VERSION, "status": "degraded", "error": str(exc)}

    def observe_services(
        self,
        *,
        backend_bootstrap: Optional[Dict[str, Any]] = None,
        voice_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tiny_status = self._observe_tinyprogrammer_status()
        telegram_status = self._observe_telegram_status()
        return {
            "schema_version": SCHEMA_VERSION,
            "hud": {"status": "available"},
            "voice": voice_status or {"active": False, "message": "Voice loop status not queried"},
            "tinyprogrammer": tiny_status,
            "telegram": telegram_status,
            "backend_bootstrap": dict(backend_bootstrap or {}),
            "freshness": self._freshness(time.time()),
            "provenance": self._provenance(source="local_service_registry", collection_method="service_status_probe"),
        }

    def _observe_tinyprogrammer_status(self) -> Dict[str, Any]:
        if self.tinyprogrammer is None:
            return {"online": False, "mode": "not_configured"}
        if self.memory_service is not None:
            try:
                events = self.memory_service.event_store.list_events_by_type("tinyprogrammer_status", limit=1)
                if events:
                    event = events[0]
                    age_seconds = max(0.0, time.time() - (event.timestamp / 1000.0))
                    if age_seconds <= 60:
                        return {
                            **dict(event.payload or {}),
                            "awareness_probe": "recent_event_cache",
                            "staleness_seconds": round(age_seconds, 2),
                        }
            except Exception:
                pass
        try:
            if hasattr(self.tinyprogrammer, "get_status"):
                try:
                    status = self.tinyprogrammer.get_status(
                        require_stream=getattr(self.tinyprogrammer, "auto_stream", True)
                    )
                except TypeError:
                    status = self.tinyprogrammer.get_status()
                status = dict(status or {})
                status.setdefault("awareness_probe", "companion_status")
                return status
            if hasattr(self.tinyprogrammer, "_build_offline_status"):
                status = self.tinyprogrammer._build_offline_status()
                status["awareness_probe"] = "offline_snapshot_no_live_probe"
                return status
            return {"online": False, "mode": "unavailable", "error": "tinyprogrammer_status_method_missing"}
        except Exception as exc:
            return {"online": False, "mode": "unavailable", "error": str(exc)}

    def _observe_telegram_status(self) -> Dict[str, Any]:
        try:
            from .telegram_gateway import read_telegram_gateway_status

            status = read_telegram_gateway_status(
                event_store=getattr(self.memory_service, "event_store", None),
            )
            status["awareness_probe"] = "events_db_gateway_state"
            return status
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc), "awareness_probe": "telegram_status_failed"}

    def observe_environment(self) -> Dict[str, Any]:
        env: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "process_id": os.getpid(),
            "workspace_root": str(paths.root),
            "data_root": str(paths.data_root),
        }
        try:
            import psutil

            boot_time = psutil.boot_time()
            memory = psutil.virtual_memory()
            env.update(
                {
                    "host_uptime_s": round(time.time() - boot_time, 2),
                    "cpu_percent": psutil.cpu_percent(interval=None),
                    "ram_total_mb": round(memory.total / (1024 * 1024), 2),
                    "ram_available_mb": round(memory.available / (1024 * 1024), 2),
                    "ram_percent": memory.percent,
                }
            )
        except Exception as exc:
            env.update({"diagnostics": "minimal", "psutil_error": str(exc)})
        env.update(
            {
                "freshness": self._freshness(time.time()),
                "provenance": self._provenance(source="local_process", collection_method="platform_psutil_probe"),
            }
        )
        return env

    def observe_queue_pressure(
        self,
        *,
        request_states: Optional[Dict[str, Dict[str, Any]]] = None,
        request_threads: Optional[Dict[str, Any]] = None,
        backend_bootstrap: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        states = list((request_states or {}).values())
        now = time.time()
        pending = [state for state in states if state.get("status") not in {"complete", "error"}]
        active_threads = [
            thread for thread in (request_threads or {}).values()
            if getattr(thread, "is_alive", lambda: False)()
        ]
        ages = [max(0.0, now - float(state.get("started_at") or now)) for state in pending]
        oldest_age_ms = round(max(ages) * 1000, 2) if ages else 0.0
        backlog_health = "healthy"
        if len(pending) >= 5 or oldest_age_ms > 60000:
            backlog_health = "degraded"
        elif len(pending) >= 2 or oldest_age_ms > 20000:
            backlog_health = "partial"
        bootstrap = dict(backend_bootstrap or {})
        return {
            "schema_version": SCHEMA_VERSION,
            "pending_requests": len(pending),
            "active_workers": len(active_threads),
            "oldest_queued_age_ms": oldest_age_ms,
            "backend_warmup": bootstrap.get("status", "unknown"),
            "backlog_health": backlog_health,
            "freshness": self._freshness(now),
            "provenance": self._provenance(source="hud_runtime_state", collection_method="request_state_rollup"),
        }

    def observe_sensors(self, *, voice_status: Optional[Dict[str, Any]] = None, sensor_mode: str = "off") -> Dict[str, Any]:
        voice_status = voice_status or {}
        observed_at = time.time()
        normalized_mode = str(sensor_mode or "off").strip().lower()
        audio_mode_active = bool(voice_status.get("active"))
        audio_devices = self._probe_audio_devices()
        stt_ready = self._module_available("faster_whisper") or self._module_available("speech_recognition")
        tts_ready = self._module_available("pyttsx3") or self._module_available("TTS") or self._module_available("pygame")
        camera_library = self._module_available("cv2")

        mic_available = audio_devices.get("input_count", 0) > 0
        speaker_available = audio_devices.get("output_count", 0) > 0
        can_hear_if_enabled = mic_available and stt_ready
        can_speak = speaker_available and tts_ready
        can_see_if_enabled = camera_library
        audio_confidence = 0.78 if audio_mode_active and can_hear_if_enabled else 0.48 if can_hear_if_enabled else 0.15
        speech_confidence = 0.84 if can_speak else 0.45 if tts_ready else 0.12
        vision_confidence = 0.52 if can_see_if_enabled else 0.1
        screen_confidence = 0.0

        audio_observation = {
            "schema_version": SCHEMA_VERSION,
            "modality": "audio",
            "observed_at": observed_at,
            "active_capture": audio_mode_active,
            "audio_confidence": audio_confidence,
            "permission_state": "unknown",
            "provenance": self._provenance(source="local_hardware_probe", collection_method="audio_capability_probe"),
        }
        vision_observation = {
            "schema_version": SCHEMA_VERSION,
            "modality": "vision",
            "observed_at": observed_at,
            "active_capture": False,
            "vision_confidence": vision_confidence,
            "permission_state": "unknown",
            "provenance": self._provenance(source="local_hardware_probe", collection_method="vision_capability_probe"),
        }
        screen_observation = {
            "schema_version": SCHEMA_VERSION,
            "modality": "screen",
            "observed_at": observed_at,
            "active_capture": False,
            "screen_confidence": screen_confidence,
            "permission_state": "not_enabled",
            "provenance": self._provenance(source="local_runtime_state", collection_method="screen_snapshot_capability_probe"),
        }
        gps_snapshot = self._observe_gps_sensor()

        sensors = {
            "schema_version": SCHEMA_VERSION,
            "sensor_mode": normalized_mode,
            "microphone": {
                "present": mic_available if audio_devices.get("status") == "available" else None,
                "input_devices": audio_devices.get("input_count"),
                "stt_ready": stt_ready,
                "permission_state": "unknown",
                "active_capture": audio_mode_active,
                "audio_confidence": audio_confidence,
                "explicit_mode_required": True,
                "can_hear": audio_mode_active and can_hear_if_enabled,
                "can_hear_state": "active" if audio_mode_active and can_hear_if_enabled else "available_if_enabled" if can_hear_if_enabled else "unavailable_or_unknown",
            },
            "speaker": {
                "present": speaker_available if audio_devices.get("status") == "available" else None,
                "output_devices": audio_devices.get("output_count"),
                "tts_ready": tts_ready,
                "permission_state": "not_required_for_output",
                "speech_confidence": speech_confidence,
                "can_speak": can_speak,
                "can_speak_state": "ready" if can_speak else "partial" if tts_ready else "unavailable_or_unknown",
            },
            "camera": {
                "present": None,
                "camera_library_available": camera_library,
                "vision_ready": camera_library,
                "permission_state": "unknown",
                "active_capture": False,
                "vision_confidence": vision_confidence,
                "explicit_mode_required": True,
                "can_see": False,
                "can_see_state": "available_if_enabled" if can_see_if_enabled else "unavailable_or_unknown",
            },
            "screen": {
                "snapshot_available": False,
                "active_capture": False,
                "permission_state": "not_enabled",
                "screen_confidence": screen_confidence,
                "can_read_screen": False,
                "can_read_screen_state": "not_enabled",
            },
            "gps": gps_snapshot,
            "capture_policy": {
                "continuous_capture_allowed": False,
                "active_camera_capture": False,
                "active_microphone_capture": audio_mode_active,
                "perception_requires_gate": True,
            },
            "perception_gate": {
                "audio": self.perception_gate.decide(
                    modality="audio",
                    mode_enabled=audio_mode_active,
                    observation=audio_observation,
                    policy_allowed=True,
                    expected_modality="audio",
                ),
                "vision": self.perception_gate.decide(
                    modality="vision",
                    mode_enabled=normalized_mode in {"vision", "camera", "camera_snapshot", "vision_snapshot", "live_vision"},
                    observation=vision_observation,
                    policy_allowed=False,
                    expected_modality="vision",
                ),
                "screen": self.perception_gate.decide(
                    modality="screen",
                    mode_enabled=normalized_mode in {"screen", "screen_snapshot"},
                    observation=screen_observation,
                    policy_allowed=False,
                    expected_modality="screen",
                ),
            },
            "observations": {
                "audio": audio_observation,
                "vision": vision_observation,
                "screen": screen_observation,
            },
            "audio_probe": audio_devices,
            "freshness": self._freshness(observed_at),
            "provenance": self._provenance(source="local_hardware_probe", collection_method="safe_capability_probe"),
        }
        return sensors

    def _observe_gps_sensor(self) -> Dict[str, Any]:
        if self.gps_service is None:
            return {
                "available": False,
                "provider_type": "not_configured",
                "recording": False,
                "latest_fix": None,
                "accuracy_m": None,
                "last_update_at": None,
                "offline_state": "offline",
                "active_route": {"recording": False, "point_count": 0, "distance_km": 0.0},
                "trail": [],
                "provenance": self._provenance(source="awareness_runtime", collection_method="gps_not_configured"),
            }
        try:
            snapshot = dict(self.gps_service.snapshot() or {})
        except Exception as exc:
            return {
                "available": False,
                "provider_type": "unavailable",
                "recording": False,
                "latest_fix": None,
                "accuracy_m": None,
                "last_update_at": None,
                "offline_state": "degraded",
                "active_route": {"recording": False, "point_count": 0, "distance_km": 0.0},
                "trail": [],
                "error": str(exc),
                "provenance": self._provenance(source="awareness_runtime", collection_method="gps_probe_failed"),
            }
        snapshot.setdefault("provenance", self._provenance(source="gps_service", collection_method="shared_sensor_adapter"))
        return snapshot

    def observe_schema_compatibility(self) -> Dict[str, Any]:
        checks = {
            event_type: self.policy_engine.check_schema_compatibility(event_type, SCHEMA_VERSION)
            for event_type in sorted(PHASE_57_EVENT_TYPES)
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "default_status": "supported",
            "checks": checks,
            "freshness": self._freshness(time.time()),
            "provenance": self._provenance(source="policy_engine", collection_method="schema_compatibility_table"),
        }

    def detect_contradictions(self, observed: Dict[str, Any]) -> List[Dict[str, Any]]:
        contradictions: List[Dict[str, Any]] = []
        backend = observed.get("backend", {})
        bootstrap = backend.get("bootstrap", {}) if isinstance(backend.get("bootstrap"), dict) else {}
        if bootstrap.get("status") == "ready" and backend.get("status") == "unavailable":
            contradictions.append(
                {
                    "type": "consistency_conflict",
                    "risk_flag": "contradictory_backend_state",
                    "summary": "HUD bootstrap reports ready, but the live backend probe reports unavailable.",
                    "observations": {"bootstrap": bootstrap, "backend_status": backend.get("status"), "backend_error": backend.get("error")},
                }
            )

        services = observed.get("services", {})
        voice = services.get("voice", {}) if isinstance(services.get("voice"), dict) else {}
        sensors = observed.get("sensors", {})
        microphone = sensors.get("microphone", {}) if isinstance(sensors.get("microphone"), dict) else {}
        if voice.get("active") and microphone.get("can_hear_state") == "unavailable_or_unknown":
            contradictions.append(
                {
                    "type": "consistency_conflict",
                    "risk_flag": "voice_active_without_hearing_readiness",
                    "summary": "Voice loop reports active, but microphone/STT readiness is unavailable or unknown.",
                    "observations": {"voice": voice, "microphone": microphone},
                }
            )
        return contradictions

    def calculate_health(self, observed: Dict[str, Any], contradictions: List[Dict[str, Any]]) -> Dict[str, Any]:
        sections: Dict[str, str] = {}
        backend_status = observed.get("backend", {}).get("status", "unknown")
        sections["backend"] = "healthy" if backend_status == "available" else "degraded" if backend_status == "degraded" else "unavailable"
        sections["memory"] = "healthy" if observed.get("memory", {}).get("status") == "available" else "degraded"
        services = observed.get("services", {})
        tiny = services.get("tinyprogrammer", {}) if isinstance(services.get("tinyprogrammer"), dict) else {}
        sections["tinyprogrammer"] = "healthy" if tiny.get("online") else "partial"
        sections["queue"] = observed.get("queue", {}).get("backlog_health", "unknown")
        sections["sensors"] = "healthy" if observed.get("sensors", {}).get("speaker", {}).get("can_speak") else "partial"

        if contradictions:
            overall = "degraded"
        elif "unavailable" in sections.values():
            overall = "degraded"
        elif "degraded" in sections.values() or "partial" in sections.values():
            overall = "partial"
        else:
            overall = "healthy"
        return {
            "overall": overall,
            "sections": sections,
            "degradation_modes": sorted(set(sections.values())),
            "risk_flags": [item["risk_flag"] for item in contradictions],
            "contradictions": contradictions,
        }

    def infer_observations(self, observed: Dict[str, Any], contradictions: List[Dict[str, Any]]) -> Dict[str, Any]:
        likely_bottlenecks: List[str] = []
        recommendations: List[str] = []
        risk_flags = [item["risk_flag"] for item in contradictions]

        queue = observed.get("queue", {})
        backend = observed.get("backend", {})
        latency = observed.get("latency", {})
        memory = observed.get("memory", {})
        if queue.get("pending_requests", 0) > 0:
            likely_bottlenecks.append("queue_pressure")
            recommendations.append("Let active requests complete before starting deep multi-agent work.")
        if backend.get("status") != "available":
            likely_bottlenecks.append("backend_availability")
            recommendations.append("Check Ollama, configured model names, and keep-alive settings.")
        if float(latency.get("average_response_time_s", 0.0) or 0.0) > 8:
            likely_bottlenecks.append("model_latency")
            recommendations.append("Inspect backend load duration and response token budget.")
        if int(latency.get("timeout_count", 0) or 0) > 0:
            likely_bottlenecks.append("backend_timeout")
            recommendations.append("Use bounded quick-mode generation and avoid retrying read timeouts for D.A.I.S.E HUD chat.")
        if float(latency.get("average_prompt_tokens", 0.0) or 0.0) > chat_settings.quick_prompt_budget_tokens:
            likely_bottlenecks.append("prompt_context_growth")
            recommendations.append("Keep quick HUD prompts compact while preserving full transcript persistence.")
        if int(memory.get("pinned_memories", 0) or 0) > 100:
            likely_bottlenecks.append("memory_context_growth")
            recommendations.append("Prefer summaries and relevance thresholds as memory grows.")

        if not likely_bottlenecks:
            likely_bottlenecks.append("none_observed")
            recommendations.append("System looks stable from the current local probes.")

        return {
            "likely_bottlenecks": likely_bottlenecks,
            "risk_flags": risk_flags,
            "recommendations": recommendations,
            "confidence": 0.72 if likely_bottlenecks != ["none_observed"] else 0.68,
        }

    def generate_diagnostic_summary(
        self,
        observed: Dict[str, Any],
        inferred: Dict[str, Any],
        health: Dict[str, Any],
        contradictions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        bottleneck = (inferred.get("likely_bottlenecks") or ["unknown"])[0]
        risk = (health.get("risk_flags") or inferred.get("risk_flags") or ["none"])[0]
        recommendation = (inferred.get("recommendations") or ["No recommendation available."])[0]
        return {
            "schema_version": SCHEMA_VERSION,
            "top_bottleneck": bottleneck,
            "top_risk": risk,
            "top_recommendation": recommendation,
            "health": health.get("overall", "unknown"),
            "contradiction_count": len(contradictions),
            "backend_status": observed.get("backend", {}).get("status", "unknown"),
            "queue_health": observed.get("queue", {}).get("backlog_health", "unknown"),
            "memory_status": observed.get("memory", {}).get("status", "unknown"),
            "provenance": self._provenance(source="awareness_snapshot", collection_method="diagnostic_summary_generator"),
        }

    def diagnostic_hints_from_trace(self, trace: Dict[str, Any]) -> Dict[str, Any]:
        phases = trace.get("phases", [])
        slow_phases = [phase for phase in phases if phase.get("ms") and phase["ms"] > 5000]
        backend_error = trace.get("backend_error") or (trace.get("result") or {}).get("backend_error")
        latency = trace.get("latency_observation") or {}
        outcome = trace.get("request_outcome") or {}
        fallback_trigger = latency.get("fallback_trigger") or outcome.get("fallback_trigger")
        return {
            "slow_phases": slow_phases,
            "backend_error": backend_error,
            "response_path": latency.get("response_path") or outcome.get("response_path"),
            "model_chain": latency.get("model_chain") or outcome.get("model_chain") or [],
            "fallback_trigger": fallback_trigger,
            "effective_timeout_s": latency.get("effective_timeout_s") or outcome.get("effective_timeout_s"),
            "health_forced_fallback": bool(latency.get("health_forced_fallback") or outcome.get("health_forced_fallback")),
            "backend_health_state_at_request": latency.get("backend_health_state_at_request") or outcome.get("backend_health_state_at_request"),
            "recovery_probe_attempted": bool(latency.get("recovery_probe_attempted") or outcome.get("recovery_probe_attempted")),
            "recovery_probe_succeeded": bool(latency.get("recovery_probe_succeeded") or outcome.get("recovery_probe_succeeded")),
            "degraded_reason": latency.get("degraded_reason") or outcome.get("degraded_reason"),
            "recovery_reason": latency.get("recovery_reason") or outcome.get("recovery_reason"),
            "timeout_window_seconds": latency.get("timeout_window_seconds") or outcome.get("timeout_window_seconds"),
            "live_lane_attempted": bool(latency.get("live_lane_attempted") or outcome.get("live_lane_attempted")),
            "fallback_mode": latency.get("fallback_mode") or outcome.get("fallback_mode") or "none",
            "recommendation": (
                "Inspect backend availability, timeout budgets, degraded reasons, and whether a recovery probe was attempted before fallback."
                if backend_error or fallback_trigger
                else "Trace is progressing normally."
            ),
        }

    def emit_snapshot_events(self, snapshot: Dict[str, Any]) -> None:
        if self.memory_service is None:
            return
        observed = snapshot.get("observed", {})
        self._emit_event("awareness_snapshot", snapshot, confidence=0.93)
        self._emit_event("performance_snapshot", self.performance_slice(snapshot), confidence=0.86)
        self._emit_event("environment_snapshot", self.environment_slice(snapshot), confidence=0.9)
        self._emit_event("sensor_capability_snapshot", snapshot.get("sensors", {}), confidence=0.82)
        self._emit_event("audio_device_observation", snapshot.get("sensors", {}).get("audio_probe", {}), confidence=0.8)
        self._emit_event("camera_device_observation", snapshot.get("sensors", {}).get("camera", {}), confidence=0.68)
        observations = snapshot.get("sensors", {}).get("observations", {})
        audio_observation = observations.get("audio", {}) if isinstance(observations, dict) else {}
        vision_observation = observations.get("vision", {}) if isinstance(observations, dict) else {}
        screen_observation = observations.get("screen", {}) if isinstance(observations, dict) else {}
        self._emit_event("audio_sensor_observation", audio_observation, confidence=audio_observation.get("audio_confidence", 0.5))
        self._emit_event("vision_sensor_observation", vision_observation, confidence=vision_observation.get("vision_confidence", 0.5))
        self._emit_event("screen_snapshot_observation", screen_observation, confidence=screen_observation.get("screen_confidence", 0.2))
        perception_gate = snapshot.get("sensors", {}).get("perception_gate", {})
        if isinstance(perception_gate, dict):
            for modality, decision in perception_gate.items():
                payload = {"modality": modality, **decision} if isinstance(decision, dict) else {"modality": modality, "decision": decision}
                self._emit_event("perception_gate_decision", payload, confidence=float(payload.get("confidence", 0.5) or 0.5), force=True)
        self._emit_event("queue_pressure_observation", observed.get("queue", {}), confidence=0.9)
        self._emit_event("backend_health_observation", observed.get("backend", {}), confidence=0.9)
        self._emit_event("capability_snapshot", observed.get("capabilities", {}), confidence=0.95)
        for conflict in snapshot.get("health", {}).get("contradictions", []):
            self._emit_event("consistency_conflict", conflict, confidence=0.78, force=True)

    def _emit_event(self, event_type: str, payload: Dict[str, Any], *, confidence: float, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_emit.get(event_type, 0.0) < self.emit_min_interval:
            return
        self._last_emit[event_type] = now
        try:
            enriched = {
                "schema_version": SCHEMA_VERSION,
                **dict(payload or {}),
                "provenance": (payload or {}).get("provenance")
                or self._provenance(source="system_awareness", collection_method="event_projection"),
            }
            self.memory_service.persist_workflow_event(
                source="system",
                event_type=event_type,
                payload=enriched,
                confidence=confidence,
                tags=["awareness", event_type],
            )
        except Exception:
            return

    def _probe_audio_devices(self) -> Dict[str, Any]:
        if not self._module_available("sounddevice"):
            return {
                "status": "unknown",
                "input_count": 0,
                "output_count": 0,
                "reason": "sounddevice module unavailable; hardware was not opened or captured.",
            }
        try:
            sounddevice = importlib.import_module("sounddevice")
            devices = sounddevice.query_devices()
            input_count = 0
            output_count = 0
            for device in devices:
                try:
                    if int(device.get("max_input_channels", 0)) > 0:
                        input_count += 1
                    if int(device.get("max_output_channels", 0)) > 0:
                        output_count += 1
                except Exception:
                    continue
            return {
                "status": "available",
                "input_count": input_count,
                "output_count": output_count,
                "capture_started": False,
            }
        except Exception as exc:
            return {
                "status": "unknown",
                "input_count": 0,
                "output_count": 0,
                "error": str(exc),
                "capture_started": False,
            }

    @staticmethod
    def _module_available(module_name: str) -> bool:
        return importlib.util.find_spec(module_name) is not None

    @staticmethod
    def _freshness(observed_at: float) -> Dict[str, Any]:
        age = max(0.0, time.time() - observed_at)
        return {
            "observed_at": observed_at,
            "age_seconds": round(age, 3),
            "stale": age > 30.0,
        }

    def _freshness_for(self, sections: Iterable[str], observed_at: float) -> Dict[str, Any]:
        return {section: self._freshness(observed_at) for section in sections}

    @staticmethod
    def _provenance(
        *,
        source: str,
        collection_method: str,
        derived_from: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "source": source,
            "origin_service": "system_awareness",
            "derived_from": derived_from or [],
            "collection_method": collection_method,
            "observed_at": time.time(),
            "schema_version": SCHEMA_VERSION,
        }

    @staticmethod
    def _last_trace_event(trace: Dict[str, Any], event_type: str) -> Optional[Dict[str, Any]]:
        for event in reversed(trace.get("events", [])):
            if event.get("event_type") == event_type:
                return event
        return None
