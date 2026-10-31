"""Authenticated loopback server for bundled assets and the browser fallback contract."""

from __future__ import annotations
import base64
import hmac
from pathlib import Path
import re
import time
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from earthglass.spatial.api import build_router
from earthglass.spatial.models import Viewport

from ..json_utils import make_json_compatible
from .contracts import Annotation, Goto

GEOJSON_GEOMETRY = {"Point": "point", "LineString": "line", "Polygon": "polygon"}
EARTH_GEOMETRY = {value: key for key, value in GEOJSON_GEOMETRY.items()}


class AnnotationImport(BaseModel):
    """A GeoJSON FeatureCollection; every feature is re-validated against Earth's own
    annotation contract before it is allowed to touch scene state."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["FeatureCollection"]
    features: list[dict] = Field(max_length=250)


def geojson_feature(annotation):
    points = [list(point) for point in annotation["coordinates"]]
    kind = EARTH_GEOMETRY[annotation["geometry"]]
    if kind == "Point":
        coordinates = points[0]
    elif kind == "Polygon":
        ring = points + ([points[0]] if points[0] != points[-1] else [])
        coordinates = [ring]
    else:
        coordinates = points
    return {
        "type": "Feature",
        "id": annotation["annotation_id"],
        "geometry": {"type": kind, "coordinates": coordinates},
        "properties": {
            "annotation_id": annotation["annotation_id"],
            "label": annotation["label"],
            "color": annotation["color"],
        },
    }


def earth_annotation(feature, index):
    """Map one GeoJSON feature onto the Annotation contract, or raise ValueError."""
    if not isinstance(feature, dict):
        raise ValueError("Feature must be an object")
    geometry = feature.get("geometry") or {}
    kind = GEOJSON_GEOMETRY.get(geometry.get("type"))
    if kind is None:
        raise ValueError("Only Point, LineString and Polygon are supported")
    coordinates = geometry.get("coordinates")
    if kind == "point":
        coordinates = [coordinates]
    elif kind == "polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("Polygon requires one linear ring")
        coordinates = coordinates[0]
        # A GeoJSON ring repeats its first point; Earth stores it unclosed.
        if (
            isinstance(coordinates, list)
            and len(coordinates) > 3
            and coordinates[0] == coordinates[-1]
        ):
            coordinates = coordinates[:-1]
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("Feature properties must be an object")
    identifier = str(
        feature.get("id") or properties.get("annotation_id") or f"import-{index}"
    )
    identifier = re.sub(r"[^a-zA-Z0-9_-]", "-", identifier)[:80] or f"import-{index}"
    colour = properties.get("color")
    return Annotation(
        annotation_id=identifier,
        label=str(properties.get("label") or properties.get("name") or identifier)[:200],
        geometry=kind,
        coordinates=coordinates,
        color=colour if colour in {"cyan", "gold", "red", "white"} else "gold",
    )


class ViewUpdate(Viewport):
    camera: Goto | None = None


class ExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(
        default="Explain the current scene and suggest an investigation.",
        min_length=1,
        max_length=2000,
    )


class ChatRequest(BaseModel):
    """Operator chat from the Earth overlay. Mirrors ExplanationRequest's shape, but
    unlike daise.explain this carries session identity, so turns accumulate in the
    operator's shared conversation rather than standing alone."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    analysis_mode: Literal["fast", "standard", "deep"] = "standard"


class RenderMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    canvasWidth: int | None = Field(default=None, ge=0, le=20000)
    canvasHeight: int | None = Field(default=None, ge=0, le=20000)
    detailBudget: int | None = Field(default=None, ge=1, le=10000)
    frameP50Ms: float | None = Field(default=None, ge=0, le=100000)
    frameP95Ms: float | None = Field(default=None, ge=0, le=100000)
    worstFrameP95Ms: float | None = Field(default=None, ge=0, le=100000)
    frameSampledIntervals: int | None = Field(default=None, ge=0, le=1000000000)
    frameWindowCount: int | None = Field(default=None, ge=0, le=1000000000)
    renderMode: str | None = Field(default=None, pattern="^(idle|continuous|hidden)$")
    renderOwners: str | None = Field(default=None, max_length=200)
    resolution: float | None = Field(default=None, ge=0.1, le=2)
    targetFPS: int | None = Field(default=None, ge=1, le=120)
    knownTracks: int | None = Field(default=None, ge=0, le=100000)
    renderedTracks: int | None = Field(default=None, ge=0, le=10000)
    updateMs: float | None = Field(default=None, ge=0, le=100000)
    loadGeneration: int | None = Field(default=None, ge=0)
    loadedPages: int | None = Field(default=None, ge=0, le=10)
    fetchedTracks: int | None = Field(default=None, ge=0, le=25000)
    loadPhase: str | None = Field(default=None, max_length=20)
    loadError: str | None = Field(default=None, max_length=500)


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    png: str = Field(max_length=11 * 1024 * 1024)


