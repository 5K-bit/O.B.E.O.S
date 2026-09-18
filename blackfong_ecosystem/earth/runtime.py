"""Explicit startup/shutdown owner. Construction performs no network or model warmup."""

from __future__ import annotations
from pathlib import Path
import secrets
import socket
import threading
import time


class EarthRuntime:
    def __init__(self, data_dir: Path, assets: Path, *, offline=None, isolated=False):
        self.data_dir, self.assets = Path(data_dir), Path(assets)
        self.offline, self.isolated = offline, isolated
        self.token = secrets.token_urlsafe(32)
        self.browser_tickets = {}
        self.engine = self.control = self.backbone = self.server = self.thread = None
        self.started_seconds = None
        self.lease = None
        self._stopped = False

    def start(self):
        from .lease import RuntimeLease

        if self.lease is not None or self._stopped:
            raise RuntimeError("Create a new runtime to start again")
        self.lease = RuntimeLease(self.data_dir)
        try:
            return self._start()
        except BaseException:
            self.stop()
            raise

    def _start(self):
        started = time.perf_counter()
        from earthglass.spatial.engine import SpatialEngine
        from .control import EarthControl
        from .context_fabric import ContextFabric
        from .server import create_app
        import uvicorn

        self.data_dir.mkdir(parents=True, exist_ok=True)
        from .credentials import CredentialBroker
        self.broker = CredentialBroker(self.data_dir / "credential-references.json")
        if not self.isolated:
            from .backbone import EarthBackbone

            self.backbone = EarthBackbone()
        self.engine = SpatialEngine(
            self.data_dir / "history",
            credentials=self.broker.get,
            on_observations=self.backbone.publish if self.backbone else None,
            on_resync=self.backbone.publish_resync if self.backbone else None,
        )
        if self.offline is not None:
            self.engine.set_offline(self.offline, persist=False)
        import json

        self.camera_path = self.data_dir / "camera-locations.json"
        cameras = (
            json.loads(self.camera_path.read_text(encoding="utf-8"))
            if self.camera_path.exists()
            else {}
        )
        if self.backbone:
            self.engine.configure_perception(self.backbone.perception_events, cameras)
        context = ContextFabric(
            self.engine,
            event_reader=self.backbone.related if self.backbone else None,
            memory_reader=self.backbone.memory_references if self.backbone else None,
        )
        self.control = EarthControl(
            self.engine, self.data_dir / "scenes.sqlite3", context=context
        )
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.origin = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        application = create_app(
            self.engine,
            self.control,
            self.broker,
            self.assets,
            self.data_dir,
            self.token,
            origin=self.origin,
            browser_tickets=self.browser_tickets,
        )
        self.application = application
        self.server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=3,
            )
        )
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            name="earth-http",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Local Earth server did not start")
            time.sleep(0.02)
        self.engine.start()  # Local perception and maintenance also run offline.
        # The offline city index costs seconds to build; do it beside the globe so the
        # first search does not pay for it. Purely local: no network, no model.
        self.places_warmup = threading.Thread(
            target=self._warm_places, name="earth-places-warmup", daemon=True
        )
        self.places_warmup.start()
        self.started_seconds = time.perf_counter() - started
        return self.origin

    @staticmethod
    def _warm_places():
        try:
            from earthglass.spatial.places import cities

            cities()
        except Exception:
            pass  # Search still works; the index just builds on first use instead.

    def browser_url(self):
        for key, value in list(self.browser_tickets.items()):
            if value <= time.monotonic():
                self.browser_tickets.pop(key, None)
        if len(self.browser_tickets) >= 20:
            self.browser_tickets.pop(next(iter(self.browser_tickets)))
        ticket = secrets.token_urlsafe(32)
        self.browser_tickets[ticket] = time.monotonic() + 60
        return self.origin + "/#connect=" + ticket

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        errors = []
        metrics = {"startup_seconds": self.started_seconds}
        if self.engine:
            self.engine._stop.set()
        if hasattr(self, "application"):
            self.application.state.weather.stopping.set()
            self.application.state.daise.stop()
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)
        if self.engine:
            try:
                import psutil

                process = psutil.Process()
                processes = [process, *process.children(recursive=True)]
                metrics.update(
                    renderer=getattr(self.engine, "renderer_metrics", {}),
                    history=self.engine.history.stats(),
                    owned_process_memory_mib=round(
                        sum(p.memory_info().rss for p in processes if p.is_running())
                        / 1024**2,
                        1,
                    ),
                )
            except Exception as exc:
                errors.append(type(exc).__name__)
        for owner, method in (
            (self.engine, "stop"),
            (getattr(getattr(getattr(self, "application", None), "state", None), "tiles", None), "close"),
            (getattr(getattr(getattr(self, "application", None), "state", None), "presence", None), "close"),
            (self.control, "close"),
            (self.backbone, "close"),
            (getattr(self, "socket", None), "close"),
            (self.lease, "close"),
        ):
            if owner is not None:
                try:
                    getattr(owner, method)()
                except Exception as exc:
                    errors.append(type(exc).__name__)
        self.shutdown_errors = errors
        metrics["shutdown_errors"] = errors
        try:
            import json

            (self.data_dir / "runtime-metrics.json").write_text(
                json.dumps(metrics, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
