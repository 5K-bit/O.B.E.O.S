# EarthGlass

A layered geospatial dashboard. **Milestone 1** ships the application skeleton:
a FastAPI service, a layer catalog, a Leaflet dashboard, and a Typer CLI.

This milestone intentionally has **no database, no ingestion, no AI, no live
APIs, and no Cesium** — just a clean, installable foundation.

## Layout

```
earthglass/
  earthglass/
    __init__.py
    cli.py            # Typer CLI (serve / layers / cache status)
    web/
      __init__.py
      app.py          # FastAPI app + layer catalog
      templates/
        index.html    # Leaflet dashboard
      static/
        app.css
  tests/
  pyproject.toml
  README.md
```

## Installation

```bash
python -m pip install -e ".[dev]"
```

## Web API

- `GET /health` → `{"status": "ok", "service": "earthglass"}`
- `GET /api/layers` → list of layer metadata (includes the `earthquakes` layer)
- `GET /` → Jinja-rendered Leaflet dashboard with a layer sidebar

Run the server:

```bash
earthglass serve            # http://127.0.0.1:8000
earthglass serve --reload   # development mode
```

## CLI

```bash
earthglass --help
earthglass layers           # list available layers
earthglass cache status     # show cache state (empty in Milestone 1)
```

## Development

```bash
pytest
```
