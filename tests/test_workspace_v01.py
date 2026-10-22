from __future__ import annotations

import subprocess
import time

import httpx
import pytest

from blackfong_ecosystem.startup_coordinator import StartupCoordinator
from blackfong_ecosystem.terminal_launcher import open_terminal, resolve_terminal_command
from blackfong_ecosystem.workspace_health import (
    HealthTile,
    HttpCheck,
    apply_staleness,
    probe_http,
    tile_from_health_payload,
    tile_from_subsystem,
    tiles_from_startup,
)

CHECK = HttpCheck(key="event_api", label="Event API", url="http://127.0.0.1:8000/health")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- health mapping -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ready", "online"),
        ("active", "online"),
        ("degraded", "degraded"),
        ("offline", "offline"),
        ("missing", "offline"),
        ("manual_only", "offline"),
        ("booting", "unknown"),
        ("warming", "unknown"),
        ("", "unknown"),
        ("something-new", "unknown"),
    ],
)
def test_subsystem_states_never_overstate_health(raw: str, expected: str) -> None:
    tile = tile_from_subsystem("x", {"label": "X", "state": raw, "updated_at": 100.0})
    assert tile.state == expected


def test_healthy_payload_is_online_and_timestamped() -> None:
    tile = tile_from_health_payload(CHECK, {"status": "healthy", "service": "control_plane"}, now=500.0)
    assert (tile.state, tile.checked_at) == ("online", 500.0)


@pytest.mark.parametrize("payload", [None, [], "ok", 7, {"status": "banana"}, {}])
def test_malformed_payload_is_never_online(payload) -> None:
    assert tile_from_health_payload(CHECK, payload, now=1.0).state == "unknown"


def test_probe_ok_response() -> None:
    tile = probe_http(CHECK, client=_client(lambda r: httpx.Response(200, json={"status": "healthy"})), now=10.0)
    assert tile.state == "online"


def test_probe_server_error_is_degraded() -> None:
    assert probe_http(CHECK, client=_client(lambda r: httpx.Response(503)), now=10.0).state == "degraded"


def test_probe_non_json_is_unknown() -> None:
    assert probe_http(CHECK, client=_client(lambda r: httpx.Response(200, text="<html>")), now=10.0).state == "unknown"


def test_probe_connection_refused_is_offline() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    tile = probe_http(CHECK, client=_client(refuse), now=10.0)
    assert (tile.state, tile.checked_at) == ("offline", 10.0)


def test_probe_timeout_is_unknown_not_online() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    assert probe_http(CHECK, client=_client(slow), now=10.0).state == "unknown"


def test_stale_online_tile_is_downgraded() -> None:
    tile = HealthTile("a", "A", "online", checked_at=100.0)
    assert apply_staleness(tile, now=100.0 + 61, max_age=60).state == "stale"
    assert apply_staleness(tile, now=100.0 + 30, max_age=60).state == "online"


def test_untimed_online_tile_is_stale() -> None:
    assert apply_staleness(HealthTile("a", "A", "online", checked_at=None), now=1.0).state == "stale"


def test_offline_tile_is_not_rewritten_by_staleness() -> None:
    tile = HealthTile("a", "A", "offline", checked_at=1.0)
    assert apply_staleness(tile, now=10_000.0).state == "offline"


def test_every_tile_from_real_coordinator_has_state_and_timestamp() -> None:
    snapshot = StartupCoordinator().snapshot()
    tiles = tiles_from_startup(snapshot, now=time.time())
    assert tiles
    for tile in tiles:
        assert tile.state in {"online", "degraded", "offline", "unknown", "stale"}
        assert tile.checked_at is not None


def test_booting_coordinator_reports_nothing_as_online() -> None:
    snapshot = StartupCoordinator().snapshot()
    states = {tile.key: tile.state for tile in tiles_from_startup(snapshot, now=time.time())}
    assert states["backend_model"] == "unknown"
    assert states["daise"] == "unknown"


def test_degraded_subsystem_is_visible_even_when_coordinator_is_ready() -> None:
    coordinator = StartupCoordinator()
    for key in list(coordinator.snapshot()["subsystems"]):
        coordinator.set_subsystem(key, state="ready", summary="ok")
    coordinator.set_subsystem("backend_model", state="degraded", summary="down")
    snapshot = coordinator.snapshot()
    assert snapshot["overall_state"] == "ready" and snapshot["ready_but_degraded"] is True
    states = {tile.key: tile.state for tile in tiles_from_startup(snapshot, now=time.time())}
    assert states["backend_model"] == "degraded"


# --- terminal launcher ----------------------------------------------------------------------


def _which_wt(name: str):
    return r"C:\wt.exe" if name in {"wt.exe", "wt"} else None


def test_launcher_uses_argument_list_without_shell(tmp_path) -> None:
    calls = []
    result = open_terminal(
        str(tmp_path), which=_which_wt, popen=lambda argv, **kw: calls.append((argv, kw)), platform="win32"
    )
    assert result["success"] is True
    argv, kwargs = calls[0]
    assert isinstance(argv, list) and "shell" not in kwargs
    assert argv[0] == r"C:\wt.exe"
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_launcher_is_detached() -> None:
    calls = []
    open_terminal(None, which=_which_wt, popen=lambda argv, **kw: calls.append(kw), platform="win32")
    assert calls[0]["creationflags"] & 0x00000008  # DETACHED_PROCESS


