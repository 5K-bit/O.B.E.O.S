# Running and validating OBEOS Earth

Earth is the native home screen. The existing workspaces remain available from the top toolbar and with `--classic`. Local model routing is unchanged; GPT-6 Astra is the coding assistant only. No OpenAI API key or Realtime agent is required.

Current V2 behavior and the deferred Cloud Node/Presence setup are documented in [the V2 operator guide](earth-v2-operator.md). Optional free keys for FIRMS, 511NY and MTA Bus Time are permitted; they are never required for offline startup.

## Source checkout

Use Python 3.11, Node.js 24 and pnpm 11.19.0. Initialize the pinned component repositories, including your private EarthGlass checkout. From the OBEOS root:

```text
python tools/bootstrap_earth.py
python launch_obeos.py
```

Bootstrap creates `.venv-earth`, installs the hash-locked Windows or Linux dependency set, installs the two local packages without resolving new dependencies, and builds the bundled client. It does not start providers, indexing, cameras or models. The launcher prefers this environment, then the existing `.venv`.

```text
python launch_obeos.py --offline
python launch_obeos.py --classic
python launch_obeos.py --offline --isolated --data-dir artifacts/my-review
```

`--isolated` disconnects the event/memory backbone. Use a separate data directory for fixtures; never seed simulated tracks into operational history. `--offline` starts with public acquisition blocked while local perception, history, search, saved scenes and local explanations stay available. Online mode now defaults to detailed Esri imagery. The selected basemap is saved through Earth Control. Offline mode forces the bundled Natural Earth overview, with limited regional detail; imagery failures show a fallback notice and a retry control. OSM street tiles are requested only for the visible view, with application identification, normal HTTP caching, a referer, and visible attribution.

Linux requires glibc 2.34 or later and a working graphical Qt/WebEngine environment. Earth does not require a credential store, account, or provider key. The Linux dependency lock resolves, but Kubuntu desktop acceptance must be run on Kubuntu. No platform result is inferred from Windows tests.

## State and ownership

The native window appears before the runtime starts. EarthRuntime owns its scheduler, HTTP service, provider connections, history database and event delivery. Shutdown cancels local explanations, closes SSE connections, stops workers and releases its OS file lock. Only one runtime may own a data directory. Classic workspaces opened from the toolbar are separate, independently owned windows.

The runtime directory contains observation history, saved scenes/investigations, watched areas, camera-location configuration, a network preference and a numeric runtime validation record. Named optional public providers use the OS-protected credential broker. Configuration contains credential references, never secret values. Presence has a separate private database and access model.

Seven-day rolling history defaults to a 5 GiB quota. The payload budget reserves space for SQLite indexes and WAL; database page limits provide an additional bound. Up to ten watched areas rotate through polling schedules. Navigation refreshes are limited to one per provider per 30 seconds (or its shorter normal interval), and global catalogs use their shared response cache. Upstream errors retain their retry backoff; a zoom-required error retries when the view changes. Investigations are stored separately and retain up to 5,000 observations from a captured view; truncation is exposed by the saved page cursor. Saving an investigation again preserves its archived evidence. Provider-restricted transient traffic is excluded from disk history, backbone events and saved investigations.

## Offline operation and optional public updates

The local workspace is the foundation in both modes. With **Public updates · optional free keys**, enabled public layers and visible online map tiles refresh when their sources are reachable. Most sources work without an account; named optional feeds require their free keys. Each source reports its own health; a connection to the local server does not imply an available internet feed. Failed map tiles fall back to the bundled globe and retry at bounded intervals.

Select **Offline · local only** to block public provider and camera-image requests and remote map tiles. The preference persists across restarts; the renderer reloads to apply the matching browser policy. The native request boundary also blocks new remote renderer requests immediately. In-flight provider results are discarded after the switch. Local 3000 processing and event delivery keep running. Returning to public updates resumes enabled feeds with their source quotas/backoff.

The bundled globe, 34,135-city search, original entity models, annotations, retained observations and saved investigations work without internet. D.A.I.S.E. uses an already-downloaded model through an on-machine Ollama service, without proxy routing or a cloud endpoint; it never downloads a model at startup. If that service/model is absent, factual scene summaries still work.

Offline camera locations can be inspected, but remote camera frames are unavailable. Cached road geometry and bikeshare station data remain usable with original timestamps; old availability or movement is not live. Rolling observations still expire after seven days or under the quota. Use **Save scene** to preserve an area as an investigation beyond rolling retention (up to 5,000 observations; truncation is exposed).

Offline GTFS route and stop imports are available through Transit desk and the import CLI. Detailed regional basemap packs and timetable/arrival reconstruction remain future work. Online tile services are not bulk-downloaded for offline use.

## Providers and coverage

The standard catalog contains 50 local/public adapters. FIRMS, 511NY and MTA Bus Time use optional free keys and start disabled. AISStream and TomTom remain excluded. Expand a layer for attribution, refresh health, coverage and source-specific switches. The V2 guide lists the added planetary and mobility layers.

