# OBEOS Earth implementation architecture

The desktop entry is blackfong_ecosystem.earth.desktop (also reached by desktop_shell.py). It shows a PySide6 window before starting services. The classic desktop remains available via --classic and separate workspaces.

EarthRuntime owns one SpatialEngine, EarthControl, ContextFabric and loopback HTTP service. Earth does not construct or consult a credential broker. QWebEngineView loads only bundled assets from its authenticated local origin. Qt WebChannel and the browser HTTP fallback carry Earth Control v1 requests with the same schema, identity checks and Blackfong policy decision. There is no JavaScript execution command.

D.A.I.S.E.'s spatial workflow uses ContextFabric evidence references and the existing local Ollama backend only on an explicit explanation request. Provider text is evidence, not instructions. Its proposed controls still pass through Blackfong. No OpenAI API or Realtime agent is introduced.

SpatialEngine owns bounded provider workers and retry schedules. EarthGlass has no Cesium dependency. The history database stores normalized observations and a coalesced publication outbox in one transaction; canonical envelope ingestion separately persists its event and delivery intent atomically. The event queue deduplicates legacy event IDs. Saved investigations live outside rolling retention.

Snapshot and polling providers produce the same Observation contract. The standard registry includes local processing and public sources, with optional free-key FIRMS, 511NY and MTA Bus Time adapters disabled by default. AIS remains unregistered. V2 adds nonspatial document products, bounded imagery tiles, local OMM propagation and an isolated private Presence API; see the V2 operator guide. Public camera positions are uncalibrated metadata. The 3000 bridge maps detector events to an operator-configured camera position, not an inferred object's ground location. Roads are an OSM catalog, with no TomTom credential path. The versioned earth.network.set command persists public-update versus offline mode. Offline mode gates provider scheduling, HTTP transport, camera images and remote renderer requests while preserving local perception and history maintenance.

The renderer uses stable entity IDs, bounded visible detail, clustering and explicit render requests. It suspends rendering when hidden. Natural Earth imagery ships with Cesium for offline startup. Anonymous Esri/OSM imagery is available in public-update mode and retains attribution; offline mode forces the bundled overview. Original generic model assets have no God’s Eye third-party asset dependency.

Legacy service extraction preserves imports and method names: memory_persistence, memory_retrieval, daise.report_orchestration and ui.desktop_workers now own cohesive responsibilities behind the existing interfaces.

The loopback server uses an ephemeral native bootstrap bearer, HttpOnly SameSite cookies, origin validation and a strict command allowlist. Cesium's bundled widget dependency requires CSP unsafe-eval; only bundled scripts and Qt's channel script may load. Remote provider text is never evaluated or inserted as HTML. Provider HTTP requests validate public destinations and pin resolved addresses; arbitrary camera image redirects are rejected.

Reference design: https://github.com/bilawalsidhu/gods-eye-view (MIT source; separate dataset/model terms). Qt widget embedding: https://doc.qt.io/qtforpython-6/overviews/qtwebengine-overview.html. Cesium explicit rendering: https://cesium.com/learn/cesiumjs/ref-doc/Scene.html.
