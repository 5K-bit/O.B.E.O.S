from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from nightwatch.db import get_db, init_db
from nightwatch.schemas import (
    ShiftNotesIn,
    ShiftOut,
    ShiftStartOut,
    SystemOut,
    TaskIn,
    TaskOut,
)
from nightwatch.services import (
    add_task,
    complete_task,
    delete_task,
    end_shift,
    get_active_shift,
    list_tasks_for_active_shift,
    reopen_task,
    set_shift_notes,
    start_shift,
)
from nightwatch.system_watch import read_system_snapshot


def _shift_out(s) -> ShiftOut:
    return ShiftOut(id=s.id, started_at=s.started_at, ended_at=s.ended_at, notes=s.notes)


def _task_out(t) -> TaskOut:
    return TaskOut(
        id=t.id,
        title=t.title,
        created_at=t.created_at,
        completed_at=t.completed_at,
        shift_id=t.shift_id,
    )


def create_app() -> FastAPI:
    async def _backup_loop() -> None:
        # init_db() already does a daily backup once; loop keeps it daily.
        from nightwatch.backup import ensure_daily_backup
        from nightwatch.config import get_settings

        while True:
            ensure_daily_backup(get_settings().db_path, get_settings().backups_dir)
            await asyncio.sleep(30 * 60)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        task = asyncio.create_task(_backup_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            # Final backup attempt on shutdown.
            from nightwatch.backup import ensure_daily_backup
            from nightwatch.config import get_settings

            ensure_daily_backup(get_settings().db_path, get_settings().backups_dir)

    app = FastAPI(title="Nightwatch OS Dashboard", version="0.1.0", lifespan=lifespan)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> Response:
        return FileResponse(static_dir / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        # Avoid noisy 404s; no icon in MVP.
        return Response(status_code=204)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/shift/current", response_model=ShiftOut | None)
    def shift_current(db: Session = Depends(get_db)) -> ShiftOut | None:
        s = get_active_shift(db)
        return _shift_out(s) if s else None

    @app.post("/api/shift/start", response_model=ShiftStartOut)
    def shift_start(db: Session = Depends(get_db)) -> ShiftStartOut:
        new_shift, carried_count, already_active = start_shift(db)
        return ShiftStartOut(
            shift=_shift_out(new_shift),
            carried_task_count=carried_count,
            already_active=already_active,
        )

    @app.post("/api/shift/end", response_model=ShiftOut)
    def shift_end(db: Session = Depends(get_db)) -> ShiftOut:
        ended = end_shift(db)
        if not ended:
            raise HTTPException(status_code=409, detail="No active shift.")
        return _shift_out(ended)

    @app.put("/api/shift/{shift_id}/notes", response_model=ShiftOut)
    def shift_notes(shift_id: int, payload: ShiftNotesIn, db: Session = Depends(get_db)) -> ShiftOut:
        s = set_shift_notes(db, shift_id, payload.notes)
        if not s:
            raise HTTPException(status_code=404, detail="Shift not found.")
        return _shift_out(s)

    @app.get("/api/tasks/current", response_model=list[TaskOut])
    def tasks_current(db: Session = Depends(get_db)) -> list[TaskOut]:
        tasks = list_tasks_for_active_shift(db)
        return [_task_out(t) for t in tasks]

    @app.post("/api/tasks", response_model=TaskOut)
    def task_add(payload: TaskIn, db: Session = Depends(get_db)) -> TaskOut:
        t = add_task(db, payload.title)
        return _task_out(t)

    @app.post("/api/tasks/{task_id}/complete", response_model=TaskOut)
    def task_complete(task_id: int, db: Session = Depends(get_db)) -> TaskOut:
        t = complete_task(db, task_id)
        if not t:
            raise HTTPException(status_code=404, detail="Task not found.")
        return _task_out(t)

    @app.post("/api/tasks/{task_id}/reopen", response_model=TaskOut)
    def task_reopen(task_id: int, db: Session = Depends(get_db)) -> TaskOut:
        t = reopen_task(db, task_id)
        if not t:
            raise HTTPException(status_code=404, detail="Task not found.")
        return _task_out(t)

    @app.delete("/api/tasks/{task_id}")
    def task_delete(task_id: int, db: Session = Depends(get_db)) -> dict:
        ok = delete_task(db, task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {"ok": True}

    @app.get("/api/system", response_model=SystemOut)
    def system() -> dict:
        return read_system_snapshot()

    return app

