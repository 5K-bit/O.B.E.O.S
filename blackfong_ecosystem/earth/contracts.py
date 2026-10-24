"""The complete allowlisted Earth Control v1 contract; no executable code payloads."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Empty(Args):
    pass


class Goto(Args):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float = Field(default=500000, ge=100, le=50000000)


class Basemap(Args):
    mode: Literal["auto", "offline", "satellite", "streets"]


class Imagery(Args):
    product: Literal["true_color", "goes_clouds", "infrared", "water_vapor", "aerosols", "snow_ice", "vegetation", "sea_surface"]
    enabled: bool
    opacity: float = Field(default=0.7, ge=0, le=1)


class Network(Args):
    mode: Literal["auto", "offline"]


class Search(Args):
    query: str = Field(min_length=1, max_length=200)


class Layer(Args):
    layer: str = Field(min_length=1, max_length=100)


class Entity(Args):
    entity_id: str = Field(min_length=1, max_length=250)


class Nearby(Goto):
    radius_m: float = Field(default=50000, ge=1, le=2000000)
    limit: int = Field(default=50, ge=1, le=250)


class Camera(Entity):
    distance_m: float = Field(default=5000, ge=50, le=5000000)


class Annotation(Args):
    annotation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    label: str = Field(min_length=1, max_length=200)
    geometry: Literal["point", "line", "polygon"] = "point"
    coordinates: list[list[float]] = Field(min_length=1, max_length=200)
    color: Literal["cyan", "gold", "red", "white"] = "gold"

    @model_validator(mode="after")
    def valid_coordinates(self):
        minimum = {"point": 1, "line": 2, "polygon": 3}[self.geometry]
        if len(self.coordinates) < minimum or (
            self.geometry == "point" and len(self.coordinates) != 1
        ):
            raise ValueError("Incorrect number of geometry coordinates")
        for coordinate in self.coordinates:
            if (
                len(coordinate) != 2
                or not -180 <= coordinate[0] <= 180
                or not -90 <= coordinate[1] <= 90
            ):
                raise ValueError("Coordinates must be [WGS84 longitude, latitude]")
        return self


class RemoveAnnotation(Args):
    annotation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class Timeline(Args):
    at: datetime | None = None
    advance: bool = False

    @field_validator("at")
    @classmethod
    def utc(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Timeline requires an explicit UTC offset")
        return value.astimezone(timezone.utc) if value else None


class Play(Args):
    playing: bool
    speed: float = Field(default=60, ge=0.1, le=3600)


class Watch(Args):
    name: str = Field(min_length=1, max_length=80)
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)


class RemoveWatch(Args):
    name: str = Field(min_length=1, max_length=80)


class Restore(Args):
    investigation_id: str = Field(pattern=r"^[a-f0-9]{24}$")


class Snapshot(Args):
    label: str = Field(default="Scene", max_length=200)


COMMAND_ARGUMENTS = {
    "earth.watch.add": Watch,
    "earth.watch.remove": RemoveWatch,
    "earth.watches.list": Empty,
    "earth.goto": Goto,
    "earth.map.set": Basemap,
    "earth.network.set": Network,
    "earth.imagery.set": Imagery,
    "earth.search": Search,
    "earth.layers.list": Empty,
    "earth.layer.enable": Layer,
    "earth.layer.disable": Layer,
    "earth.entities.nearby": Nearby,
    "earth.entity.inspect": Entity,
    "earth.entity.track": Entity,
    "earth.entity.untrack": Empty,
    "earth.camera.follow": Camera,
    "earth.camera.orbit": Camera,
    "earth.camera.overhead": Camera,
    "earth.camera.reset": Empty,
    "earth.scene.describe": Empty,
    "earth.scene.snapshot": Snapshot,
    "earth.scene.restore": Restore,
    "earth.annotation.add": Annotation,
    "earth.annotation.remove": RemoveAnnotation,
    "earth.timeline.set": Timeline,
    "earth.timeline.play": Play,
}


class Command(Args):
    schema_version: Literal["1.0"]
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    scene_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    command: str
    arguments: dict

    @field_validator("command")
    @classmethod
    def supported(cls, value):
        if value not in COMMAND_ARGUMENTS:
            raise ValueError("Unsupported Earth command")
        return value

    def validated_arguments(self):
        return COMMAND_ARGUMENTS[self.command].model_validate(self.arguments)
