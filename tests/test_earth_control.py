from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
import pytest
from earthglass.spatial.engine import SpatialEngine
from earthglass.spatial.models import Observation
from blackfong_ecosystem.earth.control import EarthControl
from blackfong_ecosystem.earth.contracts import COMMAND_ARGUMENTS
from blackfong_ecosystem.earth.credentials import CredentialBroker
from blackfong_ecosystem.earth.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def earth(tmp_path):
    engine = SpatialEngine(tmp_path / "data")
    now = datetime.now(timezone.utc)
    engine.history.ingest(
        [
            Observation(
                entity_id="test:plane",
                layer="aircraft",
                entity_type="aircraft",
                position={"latitude": 40.0, "longitude": -74.0, "altitude_m": 1000},
                observed_at=now,
                received_at=now,
                properties={"name": "Example flight"},
                provenance={
                    "provider": "fixture",
                    "source_id": "plane",
                    "source_url": "https://example.org/",
                    "attribution": "Fixture",
                },
            )
        ]
    )
    control = EarthControl(engine, tmp_path / "scene.sqlite3")
    yield engine, control
    control.close()
    engine.stop()


def payload(name, args=None, ident=None):
    return {
        "schema_version": "1.0",
        "scene_id": "home",
        "request_id": ident or str(uuid.uuid4()),
        "command": name,
        "arguments": args or {},
    }


