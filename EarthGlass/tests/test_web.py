from fastapi.testclient import TestClient

from earthglass.web.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "earthglass"}


def test_api_layers_contains_earthquakes() -> None:
    response = client.get("/api/layers")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    layer_ids = {layer["id"] for layer in data}
    assert "earthquakes" in layer_ids

    earthquakes = next(layer for layer in data if layer["id"] == "earthquakes")
    assert earthquakes["name"] == "Earthquakes"
    assert "type" in earthquakes


def test_index_renders_dashboard() -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "EarthGlass" in body
    assert "leaflet" in body.lower()
    assert "Earthquakes" in body
