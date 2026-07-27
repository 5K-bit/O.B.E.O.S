from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Protocol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
FRAME_TEXT_MSG = 0x0B
FRAME_CAPTURE_SETTINGS_MSG = 0x0D
FRAME_AUDIO_SUBS_MSG = 0x30
FRAME_TAP_SUBS_MSG = 0x10
FRAME_IMU_SUBS_MSG = 0x40


def _now() -> float:
    return time.time()


def frame_lua_app_path() -> Path:
    return Path(__file__).resolve().parent / "frame_lua" / "obeos_frame_app.lua"


def compact_frame_text(title: str, body: str = "", *, max_chars: int = 220) -> str:
    parts = [str(title or "").strip(), str(body or "").strip()]
    text = "\n".join(part for part in parts if part)
    text = " ".join(text.split()) if "\n" not in text else "\n".join(" ".join(line.split()) for line in text.splitlines())
    if len(text) <= max_chars:
        return text or "O.B.E.O.S"
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def frame_tap_intent(tap_count: int) -> str:
    if tap_count >= 3:
        return "look_and_ask"
    if tap_count == 2:
        return "tap_to_talk"
    return "status_card"


def build_frame_tap_payload(
    tap_count: int,
    *,
    device_id: str = "frame",
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    count = max(1, int(tap_count or 1))
    intent = frame_tap_intent(count)
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "tap_count": count,
        "intent": intent,
        "session_id": session_id,
        "request_id": request_id,
        "observed_at": _now(),
        "provenance": {
            "source": "frame",
            "origin_service": "frame_bridge",
            "collection_method": "frame_imu_tap_callback",
            "schema_version": SCHEMA_VERSION,
        },
    }


def build_frame_imu_payload(
    *,
    pitch: float,
    roll: float,
    heading: Optional[float] = None,
    device_id: str = "frame",
) -> Dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "pitch": round(float(pitch), 3),
        "roll": round(float(roll), 3),
        "heading": None if heading is None else round(float(heading), 3),
        "gesture": frame_imu_gesture(pitch=float(pitch), roll=float(roll), heading=heading),
        "observed_at": _now(),
        "provenance": {
            "source": "frame",
            "origin_service": "frame_bridge",
            "collection_method": "frame_imu_stream",
            "schema_version": SCHEMA_VERSION,
        },
    }
    return payload


def frame_imu_gesture(*, pitch: float, roll: float, heading: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if abs(roll) >= 22:
        return {"action": "rotate", "delta": {"amount": max(-0.35, min(0.35, roll / 120.0))}}
    if abs(pitch) >= 18:
        return {"action": "zoom", "delta": {"amount": max(-0.22, min(0.22, pitch / 140.0))}}
    if heading is not None and abs(float(heading)) >= 45:
        return {"action": "switch_layer", "target": {"layer": "sensors"}}
    return None


def build_frame_device_report(
    *,
    device_id: str = "frame",
    label: str = "Brilliant Frame",
    status: str = "online",
    battery_percent: Optional[float] = None,
    firmware_version: Optional[str] = None,
    git_tag: Optional[str] = None,
    connected_name: Optional[str] = None,
) -> Dict[str, Any]:
    health: Dict[str, Any] = {"overall": "healthy" if status == "online" else status}
    telemetry: Dict[str, Any] = {
        "connected_name": connected_name,
        "firmware_version": firmware_version,
        "git_tag": git_tag,
    }
    if battery_percent is not None:
        health["battery_percent"] = battery_percent
        telemetry["battery_percent"] = battery_percent
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "label": label,
        "role": "wearable_frame",
        "endpoint": "ble://brilliant-frame",
        "status": status,
        "health": health,
        "sensors": {
            "display": {"available": True, "active": status == "online"},
            "tap": {"available": True, "active": status == "online"},
            "imu": {"available": True, "active": status == "online"},
            "camera": {"available": True, "active": False},
            "microphone": {"available": True, "active": False},
            "bluetooth": {"available": True, "active": status == "online"},
        },
        "services": {"frame_bridge": {"available": True, "active": status == "online"}},
        "capabilities": [
            "frame_display",
            "frame_taps",
            "frame_imu",
            "tap_to_talk",
            "look_and_ask",
            "thoughtglass_gestures",
        ],
        "telemetry": telemetry,
        "last_seen": _now(),
        "provenance": {"source": "frame", "origin_service": "frame_bridge"},
    }


