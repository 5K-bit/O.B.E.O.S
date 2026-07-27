"""
Event Queue Abstraction Layer

Queue interface for event processing with retry semantics.
Can be upgraded from SQLite to proper queue systems without changing calling code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from sqlalchemy import JSON, Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import paths
from .events import Event

Base = declarative_base()


class QueueStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMMITTED = "committed"
    FAILED = "failed"


class QueueItem(Base):
    """SQLite-backed queue item for operational state tracking."""
    
    __tablename__ = "event_queue"
    
    id = Column(String, primary_key=True)
    event_id = Column(String, index=True)
    status = Column(String, index=True)  # pending, processing, committed, failed
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)


class EventQueue:
    """
    Queue abstraction for event processing.
    
    Provides:
    - enqueue: add event to queue
    - dequeue: get pending events
    - ack: mark event as processed
    - nack: mark event as failed, prepare for retry
    - retry_failed: process failed events under retry threshold
    """
    
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or paths.data_root / "event_queue.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
    
    def init_db(self) -> None:
        """Initialize queue schema."""
        Base.metadata.create_all(self.engine)
    
    def session(self) -> Session:
        return self._session_factory()
    
    def enqueue(self, event_id: str) -> str:
        """Add event to queue with PENDING status."""
        import uuid
        queue_id = str(uuid.uuid4())
        with self.session() as s:
            item = QueueItem(
                id=queue_id,
                event_id=event_id,
                status=QueueStatus.PENDING.value,
                retry_count=0,
            )
            s.add(item)
            s.commit()
        return queue_id
    
    def dequeue(self, batch_size: int = 10) -> List[str]:
        """Get pending events, mark as PROCESSING."""
        with self.session() as s:
            items = (
                s.query(QueueItem)
                .filter(QueueItem.status == QueueStatus.PENDING.value)
                .limit(batch_size)
                .all()
            )
            for item in items:
                item.status = QueueStatus.PROCESSING.value
            s.commit()
            return [item.event_id for item in items]
    
    def ack(self, event_id: str) -> bool:
        """Mark event as successfully processed."""
        with self.session() as s:
            item = (
                s.query(QueueItem)
                .filter(QueueItem.event_id == event_id, QueueItem.status == QueueStatus.PROCESSING.value)
                .one_or_none()
            )
            if item:
                item.status = QueueStatus.COMMITTED.value
                item.processed_at = datetime.now(tz=timezone.utc)
                s.commit()
                return True
        return False
    
    def nack(self, event_id: str, reason: str = "") -> bool:
        """Mark event as failed, prepare for retry."""
        with self.session() as s:
            item = (
                s.query(QueueItem)
                .filter(QueueItem.event_id == event_id, QueueItem.status == QueueStatus.PROCESSING.value)
                .one_or_none()
            )
            if item:
                item.retry_count += 1
                item.error_message = reason
                if item.retry_count >= item.max_retries:
                    item.status = QueueStatus.FAILED.value
                else:
                    item.status = QueueStatus.PENDING.value
                s.commit()
                return True
        return False
    
    def retry_failed(self) -> List[str]:
        """Get failed events under retry threshold and mark for retry."""
        with self.session() as s:
            items = (
                s.query(QueueItem)
                .filter(
                    QueueItem.status == QueueStatus.FAILED.value,
                    QueueItem.retry_count < QueueItem.max_retries,
                )
                .all()
            )
            for item in items:
                item.status = QueueStatus.PENDING.value
                item.retry_count += 1
            s.commit()
            return [item.event_id for item in items]
    
    def get_status(self, event_id: str) -> Optional[str]:
        """Check queue status of an event."""
        with self.session() as s:
            item = s.query(QueueItem).filter(QueueItem.event_id == event_id).one_or_none()
            if item:
                return item.status
        return None
