"""Blackfong-authorized, idempotent scene operations shared by native and HTTP transports."""

from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from pydantic import ValidationError

from .contracts import Command
from .context_fabric import ContextFabric


def distance_m(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b - a, math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


EARTH_RADIUS_M = 6371000


def radius_bounds(latitude, longitude, radius_m):
    """Smallest lat/lon box enclosing the radius circle, or None when it spans a pole.

    The box is always a superset of the circle, so using it as a query filter can
    never drop a true match; the exact distance test still decides membership.
    """
    angular = radius_m / EARTH_RADIUS_M
    pad = 1e-6
    south, north = latitude - math.degrees(angular), latitude + math.degrees(angular)
    if angular >= math.pi / 2 or south <= -90 or north >= 90:
        return None
    ratio = math.sin(angular) / math.cos(math.radians(latitude))
    if ratio >= 1:
        return None
    dlon = math.degrees(math.asin(ratio)) + pad
    if dlon >= 180:
        return None
    return {
        "south": max(-90.0, south - pad),
        "north": min(90.0, north + pad),
        "west": (longitude - dlon + 180) % 360 - 180,
        "east": (longitude + dlon + 180) % 360 - 180,
    }


class EarthControl:
    def __init__(self, engine, state_path: Path, *, policy=None, context=None):
        from ..policy_engine import PolicyEngine

        self.engine = engine
        self.policy = policy or PolicyEngine()
        self.context = context or ContextFabric(engine)
        self._lock = threading.RLock()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(state_path), check_same_thread=False)
        self._con.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS scene(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,digest TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS investigations(id TEXT PRIMARY KEY,label TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
        """)
        row = self._con.execute("SELECT payload FROM scene WHERE id='home'").fetchone()
        self.state = (
            json.loads(row[0])
            if row
            else {
                "schema_version": "1.0",
                "scene_id": "home",
                "revision": 0,
                "camera": {
                    "mode": "reset",
                    "latitude": 28.0,
                    "longitude": -35.0,
                    "altitude_m": 16000000,
                },
                "selected_entity_id": None,
                "tracked_entity_id": None,
                "enabled_layers": ["earthquakes"],
                "annotations": [],
                "timeline": {"at": None, "playing": False, "speed": 60},
            }
        )

        for layer in self.state.get("enabled_providers", self.state.get("enabled_layers", ["earthquakes"])):
            try:
                self.engine.enable(layer)
            except KeyError:
                continue

        self.state["enabled_layers"] = sorted({p["layer"] for p in self.engine.catalog() if p["enabled"]})
        self.state["enabled_providers"] = sorted(p["id"] for p in self.engine.catalog() if p["enabled"])

    def snapshot(self):
        with self._lock:
            return {**json.loads(json.dumps(self.state)), "network_mode": self.engine.network_status()["mode"]}

    def execute(self, payload, *, origin="operator"):
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        try:
            command = Command.model_validate(payload)
            args = command.validated_arguments()
        except (ValidationError, ValueError, TypeError):
            return self._error(
                request_id,
                "invalid_command",
                "Command or arguments do not match Earth Control v1",
            )
        if origin not in {"operator", "daise"}:
            return self._error(
                request_id, "unauthorized_origin", "Command origin is not authorized"
            )
        if command.scene_id != self.state["scene_id"]:
            return self._error(
                request_id, "scene_not_found", "The requested scene is not active"
            )
        decision = self.policy.evaluate(
            command.command,
            {"source": origin, "surface": "earth", "transport": "local"},
        )
        if not decision.approved:
            return self._error(request_id, "policy_denied", "; ".join(decision.reasons))
        digest = hashlib.sha256(
            json.dumps(command.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        with self._lock:
            existing = self._con.execute(
                "SELECT digest,result FROM receipts WHERE id=?", (request_id,)
            ).fetchone()
            if existing:
                return (
                    json.loads(existing[1])
                    if existing[0] == digest
                    else self._error(
                        request_id,
                        "request_id_conflict",
                        "Request ID was already used with different arguments",
                    )
                )
            previous = self.snapshot()
            try:
                result = self._dispatch(command.command, args)
                if command.command.startswith("earth.camera.") or command.command in {"earth.goto", "earth.entity.track", "earth.entity.untrack", "earth.scene.restore"}:
                    self.state["camera_revision"] = previous.get("camera_revision", 0) + 1
                self.state["revision"] += 1
                response = {
                    "schema_version": "1.0",
                    "request_id": request_id,
                    "scene_id": command.scene_id,
                    "ok": True,
                    "result": result,
                    "scene": self.snapshot(),
                    "error": None,
                }
                with self._con:
                    self._con.execute(
                        "INSERT OR REPLACE INTO scene VALUES('home',?)",
                        (json.dumps(self.state),),
                    )
                    self._con.execute(
                        "INSERT INTO receipts VALUES(?,?,?,?)",
                        (
                            request_id,
                            digest,
                            json.dumps(response),
                            datetime.now(timezone.utc).timestamp(),
                        ),
                    )
                    self._con.execute(
                        "DELETE FROM receipts WHERE id IN (SELECT id FROM receipts ORDER BY created DESC LIMIT -1 OFFSET 2000)"
                    )
                return response
            except (ValueError, KeyError) as exc:
                self.state = previous
                return self._error(request_id, "not_available", str(exc))
            except Exception:
                self.state = previous
                return self._error(
                    request_id,
                    "operation_failed",
                    "The operation could not be completed",
                )

    def _entity(self, entity_id):
        if self.state.get("active_investigation_id"):
            values = self.investigation(self.state["active_investigation_id"])[
                "observations"
            ]["observations"]
            result = [item for item in values if item["entity_id"] == entity_id]
            if not result:
                raise ValueError("Entity is not in this saved investigation")
            return result[0]
        page = self.engine.query(entity_id=entity_id, at=self._at(), limit=1)
        result = page.get("volatile_observations", []) or page["observations"]
        if not result:
            raise ValueError("Entity is not available in local history")
        return result[0]

    def _at(self):
        value = self.state["timeline"]["at"]
        return datetime.fromisoformat(value) if value else None

    def _dispatch(self, name, args):
        if name == "earth.watches.list":
            return self.engine.watch_list()
        if name == "earth.watch.add":
            return self.engine.set_watch(args.name, args.model_dump(exclude={"name"}))
        if name == "earth.watch.remove":
            return self.engine.set_watch(args.name)
        if name == "earth.layers.list":
            return self.engine.catalog()
        if name in {"earth.layer.enable", "earth.layer.disable"}:
            result = self.engine.enable(args.layer, name.endswith("enable"))
            self.state["enabled_layers"] = sorted(
                {provider["layer"] for provider in result if provider["enabled"]}
            )
            self.state["enabled_providers"] = sorted(p["id"] for p in result if p["enabled"])
            return result
        if name == "earth.network.set":
            return self.engine.set_offline(args.mode == "offline")
        if name == "earth.imagery.set":
            products=[p for p in self.state.get("imagery",[]) if p["product"]!=args.product]
            if args.enabled:
                if len(products)>=2:raise ValueError("At most two imagery overlays can be active")
                products.append({"product":args.product,"opacity":args.opacity})
            self.state["imagery"]=products
            self.engine.enable("imagery",bool(products))
            self.state["enabled_layers"]=sorted({p["layer"] for p in self.engine.catalog() if p["enabled"]})
            self.state["enabled_providers"]=sorted(p["id"] for p in self.engine.catalog() if p["enabled"])
            return products
        if name == "earth.map.set":
            self.state["basemap"] = args.mode
            return {"mode": args.mode}
        if name == "earth.goto":
            self.state["camera"] = {"mode": "goto", **args.model_dump()}
            self.state["tracked_entity_id"] = None
            return self.state["camera"]
        if name == "earth.search":
            text = args.query.strip().lower()
            try:
                lat, lon = [float(p.strip()) for p in text.split(",")]
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return {
                        "matches": [
                            {
                                "label": args.query,
                                "latitude": lat,
                                "longitude": lon,
                                "kind": "coordinate",
                            }
                        ]
                    }
            except ValueError:
                pass
            # Search the bounded local index; no query text is sent to an external geocoder.
            matches = [
                {
                    "label": item.get("properties", {}).get("name")
                    or item["entity_id"],
                    "entity_id": item["entity_id"],
                    **item["position"],
                    "kind": item["entity_type"],
                }
                for item in self._search_local(text, limit=50)
            ]
            from earthglass.spatial.places import search_places

            places = search_places(args.query)
            if text == "earth":
                places = [{"label": "Earth", "latitude": 0, "longitude": 0, "kind": "place"}]
            return {"matches": [*places, *matches][:50], "scope": "Local contacts, coordinates and GeoNames cities", "attribution": "City search: GeoNames · CC BY 4.0"}
        if name == "earth.entities.nearby":
            matches = []
            for item in self._all_local(
                bounds=radius_bounds(args.latitude, args.longitude, args.radius_m)
            ):
                p = item["position"]
                distance = distance_m(
                    args.latitude, args.longitude, p["latitude"], p["longitude"]
                )
                if distance <= args.radius_m:
                    matches.append({"distance_m": round(distance), "observation": item})
            return sorted(matches, key=lambda item: item["distance_m"])[: args.limit]
        if name == "earth.entity.inspect":
            item = self._entity(args.entity_id)
            self.state["selected_entity_id"] = args.entity_id
            return item
        if (
            name == "earth.entity.track"
            or name.startswith("earth.camera.")
            and name != "earth.camera.reset"
        ):
            self._entity(args.entity_id)
            self.state["tracked_entity_id"] = args.entity_id
            self.state["camera"] = {
                "mode": name.rsplit(".", 1)[1] if ".camera." in name else "follow",
                "entity_id": args.entity_id,
                "distance_m": getattr(args, "distance_m", 5000),
            }
            return self.state["camera"]
        if name == "earth.entity.untrack":
            self.state["tracked_entity_id"] = None
            self.state["camera"] = {"mode": "free"}
            return {"tracked": None}
        if name == "earth.camera.reset":
            self.state["tracked_entity_id"] = None
            self.state["camera"] = {
                "mode": "reset",
                "latitude": 28,
                "longitude": -35,
                "altitude_m": 16000000,
            }
            return self.state["camera"]
        if name == "earth.scene.describe":
            return self.describe_scene()
        if name == "earth.scene.restore":
            saved = self.investigation(args.investigation_id)
            revision = self.state["revision"]
            self.state = saved["scene"]
            self.state["revision"] = revision
            self.state["active_investigation_id"] = args.investigation_id
            self.state["timeline"]["playing"] = False
            view = self.state.get("view") or self.state.get("camera", {})
            if "latitude" in view:
                self.state["camera"] = {
                    "mode": "goto",
                    "latitude": view["latitude"],
                    "longitude": view["longitude"],
                    "altitude_m": view["altitude_m"],
                }
            provider_ids = self.state.get("enabled_providers")
            enabled = set(self.state.get("enabled_layers", []))
            for provider in self.engine.catalog():
                self.engine.enable(provider["id"], provider["id"] in provider_ids if provider_ids is not None else provider["layer"] in enabled)
            return {
                "investigation_id": args.investigation_id,
                "saved_observations": len(saved["observations"]["observations"]),
            }
        if name == "earth.scene.snapshot":
            ident = hashlib.sha256(
                (datetime.now(timezone.utc).isoformat() + args.label).encode()
            ).hexdigest()[:24]
            saved = (
                self.investigation(self.state["active_investigation_id"])
                if self.state.get("active_investigation_id")
                else None
            )
            data = {
                "scene": self.snapshot(),
                "observations": saved["observations"]
                if saved
                else self.engine.query(
                    viewport=self.engine.viewport, at=self._at(), limit=5000
                ),
                "context": self.describe_scene(),
            }
            # Volatile provider-restricted data must never be saved.
            data["observations"].pop("volatile_observations", None)
            self._con.execute(
                "INSERT INTO investigations VALUES(?,?,?,?)",
                (
                    ident,
                    args.label,
                    json.dumps(data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return {
                "investigation_id": ident,
                "label": args.label,
                "capture_canvas": True,
            }
        if name == "earth.annotation.add":
            values = [
                a
                for a in self.state["annotations"]
                if a["annotation_id"] != args.annotation_id
            ]
            if len(values) >= 200:
                raise ValueError("This scene has reached its 200 annotation limit")
            self.state["annotations"] = values + [args.model_dump()]
            return args.model_dump()
        if name == "earth.annotation.remove":
            self.state["annotations"] = [
                a
                for a in self.state["annotations"]
                if a["annotation_id"] != args.annotation_id
            ]
            return {"removed": args.annotation_id}
        if name == "earth.timeline.set":
            if args.advance and not self.state["timeline"]["playing"]:
                return self.state["timeline"]
            self.state.pop("active_investigation_id", None)
            self.state["timeline"].update(
                {
                    "at": args.at.isoformat() if args.at else None,
                    "playing": args.advance and args.at is not None,
                }
            )
            return self.state["timeline"]
        if name == "earth.timeline.play":
            if self.state.get("active_investigation_id"):
                raise ValueError(
                    "This is a saved snapshot; return to live history before replaying"
                )
            if args.playing and self._at() is None:
                raise ValueError("Choose a replay time before playing")
            self.state["timeline"].update(args.model_dump())
            return self.state["timeline"]
        raise ValueError("Unsupported command")

    def describe_scene(self):
        if self.state.get("active_investigation_id"):
            return {
                **self.investigation(self.state["active_investigation_id"])["context"],
                "saved_investigation": True,
                "camera_action": None,
            }
        return self.context.describe(
            viewport=self.engine.viewport,
            at=self._at(),
            selected=self.state["selected_entity_id"],
        )

    def _search_local(self, needle, limit=50):
        """Name/ID matches. A saved investigation searches its own stored observations;
        live history uses the indexed engine search instead of walking every record."""
        if not self.state.get("active_investigation_id"):
            yield from self.engine.search(needle, at=self._at(), limit=limit)
            return
        found = 0
        for item in self._all_local():
            if (
                needle in str(item.get("properties", {}).get("name", "")).lower()
                or needle in item["entity_id"].lower()
            ):
                yield item
                found += 1
                if found >= limit:
                    return

    def _all_local(self, bounds=None):
        if self.state.get("active_investigation_id"):
            yield from self.investigation(self.state["active_investigation_id"])[
                "observations"
            ]["observations"]
            return
        from earthglass.spatial.models import Viewport

        viewport = Viewport.model_validate(bounds) if bounds else None
        cursor = 0
        snapshot_rowid = None
        for index in range(20):
            page = self.engine.query(
                at=self._at(),
                limit=1000,
                cursor=cursor,
                snapshot_rowid=snapshot_rowid,
                viewport=viewport,
            )
            snapshot_rowid = page.get("snapshot_rowid")
            yield from page["observations"]
            if index == 0:
                yield from page.get("volatile_observations", [])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    def record_view(self, camera):
        from .contracts import Goto

        view = Goto.model_validate(camera).model_dump()
        with self._lock, self._con:
            self.state["view"] = view
            self._con.execute(
                "INSERT OR REPLACE INTO scene VALUES('home',?)",
                (json.dumps(self.state),),
            )

    def investigations(self):
        with self._lock:
            return [
                dict(zip(("id", "label", "created"), row))
                for row in self._con.execute(
                    "SELECT id,label,created FROM investigations ORDER BY created DESC LIMIT 200"
                )
            ]

    def investigation(self, ident):
        with self._lock:
            row = self._con.execute(
                "SELECT payload FROM investigations WHERE id=?", (ident,)
            ).fetchone()
        if not row:
            raise KeyError("Investigation not found")
        return json.loads(row[0])

    def _error(self, request_id, code, message):
        return {
            "schema_version": "1.0",
            "request_id": request_id,
            "scene_id": self.state["scene_id"],
            "ok": False,
            "result": None,
            "error": {"code": code, "message": message},
        }

    def close(self):
        with self._lock:
            self._con.close()
