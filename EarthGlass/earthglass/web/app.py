"""EarthGlass FastAPI application.

Milestone 1 exposes a small read-only surface: a health probe, a layer
catalog, and a Jinja-rendered Leaflet dashboard. No database, ingestion,
or live data sources are wired up yet.
"""

from pathlib import Path
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
LEAFLET_JS = STATIC_DIR / "vendor" / "leaflet.js"
LEAFLET_CSS = STATIC_DIR / "vendor" / "leaflet.css"

# Static metadata for the layers EarthGlass knows how to render. Real data
# sources arrive in a later milestone; for now this is the single source of
# truth shared by the API and the CLI.
LAYERS: list[dict[str, object]] = [
    {
        "id": "earthquakes",
        "name": "Earthquakes",
        "description": "Recent global seismic events.",
        "type": "point",
        "enabled": True,
        "attribution": "USGS (placeholder, not yet wired)",
    }
]


def get_layers() -> list[dict[str, object]]:
    """Return a copy of the layer catalog so callers cannot mutate state."""
    return [dict(layer) for layer in LAYERS]


def asset_status() -> dict[str, object]:
    offline_assets = os.getenv("EARTHGLASS_OFFLINE_ASSETS", "1").strip().lower() not in {"0", "false", "no"}
    local_leaflet_available = LEAFLET_JS.exists() and LEAFLET_CSS.exists()
    online_tiles_enabled = not offline_assets
    map_enabled = online_tiles_enabled or local_leaflet_available
    state = "ok" if map_enabled else "degraded_offline_assets_unavailable"
    return {
        "state": state,
        "offline_assets": offline_assets,
        "local_leaflet_available": local_leaflet_available,
        "online_tiles_enabled": online_tiles_enabled,
        "map_enabled": map_enabled,
    }


app = FastAPI(title="EarthGlass", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "earthglass"}


@app.get("/api/layers")
def api_layers() -> list[dict[str, object]]:
    return get_layers()


@app.get("/api/status")
def api_status() -> dict[str, object]:
    return {"status": "ok", "service": "earthglass", "assets": asset_status()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    assets = asset_status()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"layers": get_layers(), "assets": assets},
    )