class FrameTransport(Protocol):
    async def connect(self, *, name: Optional[str] = None) -> Dict[str, Any]:
        ...

    async def bootstrap_app(self, lua_path: Path) -> None:
        ...

    async def disconnect(self) -> None:
        ...

    async def send_text(self, text: str) -> None:
        ...

    async def subscribe_taps(self) -> asyncio.Queue[Any]:
        ...

    async def unsubscribe_taps(self) -> None:
        ...

    async def subscribe_imu(self) -> asyncio.Queue[Any]:
        ...

    async def unsubscribe_imu(self) -> None:
        ...

    async def request_photo(self, *, resolution: int = 720, timeout_s: float = 12.0) -> bytes:
        ...

    async def collect_audio_phrase(self, *, timeout_s: float = 8.0) -> bytes:
        ...


class BrilliantFrameTransport:
    """Real Brilliant Labs Frame transport using the current brilliant-msg SDK."""

    def __init__(self) -> None:
        self.frame: Any = None
        self._sdk: Dict[str, Any] = {}
        self._tap_rx: Any = None
        self._imu_rx: Any = None
        self._audio_rx: Any = None
        self._photo_rx: Any = None

    def _load_sdk(self) -> Dict[str, Any]:
        if self._sdk:
            return self._sdk
        try:
            from frame_msg import FrameMsg, RxAudio, RxIMU, RxPhoto, RxTap, TxCaptureSettings, TxCode, TxPlainText

            message_client = FrameMsg
            package_name = "frame-msg"
        except ImportError as exc:  # pragma: no cover - depends on optional hardware SDK
            try:
                from brilliant_msg import BrilliantMsg, RxAudio, RxIMU, RxPhoto, RxTap, TxCaptureSettings, TxCode, TxPlainText

                message_client = BrilliantMsg
                package_name = "brilliant-msg"
            except ImportError:
                raise RuntimeError(
                    "Brilliant Frame support requires the optional SDK packages. "
                    "Install with `pip install frame-msg frame-ble` or "
                    "`pip install brilliant-sdk brilliant-msg brilliant-ble`."
                ) from exc
        self._sdk = {
            "MessageClient": message_client,
            "RxAudio": RxAudio,
            "RxIMU": RxIMU,
            "RxPhoto": RxPhoto,
            "RxTap": RxTap,
            "TxCaptureSettings": TxCaptureSettings,
            "TxCode": TxCode,
            "TxPlainText": TxPlainText,
            "package": package_name,
        }
        return self._sdk

    async def connect(self, *, name: Optional[str] = None) -> Dict[str, Any]:
        sdk = self._load_sdk()
        self.frame = sdk["MessageClient"]()
        try:
            connected = await self.frame.connect(name=name) if name else await self.frame.connect()
        except TypeError:
            connected = await self.frame.connect()
        connected_name = connected if isinstance(connected, str) else name or getattr(getattr(self.frame, "ble", None), "name", None)
        info: Dict[str, Any] = {"connected_name": connected_name}
        for key, lua in {
            "firmware_version": "print(frame.FIRMWARE_VERSION)",
            "git_tag": "print(frame.GIT_TAG)",
            "battery_percent": "print(frame.battery_level())",
        }.items():
            try:
                value = await self.frame.send_lua(lua, await_print=True)
                info[key] = float(value) if key == "battery_percent" and value not in {None, ""} else value
            except Exception:
                logger.debug("frame_%s_probe_failed", key, exc_info=True)
        return info

    async def bootstrap_app(self, lua_path: Path) -> None:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        if hasattr(self.frame, "send_break_signal"):
            with contextlib.suppress(Exception):
                await self.frame.send_break_signal()
        with contextlib.suppress(Exception):
            await self.frame.print_short_text("O.B.E.O.S loading")
        await self.frame.upload_stdlua_libs(lib_names=["data", "battery", "camera", "code", "plain_text", "tap", "imu", "audio"])
        await self.frame.upload_frame_app(local_filename=str(lua_path))
        self.frame.attach_print_response_handler()
        await self.frame.start_frame_app()

    async def disconnect(self) -> None:
        if self.frame is None:
            return
        with contextlib.suppress(Exception):
            await self.unsubscribe_taps()
        with contextlib.suppress(Exception):
            await self.unsubscribe_imu()
        with contextlib.suppress(Exception):
            self.frame.detach_print_response_handler()
        with contextlib.suppress(Exception):
            await self.frame.stop_frame_app()
        await self.frame.disconnect()
        self.frame = None

    async def send_text(self, text: str) -> None:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        sdk = self._load_sdk()
        tx = sdk["TxPlainText"]
        try:
            packed = tx(text=text).pack()
        except TypeError:
            packed = tx(text).pack()
        await self.frame.send_message(FRAME_TEXT_MSG, packed)

    async def subscribe_taps(self) -> asyncio.Queue[Any]:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        sdk = self._load_sdk()
        self._tap_rx = sdk["RxTap"]()
        queue = await self._tap_rx.attach(self.frame)
        await self.frame.send_message(FRAME_TAP_SUBS_MSG, sdk["TxCode"](value=1).pack())
        return queue

    async def unsubscribe_taps(self) -> None:
        if self.frame is None:
            return
        sdk = self._load_sdk()
        with contextlib.suppress(Exception):
            await self.frame.send_message(FRAME_TAP_SUBS_MSG, sdk["TxCode"](value=0).pack())
        if self._tap_rx is not None:
            with contextlib.suppress(Exception):
                self._tap_rx.detach(self.frame)
        self._tap_rx = None

    async def subscribe_imu(self) -> asyncio.Queue[Any]:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        sdk = self._load_sdk()
        self._imu_rx = sdk["RxIMU"](smoothing_samples=5)
        queue = await self._imu_rx.attach(self.frame)
        await self.frame.send_message(FRAME_IMU_SUBS_MSG, sdk["TxCode"](value=1).pack())
        return queue

    async def unsubscribe_imu(self) -> None:
        if self.frame is None:
            return
        sdk = self._load_sdk()
        with contextlib.suppress(Exception):
            await self.frame.send_message(FRAME_IMU_SUBS_MSG, sdk["TxCode"](value=0).pack())
        if self._imu_rx is not None:
            with contextlib.suppress(Exception):
                self._imu_rx.detach(self.frame)
        self._imu_rx = None

    async def request_photo(self, *, resolution: int = 720, timeout_s: float = 12.0) -> bytes:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        sdk = self._load_sdk()
        self._photo_rx = sdk["RxPhoto"](upright=True)
        queue = await self._photo_rx.attach(self.frame)
        await self.frame.send_message(FRAME_CAPTURE_SETTINGS_MSG, sdk["TxCaptureSettings"](resolution=resolution).pack())
        try:
            return bytes(await asyncio.wait_for(queue.get(), timeout=timeout_s))
        finally:
            with contextlib.suppress(Exception):
                self._photo_rx.detach(self.frame)
            self._photo_rx = None

    async def collect_audio_phrase(self, *, timeout_s: float = 8.0) -> bytes:
        if self.frame is None:
            raise RuntimeError("Frame is not connected")
        sdk = self._load_sdk()
        self._audio_rx = sdk["RxAudio"](streaming=True)
        queue = await self._audio_rx.attach(self.frame)
        chunks: list[bytes] = []
        await self.frame.send_message(FRAME_AUDIO_SUBS_MSG, sdk["TxCode"](value=1).pack())
        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_s))
        try:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if chunk is None:
                    break
                chunks.append(bytes(chunk))
        finally:
            with contextlib.suppress(Exception):
                await self.frame.send_message(FRAME_AUDIO_SUBS_MSG, sdk["TxCode"](value=0).pack())
            with contextlib.suppress(Exception):
                self._audio_rx.detach(self.frame)
            self._audio_rx = None
        return b"".join(chunks)