CASES = {
    "earth.watch.add": {
        "name": "Test area",
        "west": -75,
        "south": 39,
        "east": -73,
        "north": 41,
    },
    "earth.watch.remove": {"name": "Test area"},
    "earth.watches.list": {},
    "earth.goto": {"latitude": 40, "longitude": -74},
    "earth.map.set": {"mode": "streets"},
    "earth.imagery.set": {"product": "true_color", "enabled": True, "opacity": 0.5},
    "earth.network.set": {"mode": "offline"},
    "earth.search": {"query": "Example"},
    "earth.layers.list": {},
    "earth.layer.enable": {"layer": "aircraft"},
    "earth.layer.disable": {"layer": "aircraft"},
    "earth.entities.nearby": {"latitude": 40, "longitude": -74},
    "earth.entity.inspect": {"entity_id": "test:plane"},
    "earth.entity.track": {"entity_id": "test:plane"},
    "earth.entity.untrack": {},
    "earth.camera.follow": {"entity_id": "test:plane"},
    "earth.camera.orbit": {"entity_id": "test:plane"},
    "earth.camera.overhead": {"entity_id": "test:plane"},
    "earth.camera.reset": {},
    "earth.scene.describe": {},
    "earth.scene.snapshot": {"label": "Saved fixture"},
    "earth.annotation.add": {
        "annotation_id": "one",
        "label": "Area",
        "geometry": "polygon",
        "coordinates": [[-74, 40], [-73, 40], [-73, 41]],
    },
    "earth.annotation.remove": {"annotation_id": "one"},
    "earth.timeline.set": {"at": "2026-09-10T10:00:00Z"},
    "earth.timeline.play": {"playing": False},
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_command_has_successful_contract(earth, name):
    _, control = earth
    result = control.execute(payload(name, CASES[name]))
    assert result["ok"], result
    assert result["schema_version"] == "1.0"


def test_command_coverage_is_complete():
    assert set(CASES) | {"earth.scene.restore"} == set(COMMAND_ARGUMENTS)


def test_idempotency_and_invalid_commands(earth):
    _, control = earth
    original = payload("earth.annotation.add", CASES["earth.annotation.add"], "same")
    first = control.execute(original)
    assert control.execute(original) == first
    assert len(control.state["annotations"]) == 1
    assert (
        control.execute(payload("earth.camera.reset", ident="same"))["error"]["code"]
        == "request_id_conflict"
    )
    assert not control.execute(
        payload("earth.goto", {"latitude": 100, "longitude": 0})
    )["ok"]
    assert not control.execute(
        payload("earth.goto", {"latitude": 0, "longitude": 0, "javascript": "alert(1)"})
    )["ok"]
    assert not control.execute(payload("earth.eval", {"code": "alert(1)"}))["ok"]
    assert not control.execute(payload("earth.camera.reset"), origin="remote")["ok"]


def test_policy_denial_prevents_scene_mutation(earth):
    _, control = earth
    control.policy = SimpleNamespace(
        evaluate=lambda *a: SimpleNamespace(approved=False, reasons=["Denied"])
    )
    before = control.snapshot()
    assert (
        control.execute(payload("earth.camera.reset"))["error"]["code"]
        == "policy_denied"
    )
    assert control.snapshot() == before


def test_saved_investigation_survives_history_retention(earth):
    engine, control = earth
    saved = control.execute(payload("earth.scene.snapshot"))["result"][
        "investigation_id"
    ]
    engine.history.prune(now=datetime.now(timezone.utc).timestamp() + 86400 * 10)
    assert not engine.query()["observations"]
    assert control.investigation(saved)["observations"]["observations"]
    assert control.execute(payload("earth.scene.restore", {"investigation_id": saved}))[
        "ok"
    ]
    assert control.execute(
        payload("earth.entity.inspect", {"entity_id": "test:plane"})
    )["ok"]


def test_credentials_are_references_only(tmp_path):
    secrets = {}
    backend = SimpleNamespace(
        set_password=lambda service, key, value: secrets.update({key: value}),
        get_password=lambda service, key: secrets.get(key),
    )
    broker = CredentialBroker(tmp_path / "references.json", backend)
    broker.set("firms_map_key", "test-private-value")
    assert "test-private-value" not in broker.path.read_text()
    assert broker.get("firms_map_key") == "test-private-value"


def test_http_session_and_origin(earth, tmp_path):
    engine, control = earth
    broker = CredentialBroker(tmp_path / "keys.json")
    app = create_app(
        engine,
        control,
        broker,
        tmp_path / "assets",
        tmp_path,
        "test-token",
        origin="http://127.0.0.1:8765",
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/scene").status_code == 401
        assert (
            client.get(
                "/api/scene", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/control",
                json=payload("earth.camera.reset"),
                headers={"Origin": "https://example.org"},
            ).status_code
            == 403
        )
        response = client.post(
            "/api/control",
            json=payload("earth.camera.reset"),
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert response.status_code == 200 and response.json()["ok"]


def test_late_replay_advance_does_not_undo_pause(earth):
    _, control = earth
    control.execute(payload("earth.timeline.set", {"at": "2026-09-10T10:00:00Z"}))
    control.execute(payload("earth.timeline.play", {"playing": True}))
    control.execute(payload("earth.timeline.play", {"playing": False}))
    result = control.execute(
        payload("earth.timeline.set", {"at": "2026-09-10T11:00:00Z", "advance": True})
    )
    assert result["ok"]
    assert control.state["timeline"]["at"].startswith("2026-09-10T10:")
    assert not control.state["timeline"]["playing"]


def test_resaving_an_archive_does_not_pull_live_history(earth):
    engine, control = earth
    first = control.execute(payload("earth.scene.snapshot"))["result"][
        "investigation_id"
    ]
    engine.history.prune(now=datetime.now(timezone.utc).timestamp() + 86400 * 10)
    control.execute(payload("earth.scene.restore", {"investigation_id": first}))
    second = control.execute(payload("earth.scene.snapshot"))["result"][
        "investigation_id"
    ]
    assert (
        control.investigation(second)["observations"]["observations"]
        == control.investigation(first)["observations"]["observations"]
    )


def test_runtime_lease_is_exclusive_and_recoverable(tmp_path):
    from blackfong_ecosystem.earth.lease import RuntimeLease

    owner = RuntimeLease(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="already open"):
            RuntimeLease(tmp_path)
    finally:
        owner.close()
    RuntimeLease(tmp_path).close()


def test_browser_ticket_is_single_use(earth, tmp_path):
    import time

    engine, control = earth
    tickets = {"one-use": time.monotonic() + 60}
    app = create_app(
        engine,
        control,
        CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets",
        tmp_path,
        "main-token",
        origin="http://127.0.0.1:8765",
        browser_tickets=tickets,
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert (
            client.post(
                "/api/session", headers={"Authorization": "Bearer one-use"}
            ).status_code
            == 200
        )
        assert client.get("/api/scene").status_code == 200
        client.cookies.clear()
        assert (
            client.post(
                "/api/session", headers={"Authorization": "Bearer one-use"}
            ).status_code
            == 401
        )


def test_transient_traffic_can_be_inspected_but_not_saved(earth):
    import time

    engine, control = earth
    item = engine.query()["observations"][0]
    item.update(entity_id="traffic:transient", layer="traffic")
    item["properties"]["retention_seconds"] = 120
    engine._volatile[item["entity_id"]] = (time.monotonic() + 120, item)
    result = control.execute(
        payload("earth.entity.inspect", {"entity_id": item["entity_id"]})
    )
    assert result["ok"]
    saved = control.execute(payload("earth.scene.snapshot"))["result"][
        "investigation_id"
    ]
    assert all(
        o["entity_id"] != item["entity_id"]
        for o in control.investigation(saved)["observations"]["observations"]
    )


def test_renderer_metrics_contract_accepts_adaptive_measurements(earth, tmp_path):
    engine, control = earth
    app = create_app(
        engine,
        control,
        CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets",
        tmp_path,
        "token",
        origin="http://127.0.0.1:8765",
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        result = client.post(
            "/api/render-metrics",
            headers={"Authorization": "Bearer token"},
            json={
                "canvasWidth": 960,
                "canvasHeight": 540,
                "detailBudget": 200,
                "frameP50Ms": 34,
                "frameP95Ms": 50,
                "targetFPS": 30,
                "knownTracks": 10000,
                "renderedTracks": 200,
                "updateMs": 4,
                "resolution": 0.5,
            },
        )
        assert result.status_code == 200
        assert engine.renderer_metrics["knownTracks"] == 10000


def test_local_explanation_cancellation_suppresses_late_result(earth):
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    _, control = earth

    def generate(*args, **kwargs):
        orchestration.stop()
        return SimpleNamespace(text="late", model="fixture", provider="local")

    orchestration = EarthOrchestration(
        control, lambda: SimpleNamespace(generate=generate)
    )
    assert orchestration.explain("test")["status"] == "unavailable"


def test_blackfong_remediation_grant_is_bound_and_single_use():
    from blackfong_ecosystem.approval_grants import ApprovalGrants

    policy = SimpleNamespace(evaluate=lambda *args: SimpleNamespace(approved=True))
    authority = ApprovalGrants(policy)
    with pytest.raises(PermissionError):
        authority.issue("repair", {})
    token = authority.issue("repair", {"path": "fixture"}, operator_confirmed=True)
    assert not authority.consume("repair", {"path": "different"}, "Bearer " + token)
    token = authority.issue("repair", {"path": "fixture"}, operator_confirmed=True)
    assert authority.consume("repair", {"path": "fixture"}, "Bearer " + token)
    assert not authority.consume("repair", {"path": "fixture"}, "Bearer " + token)


def test_basemap_preference_is_validated_and_restored(earth, tmp_path):
    engine, control = earth
    assert control.execute(payload("earth.map.set", {"mode": "satellite"}))["ok"]
    assert not control.execute(
        payload("earth.map.set", {"mode": "https://example.org/tiles"})
    )["ok"]
    reopened = EarthControl(engine, tmp_path / "scene.sqlite3")
    try:
        assert reopened.snapshot()["basemap"] == "satellite"
    finally:
        reopened.close()


def test_camera_commands_repeat_but_inspection_does_not_move_camera(earth):
    _, control = earth
    first = control.execute(payload("earth.camera.reset"))["scene"]["camera_revision"]
    inspected = control.execute(
        payload("earth.entity.inspect", {"entity_id": "test:plane"})
    )["scene"]
    assert inspected["camera_revision"] == first
    second = control.execute(payload("earth.camera.reset"))["scene"]["camera_revision"]
    assert second == first + 1


def test_worldwide_city_search_works_without_external_geocoding(earth):
    _, control = earth
    result = control.execute(payload("earth.search", {"query": "Seattle"}))
    assert result["ok"]
    assert any("Seattle" in row["label"] for row in result["result"]["matches"])


def test_offline_http_camera_and_browser_policy(earth, tmp_path):
    engine, control = earth
    engine.set_offline(True)

    class NoNetwork:
        def bytes(self, *_args, **_kwargs):
            raise AssertionError("Offline camera must not use public transport")

    original_http = engine.http
    engine.http = NoNetwork()
    try:
        app = create_app(
            engine,
            control,
            None,
            tmp_path / "assets",
            tmp_path,
            "token",
            origin="http://127.0.0.1:8765",
        )
        with TestClient(
            app,
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": "Bearer token"},
        ) as client:
            response = client.get("/api/camera-image?entity_id=any")
            assert response.status_code == 503
            assert "offline" in response.json()["detail"]
            assert "arcgisonline" not in response.headers["content-security-policy"]
            catalog = client.get("/api/v2/catalog").json()
            assert catalog["offline"]
            assert catalog["network"]["access"] == "public_with_optional_free_keys"
            assert all(
                not p["enabled"] for p in catalog["providers"] if p["credential"]
            )
            assert "credentials" not in client.get("/api/diagnostics").json()
    finally:
        engine.http = original_http


@pytest.mark.parametrize(
    "url",
    [
        "https://cloud.example.org",
        "http://192.168.1.2:11434",
        "http://user:pass@localhost:11434",
        "http://localhost:11434?key=x",
    ],
)
def test_earth_model_cannot_route_evidence_off_machine(url):
    from blackfong_ecosystem.earth.daise import local_model_url

    with pytest.raises(ValueError):
        local_model_url(url)


def test_earth_model_uses_localhost_without_dns():
    from blackfong_ecosystem.earth.daise import local_model_url

    assert local_model_url("http://localhost:11434/") == "http://127.0.0.1:11434"
    assert local_model_url("http://[::1]:11434") == "http://[::1]:11434"


def test_offline_policy_survives_saved_scene_restore(earth):
    engine, control = earth
    saved = control.execute(payload("earth.scene.snapshot"))["result"][
        "investigation_id"
    ]
    assert control.execute(payload("earth.network.set", {"mode": "offline"}))["ok"]
    assert control.execute(payload("earth.scene.restore", {"investigation_id": saved}))[
        "ok"
    ]
    assert engine.offline
    assert control.snapshot()["network_mode"] == "offline"


def test_offline_explanation_uses_on_machine_model_without_proxy(earth, monkeypatch):
    import httpx
    import blackfong_ecosystem.config as config
    import blackfong_ecosystem.chat_backends as backends
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    engine, control = earth
    engine.set_offline(True)
    original_client = httpx.Client
    calls = []

    def response(request):
        assert request.url.host == "127.0.0.1"
        return httpx.Response(
            200,
            json={
                "message": {"content": "Stored scene summary"},
                "model": "fixture-local",
                "done": True,
            },
        )

    def client(*args, **kwargs):
        calls.append(kwargs)
        return original_client(*args, **kwargs, transport=httpx.MockTransport(response))

    monkeypatch.setattr(
        config,
        "chat_settings",
        SimpleNamespace(
            ollama_url="http://localhost:11434",
            model_for_agent=lambda _: "fixture-local",
            earth_explanation_model="fixture-local",
            earth_explanation_timeout=120.0,
            earth_explanation_num_predict=300,
            earth_evidence_chars=6000,
        ),
    )
    monkeypatch.setattr(backends.httpx, "Client", client)
    orchestration = EarthOrchestration(control)
    try:
        result = orchestration.explain("Explain this stored scene")
        assert result["status"] == "ok", result
        assert result["explanation"] == "Stored scene summary"
        assert calls[0]["trust_env"] is False
    finally:
        orchestration.stop()


def test_individual_provider_choice_survives_restart(tmp_path):
    engine = SpatialEngine(tmp_path / "data")
    control = EarthControl(engine, tmp_path / "scene.sqlite3")
    try:
        assert control.execute(
            payload("earth.layer.enable", {"layer": "celestrak_weather"})
        )["ok"]
        control.close()
        reopened = EarthControl(engine, tmp_path / "scene.sqlite3")
        assert "celestrak_weather" in reopened.snapshot()["enabled_providers"]
        assert "celestrak_starlink" not in reopened.snapshot()["enabled_providers"]
        reopened.close()
    finally:
        engine.stop()


def test_private_positions_do_not_enter_public_earth_projections(earth, tmp_path):
    from earthglass.presence.models import (
        DeviceRegistration,
        PresenceSettings,
        Position,
    )

    engine, control = earth
    app = create_app(
        engine,
        control,
        None,
        tmp_path / "assets",
        tmp_path / "host",
        "token",
        origin="http://127.0.0.1:8765",
    )
    private = app.state.presence
    private.register(
        DeviceRegistration(
            device_id="private-fixture",
            label="Private fixture",
            traccar_id="99",
            tracking_enabled=True,
        )
    )
    private.configure(PresenceSettings(enabled=True))
    private.ingest(
        "private-fixture",
        Position(
            latitude=22.3456789,
            longitude=44.9876543,
            recorded_at=datetime.now(timezone.utc),
        ),
    )
    with TestClient(
        app, base_url="http://127.0.0.1:8765", headers={"Authorization": "Bearer token"}
    ) as client:
        assert (
            client.get("/private/v2/presence").json()["data"][0]["position"]["latitude"]
            == 22.3456789
        )
        for path in (
            "/api/v2/observations",
            "/api/v2/mobility/traffic",
            "/api/scene",
            "/api/diagnostics",
        ):
            response = client.get(path)
            assert response.status_code == 200
            assert (
                "22.3456789" not in response.text
                and "private-fixture" not in response.text
            )
        assert not any(
            "private-fixture" in str(change)
            for change in engine.history.pending_publications()
        )


def test_transit_import_persists_enabled_providers_and_detail_routes(earth, tmp_path):
    import io, zipfile

    engine, control = earth
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("routes.txt", "route_id,route_short_name,route_type\nR,R1,3\n")
        zipped.writestr(
            "stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nA,Fixture,40.5,-74\n"
        )
    app = create_app(
        engine,
        control,
        CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets",
        tmp_path,
        "fixture-token",
        origin="http://127.0.0.1:8765",
    )
    headers = {
        "Authorization": "Bearer fixture-token",
        "Origin": "http://127.0.0.1:8765",
    }
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=headers) as client:
        response = client.post(
            "/api/v2/mobility/transit/import",
            params={
                "agency": "fixture",
                "source_url": "https://example.org",
                "attribution": "Fixture",
            },
            content=archive.getvalue(),
        )
        assert response.status_code == 200, response.text
        assert {"local_gtfs", "local_gtfs_stops"} <= set(
            control.snapshot()["enabled_providers"]
        )
        assert client.get("/api/v2/mobility/cameras/status").status_code == 200
        assert client.get("/api/v2/mobility/cameras/missing:1").status_code == 404
        assert client.get("/api/v2/mobility/incidents/missing:1").status_code == 404
        assert (
            client.get("/api/v2/seismic/events?minimum_magnitude=nan").status_code
            == 422
        )
        assert client.get("/private/v2/presence/settings").json()["role"] == "owner"
    control.close()
    restored = EarthControl(engine, tmp_path / "scene.sqlite3")
    try:
        assert {"local_gtfs", "local_gtfs_stops"} <= set(
            restored.snapshot()["enabled_providers"]
        )
    finally:
        restored.close()


def _ingest(engine, entity_id, latitude, longitude):
    now = datetime.now(timezone.utc)
    engine.history.ingest(
        [
            Observation(
                entity_id=entity_id,
                layer="aircraft",
                entity_type="aircraft",
                position={
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude_m": 1000,
                },
                observed_at=now,
                received_at=now,
                properties={"name": entity_id},
                provenance={
                    "provider": "fixture",
                    "source_id": "s",
                    "source_url": "https://example.org/",
                    "attribution": "Fixture",
                },
            )
        ]
    )


def test_nearby_bounds_never_exclude_a_point_inside_the_radius():
    import math
    import random

    from blackfong_ecosystem.earth.control import distance_m, radius_bounds

    random.seed(11)
    for _ in range(4000):
        latitude = random.uniform(-89.9, 89.9)
        longitude = random.uniform(-180, 180)
        radius = random.choice([100, 5000, 50000, 500000, 2000000])
        box = radius_bounds(latitude, longitude, radius)
        if box is None:  # Pole-spanning circles fall back to an unbounded scan.
            continue
        bearing, angular = random.uniform(0, 2 * math.pi), random.uniform(0, radius) / 6371000
        origin_lat, origin_lon = math.radians(latitude), math.radians(longitude)
        point_lat = math.asin(
            math.sin(origin_lat) * math.cos(angular)
            + math.cos(origin_lat) * math.sin(angular) * math.cos(bearing)
        )
        point_lon = origin_lon + math.atan2(
            math.sin(bearing) * math.sin(angular) * math.cos(origin_lat),
            math.cos(angular) - math.sin(origin_lat) * math.sin(point_lat),
        )
        latitude_2 = math.degrees(point_lat)
        longitude_2 = (math.degrees(point_lon) + 540) % 360 - 180
        if distance_m(latitude, longitude, latitude_2, longitude_2) > radius:
            continue
        assert box["south"] <= latitude_2 <= box["north"]
        west, east = box["west"], box["east"]
        assert (
            west <= longitude_2 <= east
            if west <= east
            else longitude_2 >= west or longitude_2 <= east
        )


def test_nearby_matches_across_the_antimeridian(earth):
    engine, control = earth
    _ingest(engine, "test:east", 0.0, 179.98)
    _ingest(engine, "test:west", 0.0, -179.98)
    result = control.execute(
        payload(
            "earth.entities.nearby",
            {"latitude": 0.0, "longitude": 179.99, "radius_m": 50000},
        )
    )
    assert result["ok"], result
    found = {match["observation"]["entity_id"] for match in result["result"]}
    assert {"test:east", "test:west"} <= found


def test_nearby_still_matches_when_the_radius_spans_a_pole(earth):
    engine, control = earth
    _ingest(engine, "test:polar", 89.97, 140.0)
    result = control.execute(
        payload(
            "earth.entities.nearby",
            {"latitude": 89.99, "longitude": -40.0, "radius_m": 60000},
        )
    )
    assert result["ok"], result
    assert "test:polar" in {
        match["observation"]["entity_id"] for match in result["result"]
    }


def test_nearby_does_not_scan_history_outside_the_radius(earth):
    engine, control = earth
    for index in range(600):
        _ingest(engine, f"test:far{index}", -80 + index * 0.25, -180 + index * 0.6)
    _ingest(engine, "test:close", 40.01, -74.0)
    scanned = []
    original = engine.query

    def counting_query(**kwargs):
        page = original(**kwargs)
        scanned.append(len(page.get("observations", [])))
        return page

    engine.query = counting_query
    try:
        result = control.execute(
            payload(
                "earth.entities.nearby",
                {"latitude": 40.0, "longitude": -74.0, "radius_m": 50000},
            )
        )
    finally:
        engine.query = original
    assert result["ok"], result
    assert "test:close" in {
        match["observation"]["entity_id"] for match in result["result"]
    }
    # The bounded query must not drag the whole local history through Python.
    assert sum(scanned) < 50, sum(scanned)


def test_explanation_does_not_blame_the_model_for_an_evidence_failure(earth):
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    _, control = earth

    def broken_scene():
        raise RuntimeError("history unavailable")

    control.describe_scene = broken_scene
    orchestration = EarthOrchestration(
        control, backend_factory=lambda: pytest.fail("model must not be called")
    )
    result = orchestration.explain("What is here?")
    assert result["status"] == "unavailable"
    assert "model" not in result["message"]
    assert result["reason"] == "RuntimeError"


def test_unavailable_model_still_returns_the_evidence_summary(earth):
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    _, control = earth

    def refuse():
        raise ConnectionRefusedError("no local model")

    orchestration = EarthOrchestration(control, backend_factory=refuse)
    result = orchestration.explain("What is here?")
    assert result["status"] == "unavailable"
    assert result["reason"] == "ConnectionRefusedError"
    # The message promises the evidence summary, so the payload must carry it.
    assert result["evidence"]["schema_version"] == "1.0"


def test_search_does_not_walk_the_whole_history(earth):
    engine, control = earth
    for index in range(300):
        _ingest(engine, f"test:bulk{index}", -80 + index * 0.5, -180 + index * 1.2)
    calls = []
    original = engine.query

    def counting_query(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    engine.query = counting_query
    try:
        result = control.execute(payload("earth.search", {"query": "zzzznomatch"}))
    finally:
        engine.query = original
    assert result["ok"], result
    # An indexed search must not fall back to paging every stored observation.
    assert calls == []


def test_search_still_finds_transient_contacts(earth):
    import time

    engine, control = earth
    item = dict(engine.query()["observations"][0])
    item["entity_id"] = "traffic:transient"
    item["layer"] = "traffic"
    item["properties"] = {"name": "Transient camera"}
    engine._volatile[item["entity_id"]] = (time.monotonic() + 120, item)
    result = control.execute(payload("earth.search", {"query": "transient camera"}))
    assert result["ok"], result
    assert "traffic:transient" in {
        match.get("entity_id") for match in result["result"]["matches"]
    }


def test_saved_investigation_search_stays_inside_the_archive(earth):
    engine, control = earth
    saved = control.execute(payload("earth.scene.snapshot", {"label": "Archive"}))[
        "result"
    ]["investigation_id"]
    _ingest(engine, "test:added-after-save", 10.0, 10.0)
    assert control.execute(
        payload("earth.scene.restore", {"investigation_id": saved})
    )["ok"]
    result = control.execute(payload("earth.search", {"query": "added-after-save"}))
    assert result["ok"], result
    assert "test:added-after-save" not in {
        match.get("entity_id") for match in result["result"]["matches"]
    }
    archived = control.execute(payload("earth.search", {"query": "Example flight"}))
    assert "test:plane" in {
        match.get("entity_id") for match in archived["result"]["matches"]
    }


def test_annotation_geojson_round_trip(earth, tmp_path):
    engine, control = earth
    app = create_app(
        engine,
        control,
        CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets",
        tmp_path,
        "test-token",
        origin="http://127.0.0.1:8765",
    )
    auth = {"Authorization": "Bearer test-token"}
    for annotation in (
        {
            "annotation_id": "pin",
            "label": "A pin",
            "geometry": "point",
            "coordinates": [[-74, 40]],
        },
        {
            "annotation_id": "route",
            "label": "A route",
            "geometry": "line",
            "coordinates": [[-74, 40], [-73, 41]],
            "color": "cyan",
        },
        {
            "annotation_id": "area",
            "label": "An area",
            "geometry": "polygon",
            "coordinates": [[-74, 40], [-73, 40], [-73, 41]],
        },
    ):
        assert control.execute(payload("earth.annotation.add", annotation))["ok"]

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        exported = client.get("/api/annotations/export", headers=auth)
        assert exported.status_code == 200, exported.text
        collection = exported.json()
        assert collection["type"] == "FeatureCollection"
        kinds = {f["id"]: f["geometry"]["type"] for f in collection["features"]}
        assert kinds == {
            "pin": "Point",
            "route": "LineString",
            "area": "Polygon",
        }
        # A GeoJSON ring must be closed on the way out.
        ring = next(
            f for f in collection["features"] if f["id"] == "area"
        )["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1] and len(ring) == 4

        for identifier in ("pin", "route", "area"):
            assert control.execute(
                payload("earth.annotation.remove", {"annotation_id": identifier})
            )["ok"]
        assert control.snapshot()["annotations"] == []

        restored = client.post(
            "/api/annotations/import",
            json=collection,
            headers={**auth, "Origin": "http://127.0.0.1:8765"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["errors"] == []
        assert sorted(restored.json()["imported"]) == ["area", "pin", "route"]

    # The re-imported scene matches what was exported, ring closure included.
    round_tripped = {a["annotation_id"]: a for a in control.snapshot()["annotations"]}
    assert round_tripped["area"]["coordinates"] == [[-74, 40], [-73, 40], [-73, 41]]
    assert round_tripped["route"]["color"] == "cyan"
    assert round_tripped["pin"]["geometry"] == "point"


def test_annotation_import_rejects_unusable_features(earth, tmp_path):
    engine, control = earth
    app = create_app(
        engine,
        control,
        CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets",
        tmp_path,
        "test-token",
        origin="http://127.0.0.1:8765",
    )
    headers = {
        "Authorization": "Bearer test-token",
        "Origin": "http://127.0.0.1:8765",
    }
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/annotations/import",
            json={
                "type": "FeatureCollection",
                "features": [
                    {"geometry": {"type": "MultiPolygon", "coordinates": []}},
                    {
                        "id": "off-globe",
                        "geometry": {"type": "Point", "coordinates": [999, 999]},
                    },
                    {
                        "id": "good one!",
                        "geometry": {"type": "Point", "coordinates": [10, 20]},
                        "properties": {"label": "Kept"},
                    },
                ],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # Unsupported and out-of-range features are reported, not silently dropped.
        assert [error["index"] for error in body["errors"]] == [0, 1]
        assert body["imported"] == ["good-one-"]
        assert client.get("/api/annotations/export", headers=headers).json()[
            "features"
        ][0]["properties"]["label"] == "Kept"


def test_earth_explanation_uses_its_own_fast_profile(earth, monkeypatch):
    import blackfong_ecosystem.config as config
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    _, control = earth
    monkeypatch.setattr(
        config,
        "chat_settings",
        SimpleNamespace(
            ollama_url="http://localhost:11434",
            model_for_agent=lambda _: "the-big-slow-model",
            earth_explanation_model="the-small-fast-model",
            earth_explanation_timeout=120.0,
            earth_explanation_num_predict=300,
            earth_evidence_chars=6000,
        ),
    )
    # Ollama answers; the dedicated llama.cpp endpoint does not.
    monkeypatch.setattr(
        EarthOrchestration,
        "_reachable",
        staticmethod(lambda base_url, timeout=1.0: "11434" in base_url),
    )
    backend = EarthOrchestration(control)._local_backend()
    # Earth must not inherit the general DAISE chat model for this workload.
    assert backend.model == "the-small-fast-model"
    assert backend.timeout == 120.0
    assert backend.num_predict == 300


def test_dead_llamacpp_endpoint_does_not_shadow_a_running_ollama(earth, monkeypatch):
    import blackfong_ecosystem.config as config
    from blackfong_ecosystem.chat_backends import OllamaChatBackend
    from blackfong_ecosystem.earth.daise import EarthOrchestration

    _, control = earth
    monkeypatch.setattr(
        config,
        "chat_settings",
        SimpleNamespace(
            ollama_url="http://localhost:11434",
            model_for_agent=lambda _: "fixture-local",
            earth_explanation_model="fixture-local",
            earth_explanation_timeout=120.0,
            earth_explanation_num_predict=300,
            earth_evidence_chars=6000,
        ),
    )
    probed = []

    def reachable(base_url, timeout=1.0):
        probed.append(base_url)
        return "11434" in base_url  # only Ollama answers

    monkeypatch.setattr(EarthOrchestration, "_reachable", staticmethod(reachable))
    backend = EarthOrchestration(control)._local_backend()
    assert isinstance(backend, OllamaChatBackend)
    assert "11434" in backend.base_url
    assert probed, "the endpoint must be probed rather than assumed live"
