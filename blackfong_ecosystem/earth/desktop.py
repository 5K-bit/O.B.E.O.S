"""Lightweight Earth-first PySide6 launcher; classic workspaces load only on request."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def repository_root():
    return Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description="OBEOS Earth desktop")
    parser.add_argument("--classic", action="store_true")
    parser.add_argument("--offline", action="store_true", default=None, help="Start without public network requests; local processing stays available")
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--maximized", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    root = repository_root()
    if args.classic:
        import desktop_shell

        return desktop_shell.main()
    # Pinned in-tree checkout is used in development; installed EarthGlass is the packaging dependency.
    if (root / "EarthGlass" / "earthglass").exists():
        sys.path.insert(0, str(root / "EarthGlass"))
    from PySide6.QtCore import (
        QObject,
        QSettings,
        QThread,
        QTimer,
        QUrl,
        Qt,
        Signal,
        Slot,
    )
    from PySide6.QtGui import QAction, QKeySequence, QDesktopServices
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QLabel,
        QToolBar,
        QInputDialog,
        QLineEdit,
        QMessageBox,
        QDockWidget,
        QTextBrowser,
    )
    from PySide6.QtWebEngineCore import (
        QWebEngineHttpRequest,
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineUrlRequestInterceptor,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel

    class Job(QThread):
        result = Signal(object)
        failed = Signal(str)

        def __init__(self, operation):
            super().__init__()
            self.operation = operation

        def run(self):
            try:
                self.result.emit(self.operation())
            except Exception as exc:
                self.failed.emit(type(exc).__name__ + ": " + str(exc)[:300])

    class Bridge(QObject):
        result = Signal(str)

        def __init__(self, home):
            super().__init__(home)
            self.home = home
            self.jobs = set()

        @Slot(str)
        def command(self, raw):
            if len(raw.encode()) > 65536 or not self.home.runtime or self.home.closing:
                return
            try:
                payload = json.loads(raw)
            except ValueError:
                return
            if len(self.jobs) >= 8:
                self.result.emit(json.dumps({"schema_version": "1.0", "request_id": payload.get("request_id") if isinstance(payload, dict) else None, "scene_id": "home", "ok": False, "result": None, "error": {"code": "busy", "message": "Earth is handling other commands. Please retry."}}))
                return
            job = Job(
                lambda: self.home.runtime.control.execute(payload, origin="operator")
            )
            self.jobs.add(job)
            job.result.connect(lambda value: self.result.emit(json.dumps(value)))
            job.finished.connect(lambda: self.jobs.discard(job))
            job.finished.connect(job.deleteLater)
            job.start()

    class NetworkBoundary(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            runtime = getattr(self.parent(), "runtime", None)
            if runtime and runtime.engine and runtime.engine.offline:
                url = info.requestUrl()
                if url.scheme() in {"http", "https", "ws", "wss"} and not url.toString().startswith(runtime.origin + "/"):
                    info.block(True)

    class Page(QWebEnginePage):
        def __init__(self, profile, parent):
            super().__init__(profile, parent)
            self.allowed_origin = None

        def acceptNavigationRequest(self, url, kind, main_frame):
            if (
                kind == QWebEnginePage.NavigationTypeLinkClicked
                and url.scheme() == "https"
            ):
                QDesktopServices.openUrl(url)
                return False
            return url.scheme() == "about" or bool(
                self.allowed_origin
                and url.toString().startswith(self.allowed_origin + "/")
            )

        def createWindow(self, kind):
            return None

        def javaScriptConsoleMessage(self, level, message, line, source):
            print("Earth renderer:", str(message)[:1200], flush=True)

    class Home(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("OBEOS · Earth")
            self.resize(1500, 950)
            self.settings = QSettings("OBEOS", "Earth")
            geometry = self.settings.value("window/geometry")
            if geometry is not None and not args.maximized:
                self.restoreGeometry(geometry)
            self.runtime = None
            self.closing = False
            self.stopped = False
            self.jobs = set()
            self.classic_processes = []
            self.classic_process = None
            self.classic_log = None
            self.started_at = time.perf_counter()
            self.placeholder = QLabel(
                "OBEOS  /  EARTH\n\nStarting your local workspace…"
            )
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.setCentralWidget(self.placeholder)
            self.toolbar = QToolBar("Workspaces")
            self.toolbar.setMovable(False)
            self.addToolBar(self.toolbar)
            for label in (
                "Earth",
                "Thoughtglass",
                "Memory Chamber",
                "Projects",
                "System",
            ):
                action = QAction(label, self)
                action.triggered.connect(
                    lambda checked=False, name=label: self.workspace(name)
                )
                self.toolbar.addAction(action)
            classic_action = self.toolbar.addAction("Classic Workspace")
            classic_action.triggered.connect(lambda: self.workspace("Thoughtglass"))
            self.toolbar.addSeparator()
            source_action = self.toolbar.addAction("Optional free sources")
            source_action.setShortcut(QKeySequence("Ctrl+K"))
            source_action.triggered.connect(self.credentials)
            browser_action = self.toolbar.addAction("Open in browser")
            browser_action.setShortcut(QKeySequence("Ctrl+B"))
            browser_action.triggered.connect(
                lambda: (
                    QDesktopServices.openUrl(QUrl(self.runtime.browser_url()))
                    if self.runtime
                    else None
                )
            )
            camera_action = self.toolbar.addAction("Camera locations")
            camera_action.triggered.connect(self.camera_location)
            reload_action = self.toolbar.addAction("Reload Earth")
            reload_action.triggered.connect(
                lambda: self.web.reload() if hasattr(self, "web") else None
            )
            reload_action.setShortcut(QKeySequence("Ctrl+R"))
            self.statusBar().showMessage("Local storage · no model warmup required")
            self.drawer = QDockWidget("System", self)
            self.details = QTextBrowser()
            self.drawer.setWidget(self.details)
            self.addDockWidget(Qt.BottomDockWidgetArea, self.drawer)
            self.drawer.hide()
            QTimer.singleShot(0, self.start_runtime)

        def start_runtime(self):
            def start():
                from .runtime import EarthRuntime

                default = (
                    Path(
                        os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
                    )
                    / "OBEOS"
                    / "Earth"
                )
                import earthglass

                development_assets = root / "EarthGlass" / "obeos-earth" / "dist"
                assets = (
                    development_assets
                    if development_assets.exists()
                    else Path(earthglass.__file__).parent / "desktop_assets"
                )
                self.runtime = EarthRuntime(
                    args.data_dir or default,
                    assets,
                    offline=args.offline,
                    isolated=args.isolated,
                )
                return self.runtime.start()

            self.start_job = Job(start)
            self.start_job.result.connect(self.ready)
            self.start_job.failed.connect(self.failed)
            self.start_job.start()

        def ready(self, origin):
            if self.closing:
                return
            self.web = QWebEngineView(self)
            self.profile = QWebEngineProfile(
                self.web
            )  # Ephemeral cookies/cache; local history has its own owner.
            self.network_boundary = NetworkBoundary(self)
            self.profile.setUrlRequestInterceptor(self.network_boundary)
            self.profile.setHttpUserAgent(self.profile.httpUserAgent() + " OBEOS-Earth/0.4")
            self.profile.setHttpCacheMaximumSize(128 * 1024 * 1024)
            self.profile.setPersistentCookiesPolicy(
                QWebEngineProfile.NoPersistentCookies
            )
            self.page = Page(self.profile, self.web)
            self.page.allowed_origin = origin
            self.web.setPage(self.page)
            self.page.settings().setAttribute(
                QWebEngineSettings.LocalContentCanAccessRemoteUrls, False
            )
            self.page.settings().setAttribute(
                QWebEngineSettings.PlaybackRequiresUserGesture, True
            )
            self.page.settings().setAttribute(
                QWebEngineSettings.JavascriptCanOpenWindows, False
            )
            self.channel = QWebChannel(self.page)
            self.bridge = Bridge(self)
            self.channel.registerObject("earthBridge", self.bridge)
            self.page.setWebChannel(self.channel)
            self.page.renderProcessTerminated.connect(
                lambda *_: self.failed(
                    "Earth renderer stopped. Use Reload Earth to recover the saved scene."
                )
            )
            self.page_load_started = time.perf_counter()
            self.web.loadStarted.connect(self.page_loading)
            self.web.loadFinished.connect(self.page_loaded)
            self.setCentralWidget(self.web)
            request = QWebEngineHttpRequest(QUrl(origin + "/"))
            request.setHeader(
                b"Authorization", ("Bearer " + self.runtime.token).encode()
            )
            self.web.load(request)
            if args.smoke_seconds:
                QTimer.singleShot(args.smoke_seconds * 1000, self.close)

        def page_loading(self):
            self.page_load_started = time.perf_counter()
            self.statusBar().showMessage("Loading Earth · local storage")

        def page_loaded(self, ok):
            elapsed = time.perf_counter() - self.page_load_started
            self.statusBar().showMessage(
                f"Earth ready · page load {elapsed:.2f}s · local storage"
                if ok else "Earth page failed to load"
            )

        def failed(self, message):
            self.statusBar().showMessage(message)
            self.details.setPlainText(message)
            self.drawer.show()

        def credentials(self):
            if not self.runtime:
                return
            from .credentials import CREDENTIALS
            name, ok = QInputDialog.getItem(self, "Optional free public sources", "Earth works without keys. Configure a free provider only if you want its layer.", list(CREDENTIALS), 0, False)
            if not ok:
                return
            secret, ok = QInputDialog.getText(self, CREDENTIALS[name], "Key (stored only in the operating system key store)", QLineEdit.Password)
            if ok and secret:
                try:
                    self.runtime.broker.set(name, secret)
                    self.statusBar().showMessage("Optional source configured. Enable its layer when public updates are allowed.")
                except Exception:
                    QMessageBox.warning(self, "Credential storage unavailable", "The protected OS key store is unavailable. Earth remains usable without this source.")

        def camera_location(self):
            if not self.runtime:
                return
            camera_id, ok = QInputDialog.getText(
                self, "3000 camera location", "Camera ID (matches 3000 camera_index)"
            )
            if not ok or not camera_id.strip():
                return
            coordinates, ok = QInputDialog.getText(
                self, "Camera location", "WGS84 latitude, longitude"
            )
            if not ok:
                return
            try:
                from earthglass.spatial.models import Position

                lat, lon = [float(value.strip()) for value in coordinates.split(",")]
                position = Position(latitude=lat, longitude=lon)
                path = self.runtime.camera_path
                cameras = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.exists()
                    else {}
                )
                if len(cameras) >= 50 and camera_id not in cameras:
                    raise ValueError("Camera limit reached")
                cameras[camera_id.strip()[:100]] = {
                    "latitude": position.latitude,
                    "longitude": position.longitude,
                    "label": "Camera " + camera_id.strip()[:100],
                }
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(cameras, indent=2), encoding="utf-8")
                temporary.replace(path)
                if self.runtime.backbone:
                    self.runtime.engine.configure_perception(
                        self.runtime.backbone.perception_events, cameras
                    )
                self.statusBar().showMessage(
                    "Camera location saved. Enable 3000 perception after configuring its event publisher."
                )
            except (ValueError, OSError):
                QMessageBox.warning(
                    self,
                    "Invalid camera location",
                    "Enter a latitude from −90 to 90 and longitude from −180 to 180.",
                )

        def _close_classic_log(self):
            if self.classic_log is not None:
                try:
                    self.classic_log.close()
                except OSError:
                    pass
                self.classic_log = None

        def workspace(self, name):
            if name == "Earth":
                self.drawer.hide()
                return
            if name == "System":
                self.details.setPlainText(
                    json.dumps(
                        {
                            "runtime_seconds": self.runtime.started_seconds
                            if self.runtime
                            else None,
                            "storage": str(self.runtime.data_dir)
                            if self.runtime
                            else None,
                            "network": self.runtime.engine.network_status() if self.runtime and self.runtime.engine else None,
                            "renderer": getattr(self.runtime.engine, "renderer_metrics", {}) if self.runtime and self.runtime.engine else {},
                            "classic_workspaces": "Available on demand",
                        },
                        indent=2,
                    )
                )
                self.drawer.show()
                return
            if self.classic_process is not None and self.classic_process.poll() is None:
                self.statusBar().showMessage(
                    "Classic Workspace is already running. Use its window or taskbar entry."
                )
                return

            # Preserve existing interfaces and user state while the Earth home is accepted.
            environment = dict(os.environ, OBEOS_START_WORKSPACE=name)
            log_path = (self.runtime.data_dir if self.runtime else root / "data" / "runtime") / "logs" / "classic-workspace.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._close_classic_log()
            self.classic_log = log_path.open("a", encoding="utf-8")
            command = [sys.executable, str(root / "desktop_shell.py"), "--classic"]
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(
                command,
                cwd=str(root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self.classic_log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self.classic_process = process
            self.classic_processes.append(process)
            QTimer.singleShot(1500, lambda: self._check_classic_workspace(process, name, log_path))

        def _check_classic_workspace(self, process, name, log_path):
            if process.poll() is None:
                self.statusBar().showMessage(f"{name} opened in the Classic Workspace")
                return
            if self.classic_log is not None and not self.classic_log.closed:
                try:
                    self.classic_log.flush()
                except ValueError:  # Closed between the timer firing and this call.
                    pass
            self.statusBar().showMessage(
                f"Classic Workspace failed to start (exit {process.returncode}). See {log_path}"
            )
            self.details.setPlainText(
                "Classic Workspace could not start.\n\n"
                f"Command: {sys.executable} {root / 'desktop_shell.py'} --classic\n"
                f"Log: {log_path}\n\n"
                "Use Reload Earth and try Classic Workspace again after reviewing the log."
            )
            self.drawer.show()

        def closeEvent(self, event):
            if not self.isMaximized():
                self.settings.setValue("window/geometry", self.saveGeometry())
            if self.stopped:
                event.accept()
                return
            event.ignore()
            if self.closing:
                return
            self.closing = True
            self.statusBar().showMessage(
                "Stopping providers and releasing local resources…"
            )

            def stop():
                self.start_job.wait()
                if hasattr(self, "bridge"):
                    for job in list(self.bridge.jobs):
                        job.wait()
                if self.runtime:
                    self.runtime.stop()

            self.stop_job = Job(stop)
            self.stop_job.finished.connect(lambda: self.finish_close(None))
            self.stop_job.failed.connect(self.failed)
            self.stop_job.start()
            self._close_classic_log()

        def finish_close(self, _):
            self.stopped = True
            self.close()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("OBEOS Earth")
    app.setStyleSheet(
        "QMainWindow,QWidget { background:#11161c; color:#d8e1eb; } QToolBar { spacing:8px; border:0; padding:5px; } QToolButton { padding:7px 11px; border-radius:5px; } QToolButton:hover { background:#25323c; } QStatusBar { color:#8799a8; }"
    )
    window = Home()
    window.showMaximized() if args.maximized else window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