class FrameHubClient:
    async def post_device_report(self, device_id: str, report: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def post_frame_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        confidence: float = 0.9,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def post_gesture(self, gesture: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def set_sensor_mode(self, device_id: str, *, active: bool, mode: str, consent: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def post_chat(self, message: str, *, modality: str, context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def status_card(self) -> Dict[str, Any]:
        return {"title": "O.B.E.O.S", "body": "Frame online"}

    async def iter_live_events(self) -> AsyncIterator[Dict[str, Any]]:
        if False:
            yield {}


@dataclass
class HttpFrameHubClient(FrameHubClient):
    hub_url: str = "http://127.0.0.1:8080"
    token: str = ""
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        self.hub_url = str(self.hub_url or "http://127.0.0.1:8080").rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {"X-Blackfong-Agent-Token": self.token} if self.token else {}

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(f"{self.hub_url}{path}", json=payload, headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def _get(self, path: str) -> Dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.get(f"{self.hub_url}{path}", headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def post_device_report(self, device_id: str, report: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post(f"/api/devices/{device_id}/report", report)

    async def post_frame_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        confidence: float = 0.9,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        body = {
            "event_type": event_type,
            "payload": payload,
            "confidence": confidence,
            "session_id": session_id,
            "request_id": request_id,
            "correlation_id": correlation_id,
            "tags": tags or ["frame", event_type],
        }
        return await self._post("/api/frame/events", body)

    async def post_gesture(self, gesture: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post("/api/cognition/gesture", gesture)

    async def set_sensor_mode(self, device_id: str, *, active: bool, mode: str, consent: Dict[str, Any]) -> Dict[str, Any]:
        route = "start" if active else "stop"
        return await self._post(f"/api/devices/{device_id}/sensors/{route}", {"mode": mode, "consent": consent})

    async def post_chat(self, message: str, *, modality: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post("/api/frame/chat", {"message": message, "modality": modality, "context": context})

    async def status_card(self) -> Dict[str, Any]:
        try:
            metrics = await self._get("/api/metrics")
            health = metrics.get("system_health") or metrics.get("status") or "online"
            pending = metrics.get("pending_requests", 0)
            return {"title": "O.B.E.O.S", "body": f"Health: {health}\nQueue: {pending}\nDouble tap: talk\nTriple tap: look"}
        except Exception:
            return await super().status_card()

    async def iter_live_events(self) -> AsyncIterator[Dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{self.hub_url}/api/live/stream", headers=self._headers()) as response:
                response.raise_for_status()
                event_type = "message"
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "":
                        if data_lines:
                            raw = "\n".join(data_lines)
                            with contextlib.suppress(json.JSONDecodeError):
                                yield {"event": event_type, "data": json.loads(raw)}
                        event_type = "message"
                        data_lines = []


@dataclass
class FrameBridgeService:
    hub: FrameHubClient
    transport: FrameTransport
    device_id: str = "frame"
    label: str = "Brilliant Frame"
    frame_name: Optional[str] = None
    session_id: str = "frame-session"
    heartbeat_interval_s: float = 20.0
    audio_timeout_s: float = 8.0
    capture_artifacts: bool = False
    _connected_info: Dict[str, Any] = field(default_factory=dict)
    _tap_to_talk_active: bool = False
    _running: bool = False

    async def connect(self) -> Dict[str, Any]:
        self._connected_info = await self.transport.connect(name=self.frame_name)
        await self.transport.bootstrap_app(frame_lua_app_path())
        report = build_frame_device_report(
            device_id=self.device_id,
            label=self.label,
            connected_name=self._connected_info.get("connected_name"),
            firmware_version=self._connected_info.get("firmware_version"),
            git_tag=self._connected_info.get("git_tag"),
            battery_percent=self._connected_info.get("battery_percent"),
        )
        await self.hub.post_device_report(self.device_id, report)
        await self.send_display("O.B.E.O.S", "Frame online\nDouble tap: talk\nTriple tap: look")
        return report

    async def disconnect(self) -> None:
        await self.transport.disconnect()
        report = build_frame_device_report(device_id=self.device_id, label=self.label, status="offline")
        with contextlib.suppress(Exception):
            await self.hub.post_device_report(self.device_id, report)

    async def run(self, *, once: bool = False) -> None:
        await self.connect()
        if once:
            await self.disconnect()
            return
        self._running = True
        tasks = [
            asyncio.create_task(self._tap_loop()),
            asyncio.create_task(self._imu_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._live_stream_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._running = False
            for task in tasks:
                task.cancel()
            await self.disconnect()

    async def send_display(self, title: str, body: str = "", *, status: str = "sent") -> None:
        text = compact_frame_text(title, body)
        await self.transport.send_text(text)
        await self.hub.post_frame_event(
            "frame_display_update",
            {
                "schema_version": SCHEMA_VERSION,
                "device_id": self.device_id,
                "title": title,
                "body": body,
                "text": text,
                "status": status,
                "sent_at": _now(),
                "provenance": {"source": "frame", "origin_service": "frame_bridge"},
            },
            confidence=0.96,
            session_id=self.session_id,
            tags=["frame", "display"],
        )

    async def handle_tap(self, tap_count: int) -> Dict[str, Any]:
        payload = build_frame_tap_payload(tap_count, device_id=self.device_id, session_id=self.session_id)
        await self.hub.post_frame_event("frame_tap_event", payload, confidence=0.94, session_id=self.session_id)
        intent = payload["intent"]
        if intent == "status_card":
            status = await self.hub.status_card()
            await self.send_display(str(status.get("title") or "O.B.E.O.S"), str(status.get("body") or "Frame online"))
        elif intent == "tap_to_talk":
            await self._run_tap_to_talk()
        else:
            await self._run_look_and_ask()
        return payload

    async def handle_imu(self, imu_update: Any) -> Dict[str, Any]:
        pitch = float(getattr(imu_update, "pitch", 0.0) if not isinstance(imu_update, dict) else imu_update.get("pitch", 0.0))
        roll = float(getattr(imu_update, "roll", 0.0) if not isinstance(imu_update, dict) else imu_update.get("roll", 0.0))
        heading = getattr(imu_update, "compass", None) if not isinstance(imu_update, dict) else imu_update.get("heading", imu_update.get("compass"))
        payload = build_frame_imu_payload(pitch=pitch, roll=roll, heading=heading, device_id=self.device_id)
        await self.hub.post_frame_event("frame_imu_observation", payload, confidence=0.82, session_id=self.session_id)
        if payload.get("gesture"):
            await self.hub.post_gesture(payload["gesture"])
        return payload

    async def _tap_loop(self) -> None:
        queue = await self.transport.subscribe_taps()
        try:
            while True:
                tap_count = await queue.get()
                await self.handle_tap(int(tap_count or 1))
        finally:
            await self.transport.unsubscribe_taps()

    async def _imu_loop(self) -> None:
        queue = await self.transport.subscribe_imu()
        try:
            while True:
                imu_update = await queue.get()
                await self.handle_imu(imu_update)
        finally:
            await self.transport.unsubscribe_imu()

    async def _heartbeat_loop(self) -> None:
        while True:
            report = build_frame_device_report(
                device_id=self.device_id,
                label=self.label,
                connected_name=self._connected_info.get("connected_name"),
                firmware_version=self._connected_info.get("firmware_version"),
                git_tag=self._connected_info.get("git_tag"),
                battery_percent=self._connected_info.get("battery_percent"),
            )
            await self.hub.post_device_report(self.device_id, report)
            await asyncio.sleep(max(5.0, float(self.heartbeat_interval_s)))

    async def _live_stream_loop(self) -> None:
        async for event in self.hub.iter_live_events():
            event_type = str(event.get("event") or "")
            data = event.get("data") or {}
            if event_type == "notification_created":
                await self.send_display(str(data.get("title") or "Notification"), str(data.get("message") or data.get("body") or ""))
            elif event_type == "cognition_fault":
                await self.send_display("Thoughtglass", str(data.get("message") or data.get("code") or "Cognition fault"))
            elif event_type == "chat_complete":
                result = data.get("result") or {}
                await self.send_display("D.A.I.S.E", str(result.get("response") or data.get("response") or "Done"))

    async def _run_tap_to_talk(self) -> None:
        await self.hub.set_sensor_mode(
            self.device_id,
            active=True,
            mode="tap_to_talk",
            consent={"approved": True, "level": 1, "reason": "operator_double_tap"},
        )
        await self.send_display("Listening", "Speak now")
        audio = await self.transport.collect_audio_phrase(timeout_s=self.audio_timeout_s)
        await self.hub.set_sensor_mode(
            self.device_id,
            active=False,
            mode="tap_to_talk",
            consent={"approved": True, "level": 1, "reason": "tap_to_talk_complete"},
        )
        await self.hub.post_frame_event(
            "audio_sensor_observation",
            self._media_payload("audio", audio, media_type="audio/pcm;rate=8000", artifact_name="frame_audio.pcm"),
            confidence=0.84 if audio else 0.35,
            session_id=self.session_id,
            tags=["frame", "audio", "tap_to_talk"],
        )
        transcript = await self._transcribe_audio(audio)
        if not transcript:
            await self.send_display("D.A.I.S.E", "I did not catch that.")
            return
        response = await self.hub.post_chat(
            transcript,
            modality="voice",
            context={"input_origin": "frame", "device_id": self.device_id, "session_id": self.session_id},
        )
        result = response.get("result") or response
        await self.send_display("D.A.I.S.E", str(result.get("response") or result.get("message") or "Done"))

    async def _run_look_and_ask(self) -> None:
        await self.hub.set_sensor_mode(
            self.device_id,
            active=True,
            mode="look_and_ask",
            consent={"approved": True, "level": 1, "reason": "operator_triple_tap"},
        )
        await self.send_display("Looking", "Capturing photo")
        photo = await self.transport.request_photo(resolution=720)
        await self.hub.set_sensor_mode(
            self.device_id,
            active=False,
            mode="look_and_ask",
            consent={"approved": True, "level": 1, "reason": "look_and_ask_complete"},
        )
        payload = self._media_payload("vision", photo, media_type="image/jpeg", artifact_name="frame_photo.jpg")
        payload["vision_confidence"] = 0.78 if photo else 0.25
        await self.hub.post_frame_event(
            "vision_sensor_observation",
            payload,
            confidence=payload["vision_confidence"],
            session_id=self.session_id,
            tags=["frame", "vision", "look_and_ask"],
        )
        response = await self.hub.post_chat(
            "Use the latest Brilliant Frame photo as visual context and tell me what matters.",
            modality="vision",
            context={"input_origin": "frame", "device_id": self.device_id, "session_id": self.session_id, "frame_photo": payload},
        )
        result = response.get("result") or response
        await self.send_display("D.A.I.S.E", str(result.get("response") or result.get("message") or "Photo captured"))

    def _media_payload(self, modality: str, data: bytes, *, media_type: str, artifact_name: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "device_id": self.device_id,
            "modality": modality,
            "active_capture": False,
            "byte_count": len(data or b""),
            "capture_profile": "bounded_raw" if self.capture_artifacts else "metadata_only",
            "observed_at": _now(),
            "provenance": {
                "source": "frame",
                "origin_service": "frame_bridge",
                "collection_method": "operator_tap",
                "schema_version": SCHEMA_VERSION,
            },
        }
        if self.capture_artifacts and data:
            payload.update(
                {
                    "artifact_base64": base64.b64encode(data).decode("ascii"),
                    "artifact_name": artifact_name,
                    "media_type": media_type,
                }
            )
        return payload

    async def _transcribe_audio(self, pcm16_8khz: bytes) -> str:
        if not pcm16_8khz:
            return ""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.warning("frame_audio_transcription_unavailable")
            return ""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(pcm16_8khz)
            model = WhisperModel("base.en", device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(wav_path))
            return " ".join(segment.text.strip() for segment in segments if segment.text).strip()
        except Exception:
            logger.exception("frame_audio_transcription_failed")
            return ""
        finally:
            with contextlib.suppress(Exception):
                wav_path.unlink()


async def run_frame_agent(
    *,
    hub: str,
    token: str,
    device_id: str = "frame",
    label: str = "Brilliant Frame",
    frame_name: Optional[str] = None,
    once: bool = False,
    capture_artifacts: bool = False,
) -> None:
    client = HttpFrameHubClient(hub_url=hub, token=token)
    service = FrameBridgeService(
        hub=client,
        transport=BrilliantFrameTransport(),
        device_id=device_id,
        label=label,
        frame_name=frame_name,
        capture_artifacts=capture_artifacts,
    )
    await service.run(once=once)
