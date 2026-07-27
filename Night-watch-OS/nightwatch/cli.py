from __future__ import annotations

import argparse
import sys

import uvicorn

from nightwatch.app import create_app
from nightwatch.config import get_settings
from nightwatch.db import SessionLocal, init_db
from nightwatch.services import end_shift, get_active_shift, list_tasks_for_active_shift, start_shift
from nightwatch.system_watch import read_system_snapshot


def _print(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def cmd_status(_: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        s = get_active_shift(db)
        tasks = list_tasks_for_active_shift(db)

    sysinfo = read_system_snapshot()

    if s:
        _print(f"shift: active (id={s.id}) started_at={s.started_at.isoformat()}")
    else:
        _print("shift: none")

    done = sum(1 for t in tasks if t.completed_at is not None)
    _print(f"tasks: {len(tasks)} total / {done} done")
    _print(
        "system: "
        f"cpu={sysinfo['cpu_percent']:.0f}% "
        f"ram={sysinfo['ram_percent']:.0f}% "
        f"disk={sysinfo['disk_percent']:.0f}% "
        f"net={'UP' if sysinfo['network_up'] else 'DOWN'}"
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    s = get_settings()
    host = args.host or s.host
    port = args.port or s.port
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


def cmd_start_shift(_: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        s, carried, already = start_shift(db)
    if already:
        _print(f"shift already active: id={s.id}")
    else:
        _print(f"shift started: id={s.id} carried={carried}")
    return 0


def cmd_end_shift(_: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        s = end_shift(db)
    if not s:
        _print("no active shift")
        return 1
    _print(f"shift ended: id={s.id} ended_at={s.ended_at.isoformat() if s.ended_at else ''}")
    return 0


def cmd_tasks(_: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        from nightwatch.services import get_active_shift

        s = get_active_shift(db)
        tasks = list_tasks_for_active_shift(db)
    if not s:
        _print("no active shift")
        return 1
    if not tasks:
        _print("no tasks")
        return 0
    # oldest first for reading
    for t in reversed(tasks):
        mark = "x" if t.completed_at else " "
        _print(f"[{mark}] {t.id} {t.title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nightwatch", add_help=True)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="run the local dashboard server")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", default=None, type=int)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("status", help="show current state")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("start-shift", help="start a shift (carry unfinished tasks)")
    sp.set_defaults(func=cmd_start_shift)

    sp = sub.add_parser("end-shift", help="end the active shift")
    sp.set_defaults(func=cmd_end_shift)

    sp = sub.add_parser("tasks", help="list tasks for the active shift")
    sp.set_defaults(func=cmd_tasks)

    return p

