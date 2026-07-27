from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import paths
from ..actions import archive, mark_reviewed, reject, rename
from ..config import Config, load as load_config
from ..db import Item, connect, get_item, list_items
from ..scanner import scan_once
from ..watcher import run_forever


def _templates_dir() -> Path:
    return Path(__file__).parent / "templates"


def _static_dir() -> Path:
    return Path(__file__).parent / "static"


def _item_to_dict(i: Item) -> dict[str, Any]:
    return {
        "id": i.id,
        "path": i.path,
        "filename": i.filename,
        "size": i.size,
        "mtime": i.mtime,
        "first_seen": i.first_seen,
        "status": i.status,
        "reviewed_at": i.reviewed_at,
        "archived_at": i.archived_at,
        "rejected_at": i.rejected_at,
        "tags": i.tags,
    }


def _get_cfg() -> Config:
    cfg_path = paths.config_path()
    if not cfg_path.exists():
        raise HTTPException(
            500,
            detail=f"QuietDrop config not found at {cfg_path}. Run `quietdrop init`.",
        )
    return load_config(cfg_path)


def _get_con(cfg: Config = Depends(_get_cfg)):
    con = connect(paths.db_path())
    try:
        yield con
    finally:
        con.close()


def create_app(*, start_watcher: bool = False) -> FastAPI:
    app = FastAPI(title="QuietDrop")
    templates = Jinja2Templates(directory=str(_templates_dir()))

    app.mount("/static", StaticFiles(directory=str(_static_dir())), name="static")

    if start_watcher:
        cfg = _get_cfg()
        stop = threading.Event()

        def _bg() -> None:
            con = connect(paths.db_path())
            try:
                run_forever(con, cfg, stop_event=stop)
            finally:
                con.close()

        t = threading.Thread(target=_bg, name="quietdrop-watcher", daemon=True)
        t.start()

        @app.on_event("shutdown")
        def _shutdown():  # noqa: ANN202
            stop.set()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, status: str | None = None, con=Depends(_get_con), cfg=Depends(_get_cfg)):
        # keep filesystem as source of truth; refresh quickly on page load
        scan_once(con, cfg)
        items = list_items(con, status=status, limit=200)  # type: ignore[arg-type]
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "items": items,
                "status": status,
            },
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(request: Request, item_id: int, con=Depends(_get_con)):
        item = get_item(con, item_id)
        if not item:
            raise HTTPException(404, detail="not found")
        return templates.TemplateResponse("item.html", {"request": request, "item": item})

    @app.post("/items/{item_id}/review")
    def item_review(item_id: int, con=Depends(_get_con)):
        mark_reviewed(con, item_id)
        return RedirectResponse(url=f"/items/{item_id}", status_code=303)

    @app.post("/items/{item_id}/reject")
    def item_reject(item_id: int, con=Depends(_get_con)):
        reject(con, item_id)
        return RedirectResponse(url=f"/items/{item_id}", status_code=303)

    @app.post("/items/{item_id}/archive")
    def item_archive(item_id: int, con=Depends(_get_con), cfg=Depends(_get_cfg)):
        archive(con, cfg, item_id)
        return RedirectResponse(url=f"/items/{item_id}", status_code=303)

    @app.post("/items/{item_id}/rename")
    def item_rename(
        item_id: int,
        new_name: str = Form(...),
        con=Depends(_get_con),
    ):
        rename(con, item_id, new_name)
        return RedirectResponse(url=f"/items/{item_id}", status_code=303)

    # JSON API
    @app.get("/api/items")
    def api_items(status: str | None = None, con=Depends(_get_con)):
        items = list_items(con, status=status, limit=500)  # type: ignore[arg-type]
        return JSONResponse([_item_to_dict(i) for i in items])

    @app.get("/api/items/{item_id}")
    def api_item(item_id: int, con=Depends(_get_con)):
        item = get_item(con, item_id)
        if not item:
            raise HTTPException(404, detail="not found")
        return JSONResponse(_item_to_dict(item))

    @app.post("/api/items/{item_id}/review")
    def api_review(item_id: int, con=Depends(_get_con)):
        item = mark_reviewed(con, item_id)
        return JSONResponse(_item_to_dict(item))

    @app.post("/api/items/{item_id}/reject")
    def api_reject(item_id: int, con=Depends(_get_con)):
        item = reject(con, item_id)
        return JSONResponse(_item_to_dict(item))

    @app.post("/api/items/{item_id}/archive")
    def api_archive(item_id: int, con=Depends(_get_con), cfg=Depends(_get_cfg)):
        item = archive(con, cfg, item_id)
        return JSONResponse(_item_to_dict(item))

    return app