def create_app(
    engine,
    control,
    broker,
    assets: Path,
    data_dir: Path,
    session_token: str,
    *,
    origin: str,
    browser_tickets=None,
):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            application.state.weather.close()
            application.state.tiles.close()
            application.state.presence.close()

    app = FastAPI(
        title="OBEOS Earth",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    from earthglass.spatial.local_maps import map_router

    app.include_router(map_router(data_dir / "maps" / "nyc"))
    from earthglass.spatial.reference_maps import reference_router

    app.include_router(reference_router(data_dir / "maps" / "reference"))
    from .daise import EarthOrchestration

    daise = EarthOrchestration(control)
    app.state.daise = daise
    tickets = browser_tickets if browser_tickets is not None else {}

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        if request.headers.get("host") != origin.split("://", 1)[1]:
            return JSONResponse({"error": "invalid_host"}, status_code=403)
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        bearer = bool(supplied) and hmac.compare_digest(supplied, session_token)
        if request.url.path == "/api/session" and supplied and not bearer:
            expires = tickets.pop(supplied, 0)
            bearer = expires > time.monotonic()
        cookie = request.cookies.get("obeos_earth", "")
        public_asset = request.method in {"GET", "HEAD"} and (
            request.url.path == "/"
            or request.url.path.startswith(("/assets/", "/cesium/", "/models/"))
        )
        if (
            not public_asset
            and not bearer
            and not (cookie and hmac.compare_digest(cookie, session_token))
        ):
            return JSONResponse({"error": "local_session_required"}, status_code=401)
        if (
            request.method not in {"GET", "HEAD"}
            and not bearer
            and request.headers.get("origin") != origin
        ):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 12 * 1024 * 1024):
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        if request.method not in {"GET", "HEAD"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 12 * 1024 * 1024:
                    return JSONResponse({"error": "request_too_large"}, status_code=413)
            request._body = bytes(body)
        request.state.earth_owner = bool(
            bearer or (cookie and hmac.compare_digest(cookie, session_token))
        )
        response = await call_next(request)
        if bearer:
            response.set_cookie(
                "obeos_earth", session_token, httponly=True, samesite="strict", path="/"
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        remote_maps = (
            ""
            if engine.offline
            else " https://server.arcgisonline.com https://tile.openstreetmap.org"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval' qrc:; "
            "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; "
            f"img-src 'self' data: blob:{remote_maps}; "
            f"connect-src 'self'{remote_maps}; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.post("/api/session")
    def session():
        return {"ok": True}

    app.include_router(build_router(engine))
    from earthglass.weather.api import WeatherService

    app.state.weather = WeatherService(data_dir / "weather" / "gfs", lambda: engine.offline)
    app.include_router(app.state.weather.router())
    from earthglass.spatial.products import TileCache
    from earthglass.spatial.v2_api import build_v2_router

    tiles = TileCache(data_dir / "imagery-tiles.sqlite3")
    app.state.tiles = tiles

    def enable_imported_transit():
        from uuid import uuid4

        for layer in ("transit_routes", "transit_stops"):
            result = control.execute(
                {
                    "schema_version": "1.0",
                    "request_id": str(uuid4()),
                    "scene_id": control.snapshot()["scene_id"],
                    "command": "earth.layer.enable",
                    "arguments": {"layer": layer},
                },
                origin="operator",
            )
            if not result["ok"]:
                raise ValueError(result["error"]["message"])

    app.include_router(
        build_v2_router(engine, tiles, on_transit_import=enable_imported_transit)
    )
    from earthglass.presence.store import PresenceStore
    from earthglass.presence.api import build_presence_app

    presence = PresenceStore(data_dir / "private" / "presence.sqlite3")
    app.state.presence = presence
    app.mount(
        "/private",
        build_presence_app(
            presence,
            owner_auth=lambda request: getattr(request.state, "earth_owner", False),
            require_tls=False,
        ),
    )

    @app.get("/api/scene")
    def scene():
        return control.snapshot()

    @app.post("/api/control")
    def command(payload: dict):
        return control.execute(payload, origin="operator")

    @app.post("/api/daise/control")
    def daise_command(payload: dict):
        # Explicit operator-issued proposals enter the same Blackfong policy boundary.
        return daise.execute_proposal(payload)

    @app.post("/api/daise/explain")
    def explain(payload: ExplanationRequest):
        return daise.explain(payload.question)

    @app.post("/api/chat")
    def chat(payload: ChatRequest):
        service = getattr(app.state, "operator_chat", None)
        if service is None:
            # Deferred to first message, not import or create_app: importing
            # operator_chat constructs a ConversationMemoryService at module scope,
            # and send_message loads the unified orchestrator on its first call.
            # Earth is documented as booting with no model warmup, so neither may
            # land in the startup path.
            try:
                from ..operator_chat import OperatorChatService
            except Exception as exc:
                return {
                    "status": "error",
                    "error": "Operator chat is unavailable in this build.",
                    "reason": type(exc).__name__,
                }
            service = OperatorChatService()
            app.state.operator_chat = service
        result = service.send_message(
            message=payload.message,
            session_id=payload.session_id,
            analysis_mode=payload.analysis_mode,
            include_trace=False,
            context={"input_origin": "earth", "transport": "earth_overlay"},
        )
        # The orchestrator reply carries arbitrary objects; FastAPI's encoder would
        # raise on them, which would lose the whole turn rather than one field.
        return make_json_compatible(result)

    @app.post("/api/viewport")
    def viewport(payload: ViewUpdate):
        engine.set_viewport(payload.model_dump(exclude={"camera"}))
        if payload.camera:
            control.record_view(payload.camera.model_dump())
        return {"ok": True}

    @app.post("/api/render-metrics")
    def render_metrics(payload: RenderMetrics):
        engine.renderer_metrics = payload.model_dump(exclude_none=True)
        return {"ok": True}

    @app.get("/api/diagnostics")
    def diagnostics():
        return {
            "history": engine.history.stats(),
            "providers": engine.catalog(),
            "maintenance_error": engine.maintenance_error,
            "delivery_error": engine.delivery_error,
            "network": engine.network_status(),
            "optional_sources": broker.status() if broker else {"providers": []},
            "local_models": True,
            "data_dir": str(data_dir),
            "renderer": getattr(engine, "renderer_metrics", {}),
            "render_target_fps": 30,
        }

    @app.get("/api/annotations/export")
    def export_annotations():
        """The current scene's annotations as WGS84 GeoJSON, for QGIS and friends."""
        annotations = control.snapshot()["annotations"]
        return {
            "type": "FeatureCollection",
            "features": [geojson_feature(item) for item in annotations],
        }

    @app.post("/api/annotations/import")
    def import_annotations(payload: AnnotationImport):
        from uuid import uuid4

        imported, errors = [], []
        for index, feature in enumerate(payload.features):
            try:
                annotation = earth_annotation(feature, index)
            except (ValidationError, ValueError, TypeError) as exc:
                errors.append({"index": index, "error": str(exc)[:200]})
                continue
            # Scene state changes stay behind the same policy boundary as any command.
            result = control.execute(
                {
                    "schema_version": "1.0",
                    "request_id": str(uuid4()),
                    "scene_id": control.snapshot()["scene_id"],
                    "command": "earth.annotation.add",
                    "arguments": annotation.model_dump(),
                },
                origin="operator",
            )
            if result["ok"]:
                imported.append(annotation.annotation_id)
            else:
                errors.append(
                    {"index": index, "error": result["error"]["message"][:200]}
                )
        return {"imported": imported, "imported_count": len(imported), "errors": errors}

    @app.get("/api/investigations")
    def investigations():
        return control.investigations()

    @app.get("/api/investigations/{ident}")
    def investigation(ident: str):
        try:
            return control.investigation(ident)
        except KeyError:
            raise HTTPException(404, "Investigation not found")

    @app.post("/api/investigations/{ident}/image")
    def save_image(ident: str, payload: Canvas):
        if not re.fullmatch("[a-f0-9]{24}", ident):
            raise HTTPException(422, "Invalid investigation ID")
        try:
            control.investigation(ident)
            image = base64.b64decode(
                payload.png.removeprefix("data:image/png;base64,"), validate=True
            )
        except (KeyError, ValueError):
            raise HTTPException(422, "Invalid image or investigation")
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 8 * 1024 * 1024:
            raise HTTPException(422, "Expected a PNG smaller than 8 MiB")
        path = data_dir / "investigations"
        path.mkdir(parents=True, exist_ok=True)
        (path / (ident + ".png")).write_bytes(image)
        return {"saved": True}

    @app.get("/api/camera-image")
    def camera_image(entity_id: str):
        if engine.offline:
            raise HTTPException(
                503,
                "Live public camera images are unavailable offline; stored camera locations remain available",
            )
        values = engine.query(entity_id=entity_id, limit=1)["observations"]
        if not values or values[0]["layer"] != "cctv":
            raise HTTPException(404, "Camera not found")
        url = values[0]["properties"].get("image_url")
        if not url:
            raise HTTPException(404, "No supported still-image feed")
        try:
            data, content_type = engine.http.bytes(url)
        except Exception:
            raise HTTPException(503, "Public image is unavailable")
        if not content_type.startswith(("image/jpeg", "image/png", "image/webp")):
            raise HTTPException(415, "This camera format is not supported")
        return Response(
            data,
            media_type=content_type.split(";")[0],
            headers={"Cache-Control": "no-store"},
        )

    # Compatibility adapter: the v1 earthquake URL reads the same v2 cache.
    @app.get("/api/layers/earthquakes/features")
    def earthquake_compatibility():
        from earthglass.spatial.compatibility import earthquake_collection

        return earthquake_collection(engine)

    @app.post("/api/layers/earthquakes/refresh")
    def refresh_earthquake_compatibility():
        engine.enable("earthquakes")
        # The provider scheduler enforces refresh quotas; this response truthfully remains a cache read.
        return earthquake_compatibility()

    @app.get("/")
    def index():
        if not (assets / "index.html").exists():
            return Response(
                "OBEOS Earth assets have not been built. Run the documented desktop bootstrap.",
                status_code=503,
            )
        return FileResponse(
            assets / "index.html", headers={"Cache-Control": "no-cache"}
        )

    if assets.exists():
        app.mount("/", StaticFiles(directory=assets), name="earth-assets")
    return app