| Layer | Initial source | Meaning and access |
|---|---|---|
| Aircraft | adsb.lol; OpenSky fallback | Regional public ADS-B, incomplete coverage |
| Satellites | CelesTrak + local SGP4 | Predicted positions from orbital elements; first 5,000 catalog records |
| Earthquakes | USGS | Observed events; depth is distinct from altitude |
| Weather / temperature | Open-Meteo NOAA GFS | Nine model samples across the selected area, not station observations or continuous uniform conditions |
| Infrastructure | OpenStreetMap / Overpass | Catalog geometry; regional query limits apply |
| Launches | Launch Library 2 | Scheduled launch-site metadata, not live ascent telemetry |
| Bikeshare | Citi Bike GBFS | NYC/NJ station availability |
| Road network | OpenStreetMap / Overpass | Stored catalog geometry; no live congestion or transit arrival claims |
| Public cameras | TfL, Caltrans districts 3/4 | Metadata and supported periodic still images; pose is uncalibrated |
| Local perception | 3000 canonical events | Operator-configured camera position; object ground position remains unknown |

Use **Camera locations** to associate a 3000 camera ID with a latitude/longitude. Earth never activates a camera automatically. Configure the 3000 publisher to send canonical motion events to the existing OBEOS event API before enabling perception.

The source adapters are independent of Cesium. Provider errors, malformed records, quota delays and recovery remain visible. Shared HTTP clients enforce HTTPS, public-address DNS pinning, request deadlines, response limits and bounded caching. Providers with configured alternative sources fail over with attribution intact.

## Controls and presentation

The same versioned, validated Earth Control contract is used by the Qt WebChannel and authenticated browser fallback. D.A.I.S.E. proposals pass through Blackfong policy before changing the scene. Arbitrary JavaScript execution is not exposed. A local explanation uses the configured on-machine model only when requested; evidence summaries remain available if it fails.

Controls include offline search across 34,135 GeoNames cities (CC BY 4.0), local contacts and coordinates; layer enablement, nearby contacts, selection, tracking, follow/orbit/overhead/reset, annotations, scene descriptions, snapshots, saved-view restoration, watched areas and replay. A paused replay rejects late playback advances. Snapshot pagination is anchored to a fixed database boundary, and SSE resumes from the first page's sequence. Old event cursors require a new snapshot.

The renderer retains a bounded contact index, updates entities incrementally, limits models and labels, and adjusts resolution and visible detail from measured frame times. When camera motion stops it restores full resolution for sharp static imagery. Trails are separate scene objects so their bounds do not pull the tracking camera away from a contact. Hiding the page pauses rendering. Use **Reload Earth** after a renderer failure; scene state is retained. The browser button issues a short-lived, single-use connection ticket.

## Reproducible checks

```text
python tools/validate_ecosystem_paths.py
python tools/export_earth_control_contracts.py --check
python EarthGlass/tools/export_spatial_contracts.py --check
python -m pytest tests/test_earth_control.py tests/test_event_outbox.py tests/test_background_delivery.py tests/test_desktop_cancellation.py EarthGlass/tests
pnpm --dir EarthGlass/obeos-earth test
python EarthGlass/tools/benchmark_spatial.py
python EarthGlass/tools/verify_live_spatial.py
python tools/build_earth_release.py
```

Run commands with the selected desktop environment's Python. Release builds use clean staging trees so stale build files cannot enter wheels. `EARTHGLASS_BUNDLE_DESKTOP=1` selects the prebuilt desktop assets for an EarthGlass wheel; ordinary library wheels remain headless. Installed desktop wheels use their packaged assets.

For native stress testing, run `python tools/seed_earth_probe.py artifacts/new-empty-probe`, then launch with `--offline --isolated --data-dir artifacts/new-empty-probe`. The fixture explicitly labels 10,000 tracks as simulated. Read the recorded runtime metrics after closing the window. Do not compare a busy-machine run to an unloaded run without recording that difference.

## Attribution and distribution

God's Eye View is an implementation reference; its OpenAI Realtime agent is excluded. Its MIT source license does not grant rights to every dataset or model. No God's Eye 3D model assets are imported. The bundled aircraft, vessel and satellite meshes are original procedural geometry generated for OBEOS. Cesium's license and third-party notices ship with the bundle. Dataset attribution remains visible and is included in scene image exports. Review each provider's usage terms for your distribution and intended workload.

See [architecture](earth-architecture.md), [implementation record](earth-implementation-status.md) and the JSON records in [validation](validation/).

The manual **Earth desktop acceptance** workflow requires repository read access to all pinned components; configure the existing GitHub Actions secret `ECOSYSTEM_READ_TOKEN` when the default token cannot read private EarthGlass. Its automated checks do not replace physical Windows/Kubuntu GPU, scaling and keyboard acceptance.

Use `python launch_obeos.py --maximized` for a full-screen workspace. The last view and basemap survive reload; pending entity-camera commands are applied after their contact loads. Repeated Home/Go commands recenter even after manual panning.

Public CelesTrak orbital elements are cached on disk for six hours across restarts. In-process source failures can use retained elements for at most seven days, visibly marked degraded; prediction metadata includes element retrieval time and epoch. No orbital positions are presented as direct observations.

City search data and its attribution ship in the EarthGlass wheel. `EarthGlass/tools/build_place_index.py` rebuilds the bounded local index from GeoNames; the compressed asset records the source archive hash and retrieval time. It is not a street-address geocoder.
