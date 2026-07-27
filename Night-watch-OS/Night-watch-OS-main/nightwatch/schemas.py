from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ShiftOut(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    notes: str


class ShiftStartOut(BaseModel):
    shift: ShiftOut
    carried_task_count: int = 0
    already_active: bool = False


class ShiftNotesIn(BaseModel):
    notes: str = Field(default="", max_length=200_000)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class TaskOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    completed_at: datetime | None
    shift_id: int | None


class SystemOut(BaseModel):
    at: datetime
    cpu_percent: float
    ram_percent: float
    ram_used_mb: int
    ram_total_mb: int
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    temp_c: float | None
    network_up: bool

