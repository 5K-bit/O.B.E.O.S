from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import JSON, Column, Float, Integer, String, create_engine, event as sqlalchemy_event, func, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import paths
from .events import Event, validate_event_type


Base = declarative_base()
logging.getLogger("chromadb.telemetry.product.posthog").disabled = True

MEMORY_CHAMBER_TABLE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_documents (
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        doc_type TEXT NOT NULL,
        hash TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_ingested_at REAL NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_nodes (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        normalized_label TEXT NOT NULL,
        node_type TEXT NOT NULL,
        zone TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'ready',
        weight REAL NOT NULL DEFAULT 0.0,
        document_count INTEGER NOT NULL DEFAULT 0,
        mention_count INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        provenance_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_seen_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_edges (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 0.0,
        confidence REAL NOT NULL DEFAULT 0.0,
        freshness_s REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        provenance_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_seen_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_document_mentions (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        occurrences INTEGER NOT NULL DEFAULT 0,
        positions_json TEXT NOT NULL DEFAULT '[]',
        weight REAL NOT NULL DEFAULT 0.0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_graph_snapshots (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        source TEXT NOT NULL,
        node_count INTEGER NOT NULL DEFAULT 0,
        edge_count INTEGER NOT NULL DEFAULT 0,
        cluster_count INTEGER NOT NULL DEFAULT 0,
        delta_summary_json TEXT NOT NULL DEFAULT '{}',
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_ingest_runs (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        root_path TEXT NOT NULL,
        document_count INTEGER NOT NULL DEFAULT 0,
        node_count INTEGER NOT NULL DEFAULT 0,
        edge_count INTEGER NOT NULL DEFAULT 0,
        started_at REAL NOT NULL,
        completed_at REAL,
        error TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_agent_artifacts (
        id TEXT PRIMARY KEY,
        agent_name TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        artifact_ref TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        session_id TEXT,
        request_id TEXT,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_sessions (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        last_request_id TEXT,
        summary TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_clusters (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        score REAL NOT NULL DEFAULT 0.0,
        member_count INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_relationship_hints (
        id TEXT PRIMARY KEY,
        source_node_id TEXT NOT NULL,
        target_node_id TEXT NOT NULL,
        hint_type TEXT NOT NULL,
        score REAL NOT NULL DEFAULT 0.0,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'proposed',
        created_by TEXT NOT NULL DEFAULT 'memory_chamber',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_chamber_embeddings (
        id TEXT PRIMARY KEY,
        vector_key TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL DEFAULT 0,
        content_hash TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
)

MEMORY_CHAMBER_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_documents_path ON memory_chamber_documents(path)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_documents_source_kind ON memory_chamber_documents(source_kind)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_documents_hash ON memory_chamber_documents(hash)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_nodes_label ON memory_chamber_nodes(label)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_nodes_normalized_label ON memory_chamber_nodes(normalized_label)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_nodes_type ON memory_chamber_nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_edges_source ON memory_chamber_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_edges_target ON memory_chamber_edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_edges_relation ON memory_chamber_edges(relation)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_mentions_document ON memory_chamber_document_mentions(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_mentions_node ON memory_chamber_document_mentions(node_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_sessions_session_id ON memory_chamber_sessions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_clusters_label ON memory_chamber_clusters(label)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_hints_pair ON memory_chamber_relationship_hints(source_node_id, target_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chamber_embeddings_source ON memory_chamber_embeddings(source_type, source_id)",
)

try:
    from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent

    class BlackfongNullProductTelemetry(ProductTelemetryClient):
        """Offline-first no-op telemetry sink for local Chroma usage."""

        def capture(self, event: ProductTelemetryEvent, *args: Any, **kwargs: Any) -> None:
            return None

except Exception:  # pragma: no cover - Chroma may be optional in some environments
    try:
        from chromadb.config import Component as _ChromaComponent

        class BlackfongNullProductTelemetry(_ChromaComponent):
            def capture(self, event: Any, *args: Any, **kwargs: Any) -> None:
                return None

    except Exception:
        class BlackfongNullProductTelemetry:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def capture(self, event: Any, *args: Any, **kwargs: Any) -> None:
                return None


class EventModel(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True)
    timestamp = Column(Integer, index=True)
    source = Column(String, index=True)
    type = Column(String, index=True)
    payload = Column(JSON)
    confidence = Column(Float)
    event_metadata = Column(JSON, nullable=True)  # metadata layer for multi-agent coordination
    session_id = Column(String, index=True, nullable=True)
    request_id = Column(String, index=True, nullable=True)
    correlation_id = Column(String, index=True, nullable=True)
    hash = Column(String, index=True)


class EventStore:
    """
    SQLite-backed event store.

    This is the canonical storage for normalized events, matching the v0 schema.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or paths.events_db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"timeout": 30.0}, future=True)

        @sqlalchemy_event.listens_for(self.engine, "connect")
        def _set_sqlite_busy_timeout(dbapi_connection, _connection_record) -> None:
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def init_db(self) -> None:
        """
        Initialize schema and enforce immutability at the database level.
        """
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA busy_timeout=30000"))
            try:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logging.getLogger(__name__).warning("sqlite_wal_unavailable: %s", exc)

        Base.metadata.create_all(self.engine)
        self._ensure_trace_indexes()

        # Create triggers that forbid UPDATE and DELETE on the events table.
        with self.engine.begin() as conn:
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS prevent_event_update
                    BEFORE UPDATE ON events
                    BEGIN
                        SELECT RAISE(ABORT, 'events are immutable');
                    END;
                    """
                )
            )
            for statement in MEMORY_CHAMBER_TABLE_SQL:
                conn.execute(text(statement))
            for statement in MEMORY_CHAMBER_INDEX_SQL:
                conn.execute(text(statement))
            try:
                conn.execute(
                    text(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_chamber_documents_fts
                        USING fts5(
                            id UNINDEXED,
                            path,
                            title,
                            content,
                            tokenize='unicode61'
                        );
                        """
                    )
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("sqlite_fts5_unavailable: %s", exc)
            conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS prevent_event_delete
                    BEFORE DELETE ON events
                    BEGIN
                        SELECT RAISE(ABORT, 'events are immutable');
                    END;
                    """
                )
            )

    def _ensure_trace_indexes(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("PRAGMA busy_timeout=30000"))
            existing_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(events)")).fetchall()}
            for column in ("session_id", "request_id", "correlation_id"):
                if column not in existing_columns:
                    conn.execute(text(f"ALTER TABLE events ADD COLUMN {column} TEXT"))

            # Backfill trace columns before the immutable UPDATE trigger is restored.
            conn.execute(text("DROP TRIGGER IF EXISTS prevent_event_update"))
            try:
                for column in ("session_id", "request_id", "correlation_id"):
                    conn.execute(
                        text(
                            f"""
                            UPDATE events
                            SET {column} = json_extract(event_metadata, '$.{column}')
                            WHERE {column} IS NULL
                              AND event_metadata IS NOT NULL
                              AND json_valid(event_metadata)
                              AND json_extract(event_metadata, '$.{column}') IS NOT NULL
                            """
                        )
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning("event_trace_backfill_unavailable: %s", exc)

            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_session_id_timestamp ON events(session_id, timestamp)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_request_id_timestamp ON events(request_id, timestamp)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_correlation_id_timestamp ON events(correlation_id, timestamp)"))

    def session(self) -> Session:
        return self._session_factory()

    def add_event(self, event: Event) -> None:
        # Enforce canonical event type at ingestion time.
        validate_event_type(event.type)
        metadata = event.metadata or {}
        with self.session() as s:
            model = EventModel(
                id=event.id,
                timestamp=event.timestamp,
                source=event.source,
                type=event.type,
                payload=event.payload,
                confidence=event.confidence,
                event_metadata=metadata,
                session_id=metadata.get("session_id"),
                request_id=metadata.get("request_id"),
                correlation_id=metadata.get("correlation_id"),
                hash=event.hash,
            )
            s.add(model)
            s.commit()

    def list_events(
        self,
        *,
        source: Optional[str] = None,
        type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Event]:
        with self.session() as s:
            q = s.query(EventModel).order_by(EventModel.timestamp.desc())
            if source is not None:
                q = q.filter(EventModel.source == source)
            if type is not None:
                q = q.filter(EventModel.type == type)
            q = q.limit(limit)
            rows = q.all()
            return [self._row_to_event(row) for row in rows]

    def recent_events(self, limit: int = 100) -> List[Event]:
        """Return the most recent events regardless of source or type."""
        return self.list_events(limit=limit)

    def list_events_by_source_and_type(
        self, source: str, type: str, limit: int = 100
    ) -> List[Event]:
        """Convenience wrapper filtering both source and type."""
        with self.session() as s:
            q = (
                s.query(EventModel)
                .filter(EventModel.source == source, EventModel.type == type)
                .order_by(EventModel.timestamp.desc())
                .limit(limit)
            )
            rows = q.all()
            return [self._row_to_event(row) for row in rows]

    def list_events_for_session(
        self,
        session_id: str,
        *,
        types: Optional[Iterable[str]] = None,
        limit: int = 1000,
        ascending: bool = True,
    ) -> List[Event]:
        """Return events for a session ordered by timestamp."""
        allowed_types = set(types) if types is not None else None
        with self.session() as s:
            order_clause = EventModel.timestamp.asc() if ascending else EventModel.timestamp.desc()
            q = s.query(EventModel).filter(EventModel.session_id == session_id).order_by(order_clause)
            if allowed_types is not None:
                q = q.filter(EventModel.type.in_(allowed_types))
            rows = q.limit(limit).all()
            return [self._row_to_event(row) for row in rows]

    def list_recent_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return recent session metadata derived from persisted events."""
        sessions: List[Dict[str, Any]] = []
        with self.session() as s:
            last_activity = func.max(EventModel.timestamp).label("last_activity")
            rows = (
                s.query(
                    EventModel.session_id,
                    func.min(EventModel.timestamp).label("start_time"),
                    last_activity,
                    func.count(EventModel.id).label("event_count"),
                )
                .filter(EventModel.session_id.isnot(None), EventModel.session_id != "")
                .group_by(EventModel.session_id)
                .order_by(last_activity.desc())
                .limit(limit)
                .all()
            )
            for row in rows:
                session_id = str(row.session_id)
                recent_rows = (
                    s.query(EventModel)
                    .filter(EventModel.session_id == session_id)
                    .order_by(EventModel.timestamp.desc())
                    .limit(1000)
                    .all()
                )
                agents: set[str] = set()
                preview = ""
                message_count = 0
                for event_row in recent_rows:
                    event = self._row_to_event(event_row)
                    if event.type in {"agent_query", "agent_response"}:
                        message_count += 1
                        if not preview:
                            preview = str(event.payload.get("text") or event.payload.get("response") or "")[:120]
                    if event.type == "agent_response":
                        agents.add(event.source)
                    else:
                        agent_name = event.payload.get("agent")
                        if agent_name:
                            agents.add(str(agent_name))
                sessions.append(
                    {
                        "session_id": session_id,
                        "start_time": int(row.start_time) / 1000.0,
                        "last_activity": int(row.last_activity) / 1000.0,
                        "event_count": int(row.event_count),
                        "message_count": message_count,
                        "agents": sorted(agents),
                        "preview": preview,
                    }
                )
        return sessions

    def list_events_for_request(
        self,
        request_id: str,
        *,
        types: Optional[Iterable[str]] = None,
        limit: int = 1000,
        ascending: bool = True,
    ) -> List[Event]:
        """Return all events associated with a specific request id."""
        allowed_types = set(types) if types is not None else None
        with self.session() as s:
            order_clause = EventModel.timestamp.asc() if ascending else EventModel.timestamp.desc()
            q = s.query(EventModel).filter(EventModel.request_id == request_id).order_by(order_clause)
            if allowed_types is not None:
                q = q.filter(EventModel.type.in_(allowed_types))
            rows = q.limit(limit).all()
            return [self._row_to_event(row) for row in rows]

    def list_events_for_correlation(
        self,
        correlation_id: str,
        *,
        types: Optional[Iterable[str]] = None,
        limit: int = 1000,
        ascending: bool = True,
    ) -> List[Event]:
        """Return all events associated with a workflow correlation id."""
        allowed_types = set(types) if types is not None else None
        with self.session() as s:
            order_clause = EventModel.timestamp.asc() if ascending else EventModel.timestamp.desc()
            q = s.query(EventModel).filter(EventModel.correlation_id == correlation_id).order_by(order_clause)
            if allowed_types is not None:
                q = q.filter(EventModel.type.in_(allowed_types))
            rows = q.limit(limit).all()
            return [self._row_to_event(row) for row in rows]

    def get_event_by_id(self, event_id: str) -> Event | None:
        with self.session() as s:
            row = s.query(EventModel).filter(EventModel.id == event_id).one_or_none()
            if row:
                return self._row_to_event(row)
            return None

    def list_events_around(
        self,
        center_ms: int,
        window_ms: int,
        *,
        source: Optional[str] = None,
        type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Event]:
        start_ms = center_ms - window_ms
        end_ms = center_ms + window_ms
        return self.list_events_between(
            start_ms=start_ms,
            end_ms=end_ms,
            source=source,
            type=type,
            limit=limit,
        )

    def events_around_event(
        self, event_id: str, window_ms: int, limit: int = 1000
    ) -> List[Event]:
        """Return events within window_ms of the given event ID."""
        ev = self.get_event_by_id(event_id)
        if not ev:
            return []
        return self.list_events_around(center_ms=ev.timestamp, window_ms=window_ms, limit=limit)

    def search_payload(self, keyword: str, limit: int = 100) -> List[Event]:
        """Simple keyword search over JSON payload text using LIKE."""
        like_pattern = f"%{keyword}%"
        with self.session() as s:
            q = (
                s.query(EventModel)
                .filter(EventModel.payload.cast(String).like(like_pattern))
                .order_by(EventModel.timestamp.desc())
                .limit(limit)
            )
            rows = q.all()
            return [self._row_to_event(row) for row in rows]

    def events_containing_entity(
        self, entity: str, limit: int = 100
    ) -> List[Event]:
        """Alias for search_payload until a real entity index is available."""
        return self.search_payload(entity, limit=limit)

    def list_events_between(
        self,
        start_ms: int,
        end_ms: int,
        *,
        source: Optional[str] = None,
        type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Event]:
        with self.session() as s:
            q = (
                s.query(EventModel)
                .filter(EventModel.timestamp >= start_ms, EventModel.timestamp <= end_ms)
                .order_by(EventModel.timestamp.asc())
            )
            if source is not None:
                q = q.filter(EventModel.source == source)
            if type is not None:
                q = q.filter(EventModel.type == type)
            q = q.limit(limit)
            rows = q.all()
            return [self._row_to_event(row) for row in rows]

    def list_events_by_type(self, type: str, limit: int = 100) -> List[Event]:
        with self.session() as s:
            q = (
                s.query(EventModel)
                .filter(EventModel.type == type)
                .order_by(EventModel.timestamp.desc())
                .limit(limit)
            )
            rows = q.all()
            return [self._row_to_event(row) for row in rows]

    def count_events(self, *, type: Optional[str] = None, source: Optional[str] = None) -> int:
        """Count events with optional source/type filters."""
        with self.session() as s:
            q = s.query(EventModel)
            if type is not None:
                q = q.filter(EventModel.type == type)
            if source is not None:
                q = q.filter(EventModel.source == source)
            return q.count()

    @staticmethod
    def _row_to_event(row: EventModel) -> Event:
        return Event(
            id=row.id,
            timestamp=row.timestamp,
            source=row.source,  # type: ignore[arg-type]
            type=row.type,
            payload=row.payload or {},
            confidence=row.confidence,
            metadata=row.event_metadata or {"version": "1.0"},
            hash=row.hash,
        )


class VectorStore(ABC):
    """
    Abstract interface for DAISE's vector store.

    Concrete implementations can bind to FAISS, Chroma, Qdrant, etc.,
    but the core system remains agnostic.
    """

    @abstractmethod
    def add(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def upsert(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        self.add(ids, texts, metadatas)


class ChromaVectorStore(VectorStore):
    """
    Chroma-backed vector store. Persistent, offline, default embedding only.

    DAISE depends only on the VectorStore interface; Chroma lives behind it.
    """

    COLLECTION_NAME = "daise_knowledge"

    # HNSW tuning baked in at collection creation. A higher search_ef (vs the
    # Chroma 0.4.x default of 10) is required so that hnswlib can reliably
    # collect ``k`` neighbours for every query vector; without it, certain
    # query embeddings raise "Cannot return the results in a contigious 2D
    # array. Probably ef or M is too small" even though the index is healthy.
    COLLECTION_METADATA: Dict[str, Any] = {
        "description": "DAISE knowledge chunks",
        "hnsw:search_ef": 128,
        "hnsw:construction_ef": 200,
        "hnsw:M": 32,
    }

    def __init__(self, persist_path: Path) -> None:
        import chromadb
        try:
            from chromadb.config import Settings
        except Exception:  # pragma: no cover - compatibility across Chroma builds
            Settings = None

        self._path = persist_path
        self._path.mkdir(parents=True, exist_ok=True)
        client_kwargs: Dict[str, Any] = {"path": str(self._path)}
        if Settings is not None:
            try:
                telemetry_impl = "blackfong_ecosystem.storage.BlackfongNullProductTelemetry"
                client_kwargs["settings"] = Settings(
                    anonymized_telemetry=False,
                    chroma_product_telemetry_impl=telemetry_impl,
                    chroma_telemetry_impl=telemetry_impl,
                )
            except Exception:
                pass
        self._client = chromadb.PersistentClient(**client_kwargs)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata=dict(self.COLLECTION_METADATA),
        )

    def add(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        ids_list = list(ids)
        texts_list = list(texts)
        meta_list = list(metadatas) if metadatas is not None else [{}] * len(ids_list)
        while len(meta_list) < len(ids_list):
            meta_list.append({})
        meta_list = meta_list[: len(ids_list)]
        # Chroma accepts only str, int, float, bool in metadata
        normalized = []
        for m in meta_list:
            normalized.append({k: _scalar_for_chroma(v) for k, v in m.items()})
        self._collection.add(ids=ids_list, documents=texts_list, metadatas=normalized)

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        try:
            result = self._collection.query(
                query_texts=[query], n_results=k, include=["documents", "metadatas", "distances"]
            )
        except RuntimeError as exc:
            # hnswlib can raise "Cannot return the results in a contigious 2D
            # array. Probably ef or M is too small" for an awkward query vector.
            # Retry once with a smaller fan-out; degrade to empty results rather
            # than crashing the librarian / API on a single query.
            logging.getLogger(__name__).warning("chroma_search_runtime_error: %s", exc)
            if k > 1:
                try:
                    result = self._collection.query(
                        query_texts=[query],
                        n_results=max(1, k // 2),
                        include=["documents", "metadatas", "distances"],
                    )
                except Exception:  # noqa: BLE001
                    return []
            else:
                return []
        ids = result["ids"][0] if result["ids"] else []
        documents = result["documents"][0] if result["documents"] else []
        metadatas = result["metadatas"][0] if result["metadatas"] else []
        distances = result["distances"][0] if result["distances"] else []
        results = []
        for iid, doc, meta, dist in zip(ids, documents, metadatas, distances):
            dist_val = float(dist)
            # simple normalization: translate distance to [0,1] relevance
            relevance = 1.0 / (1.0 + dist_val) if dist_val >= 0 else 0.0
            results.append(
                {
                    "id": iid,
                    "text": doc,
                    "metadata": meta or {},
                    "distance": dist_val,
                    "relevance": relevance,
                }
            )
        return results

    def upsert(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        ids_list = list(ids)
        texts_list = list(texts)
        meta_list = list(metadatas) if metadatas is not None else [{}] * len(ids_list)
        while len(meta_list) < len(ids_list):
            meta_list.append({})
        meta_list = meta_list[: len(ids_list)]
        normalized = []
        for metadata in meta_list:
            normalized.append({key: _scalar_for_chroma(value) for key, value in metadata.items()})
        self._collection.upsert(ids=ids_list, documents=texts_list, metadatas=normalized)


def _scalar_for_chroma(value: Any) -> Any:
    """Chroma metadata must be str, int, float, or bool."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if value is None:
        return ""
    return str(value)


class NullVectorStore(VectorStore):
    """
    Placeholder vector store used until a real backend is configured.

    Keeps the system offline and operational while signaling that
    semantic search is not yet wired.
    """

    def add(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        # Intentionally do nothing; this is a stub.
        return None

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        # No results in the null implementation.
        return []

    def upsert(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        return None