def test_launcher_directory_is_never_interpolated_into_a_command_string(tmp_path) -> None:
    hostile = tmp_path / "a & calc.exe ; rm -rf"
    hostile.mkdir()
    calls = []
    open_terminal(str(hostile), which=_which_wt, popen=lambda argv, **kw: calls.append(argv), platform="win32")
    argv = calls[0]
    assert str(hostile) in argv  # passed as one inert element
    assert all(isinstance(part, str) for part in argv)


def test_launcher_rejects_missing_directory() -> None:
    result = open_terminal("/definitely/not/here", which=_which_wt, popen=lambda *a, **k: None, platform="win32")
    assert result["success"] is False


def test_launcher_reports_missing_terminal() -> None:
    result = open_terminal(None, which=lambda name: None, popen=lambda *a, **k: None, platform="win32")
    assert result["success"] is False and "PATH" in result["error"]


def test_launcher_refuses_off_windows() -> None:
    assert open_terminal(None, which=_which_wt, popen=lambda *a, **k: None, platform="linux")["success"] is False


def test_launcher_survives_spawn_failure() -> None:
    def boom(*args, **kwargs):
        raise OSError("denied")

    assert open_terminal(None, which=_which_wt, popen=boom, platform="win32")["success"] is False


def test_powershell_fallback_when_wt_missing() -> None:
    which = lambda name: r"C:\pwsh.exe" if name == "pwsh.exe" else None
    assert resolve_terminal_command(None, which=which) == [r"C:\pwsh.exe", "-NoLogo", "-NoExit"]


# --- Qt smoke tests (offscreen) -------------------------------------------------------------

import os
import sys

from blackfong_ecosystem.workspace_health import default_http_checks, format_tile_line


def test_format_line_always_shows_state_and_time() -> None:
    online = format_tile_line(HealthTile("a", "Alpha", "online", checked_at=100.0), now=105.0)
    never = format_tile_line(HealthTile("b", "Beta", "unknown", checked_at=None), now=105.0)
    assert "[ONLINE" in online and "Alpha" in online and "5s ago" in online
    assert "[UNKNOWN" in never and "never checked" in never


def test_default_checks_only_include_confirmed_health_routes(monkeypatch) -> None:
    monkeypatch.delenv("BLACKFONG_EVENT_API_URL", raising=False)
    checks = default_http_checks()
    assert [c.key for c in checks] == ["event_api"]
    assert checks[0].url == "http://127.0.0.1:8000/health"


@pytest.fixture(scope="module")
def window():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    desktop_shell = pytest.importorskip("desktop_shell")
    app = desktop_shell.QApplication.instance() or desktop_shell.QApplication(["test", "-platform", "offscreen"])
    desktop_shell.apply_workspace_theme(app)
    win = desktop_shell.MainWindow(start_timer=False, initial_refresh=False)
    yield win
    win.close()


def test_fullscreen_is_default_and_opt_out_works() -> None:
    import desktop_shell

    assert desktop_shell.want_fullscreen([], {}) is True
    assert desktop_shell.want_fullscreen(["--windowed"], {}) is False
    assert desktop_shell.want_fullscreen([], {"BLACKFONG_WINDOWED": "1"}) is False


def test_window_has_health_panel_and_fullscreen_toggle(window) -> None:
    from PySide6.QtWidgets import QApplication

    assert window.health_dock.windowTitle() == "Service Health"
    window.show()
    QApplication.processEvents()
    window.toggle_fullscreen()
    QApplication.processEvents()
    assert window.isFullScreen()
    window.toggle_fullscreen()
    QApplication.processEvents()
    assert not window.isFullScreen()


def test_health_pane_renders_each_tile_with_state_and_time(window) -> None:
    window.health_pane.apply_tiles(
        [
            {"key": "a", "label": "Alpha", "state": "online", "checked_at": time.time(), "detail": ""},
            {"key": "b", "label": "Beta", "state": "offline", "checked_at": time.time(), "detail": "refused"},
            {"key": "bad", "label": "Bad", "state": "healthy-ish", "checked_at": None},
        ]
    )
    text = window.health_pane.output.toPlainText()
    assert "[ONLINE" in text and "[OFFLINE" in text
    assert "Bad" not in text  # an unsupported state is dropped, never shown as healthy


def test_snapshot_reports_offline_event_api_when_nothing_listens(window, monkeypatch) -> None:
    import desktop_shell

    monkeypatch.setenv("BLACKFONG_EVENT_API_URL", "http://127.0.0.1:9")  # discard port: refused
    snapshot = desktop_shell.build_workspace_snapshot(window.services, lightweight=True)
    states = {tile["key"]: tile["state"] for tile in snapshot["health_tiles"]}
    assert states["event_api"] in {"offline", "unknown"}
    assert states["event_api"] != "online"


def test_closing_the_window_leaves_unrelated_processes_running() -> None:
    import desktop_shell

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        win = desktop_shell.MainWindow(start_timer=False, initial_refresh=False)
        win.close()
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()
