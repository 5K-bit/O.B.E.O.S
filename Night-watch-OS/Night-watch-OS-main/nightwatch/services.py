from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nightwatch.models import Shift, Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_active_shift(db: Session) -> Shift | None:
    return (
        db.execute(select(Shift).where(Shift.ended_at.is_(None)).order_by(Shift.id.desc()))
        .scalars()
        .first()
    )


def start_shift(db: Session) -> tuple[Shift, int, bool]:
    """
    Returns (shift, carried_task_count, already_active).
    """
    active = get_active_shift(db)
    if active:
        return active, 0, True

    s = Shift(started_at=_utcnow(), ended_at=None, notes="")
    db.add(s)
    db.flush()

    carried = db.execute(
        update(Task)
        .where(Task.completed_at.is_(None))
        .values(shift_id=s.id)
        .execution_options(synchronize_session="fetch")
    )
    db.commit()
    db.refresh(s)
    return s, int(getattr(carried, "rowcount", 0) or 0), False


def end_shift(db: Session) -> Shift | None:
    active = get_active_shift(db)
    if not active:
        return None
    active.ended_at = _utcnow()
    db.add(active)
    db.commit()
    db.refresh(active)
    return active


def set_shift_notes(db: Session, shift_id: int, notes: str) -> Shift | None:
    s = db.get(Shift, shift_id)
    if not s:
        return None
    s.notes = notes
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def list_tasks_for_active_shift(db: Session) -> list[Task]:
    active = get_active_shift(db)
    if not active:
        return []
    return (
        db.execute(select(Task).where(Task.shift_id == active.id).order_by(Task.id.desc()))
        .scalars()
        .all()
    )


def add_task(db: Session, title: str) -> Task:
    active = get_active_shift(db)
    t = Task(title=title.strip(), shift_id=active.id if active else None, completed_at=None)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def complete_task(db: Session, task_id: int) -> Task | None:
    t = db.get(Task, task_id)
    if not t:
        return None
    t.completed_at = _utcnow()
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def reopen_task(db: Session, task_id: int) -> Task | None:
    t = db.get(Task, task_id)
    if not t:
        return None
    t.completed_at = None
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def delete_task(db: Session, task_id: int) -> bool:
    t = db.get(Task, task_id)
    if not t:
        return False
    db.delete(t)
    db.commit()
    return True

