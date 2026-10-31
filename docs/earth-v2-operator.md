# Earth V2 operator guide

Status: native offline workflow verified; broader release acceptance gates remain open, 2026-09-12. The original requests are preserved in [Earth V2](specifications/earth-v2.md) and [Mobility & Presence](specifications/mobility-presence-v1.md).

## Decisions retained from this task

- Earth remains local first. No provider key, cloud service, or language model is required to open the workspace.
- The user permits optional free accounts/API keys for named public providers. NASA FIRMS, 511NY and MTA Bus Time are registered but disabled by default. Paid integrations remain excluded.
- Cloud Node, Tailscale and device enrollment will be configured by the user later. No host has been deployed and no phone, camera or location tracking has been activated.
- GPT-6 Astra is the coding assistant only; OBEOS retains its on-machine model architecture.

## Launch and offline data

Run the canonical launcher from the OBEOS checkout:

    python launch_obeos.py --offline --maximized

Use the locked desktop environment created by tools/bootstrap_earth.py. The offline preference persists. The bundled globe is an overview, not street imagery or a detailed building model. Its blur when closely zoomed is a resolution limit. Imported routes/stops and retained observations provide useful geographic detail independently of the basemap. A language model cannot supply missing map tiles, live camera frames or actual vehicle fixes.

The standard catalog now has 50 provider adapters across the existing layers and V2 domains. Expand a layer for its source controls, coverage, attribution and health. Individual source choices survive restart. Offline mode blocks public acquisition and external map/camera requests; local SGP4, imported transit, queries and configured 3000 processing still work.

Under **Transit desk → Import routes for offline use**, select an official GTFS ZIP, provide agency ID, source URL and attribution, and import it. The desktop upload limit is 12 MiB. The CLI accepts archives up to 64 MiB:

    python -m earthglass.mobility.import_cli path/to/gtfs.zip --data-dir path/to/earth-data --agency mta_manhattan --source-url https://www.mta.info/developers --attribution "Data obtained from MTA; stored locally by OBEOS"

The data directory is the Earth runtime directory, not its history subdirectory. The importer maintains agency catalogs under history/provider-cache/transit. It imports routes, shapes and stops; it does not invent arrivals or detours from timetables. A verified official Manhattan archive contained 307 routes, 158 mapped route/shape objects and 1,824 stops. Some route definitions have no geometry.

MTA data is cached locally, with fix timestamps and a warning when transit reports are older than one minute. Only records containing actual coordinates become vehicle markers. Station-only subway reports remain trip/service information. Marker animation blends two recent fixes for two seconds and does not extrapolate beyond the new report. See the [MTA developer terms](https://www.mta.info/developers/terms-and-conditions).

## Planetary layers

| Product | Local behavior and limitation |
|---|---|
| NOAA GFS fields via Open-Meteo | Temperature, wind, precipitation, pressure and cloud samples; hourly retained model output follows the timeline. Nine samples across the view, not a continuous high-resolution field. |
| NASA GIBS / NOAA GOES | Eight curated imagery categories; two overlays maximum with opacity controls; tiles cached within 128 MiB. Requested times resolve to a source frame at or before that time. Uncached offline tiles remain unavailable. |
| CelesTrak | ISS/stations, weather, GPS, science, Starlink and OneWeb; bounded OMM caches, six-hour download cadence, local SGP4. Prediction stops beyond seven days from element epoch. |
| NWS / AWC / EONET | Native lines/polygons retained with bounded geometry; expiration and observation times stay explicit. NWS zone geometry is resolved incrementally, so initial coverage can be partial. |
| NOAA SWPC | Nonspatial Kp, reported geomagnetic scale, solar wind and alerts; each subproduct keeps its own retrieval time during partial failure. |
| USGS / FIRMS | Earthquakes retain depth separately from altitude. FIRMS requires an optional free map key. |
| Public aircraft | Existing public ADS-B primary source and OpenSky fallback retain separate attribution/retention requirements. Not complete air traffic coverage. |

NASA imagery layer IDs were checked against the [official GIBS catalog](https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml). GOES East is regional. Cached imagery availability does not imply a current cloud-free frame.

## Optional public mobility

NYC DOT sensor segments preserve source timestamps and raw speed/travel-time values. The reviewed [official metadata](https://www.nyc.gov/html/dot/downloads/pdf/metadata-trafficspeeds.pdf) does not establish their units or expected-speed baseline. Normalized speed, travel-time and congestion values remain unknown until that contract is confirmed. Zero or missing readings are never represented as stopped traffic.

511NY incidents, cameras and winter-road conditions use an optional free developer key. Current official endpoints use v2 for events/cameras and v3 for winter roads. Refreshes are bounded and share the source quota. See the [511NY API documentation](https://511ny.org/developers/doc).

Camera metadata links to the official player where inline playback is unsupported. Existing TfL and Caltrans adapters support periodic public still images. No arbitrary discovered webcams are embedded. No live frame is fabricated offline.

Use the native **Optional free sources** action to store a free key through the OS credential broker. Only credential references are written to configuration JSON. Existing secrets are not printed or copied into frontend assets.

## Private Presence — later setup

The local API, Traccar normalizer, private database, owner controls and read-only viewer are implemented. Tracking defaults to disabled globally and per device. Current positions, optional history and battery permissions are checked independently for each viewer. Tokens are hashed at rest, expiring and revocable; private coordinates do not enter the public spatial history, event backbone, investigations or screenshots. Hide Presence before saving a public Earth scene.

The local panel provides device selection, accuracy circles, close/city/regional follow, trails and timeline history. Retention choices include off, 1/7/30/90/365 days and a custom value up to 3,650 days; default 30 days. Turning history off retains only the latest position for at most 24 hours. The position-count quota refuses new history instead of silently deleting saved records. Expired locations are pruned.

A separate private-only gateway and viewer are ready for the user's future host setup:

    python -m earthglass.presence.service --data-dir path/to/private-gateway --create-owner-token
    python -m earthglass.presence.service --data-dir path/to/private-gateway

The first command is a later operator action: it displays one expiring owner token once and does not enable tracking. Store it securely. The service binds only to 127.0.0.1:8766 and requires HTTPS from its trusted loopback proxy. Its root serves the bundled private viewer; /private/v2/presence contains its API. It has no public observation API. The viewer sends its credential only to private Presence routes, keeps it in memory, and uses local map assets.

For later deployment, use a private HTTPS proxy such as [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve), with tailnet grants restricted to the owner and selected family viewers. Do not expose the gateway through a public forwarding service. Tailnet identity is enforced by the private network; the application separately enforces its owner/device/viewer credentials. It does not trust a browser-supplied identity header.

Register a device with the Traccar server's numeric device ID, then explicitly enable both global ingestion and that device. Configure Traccar JSON forwarding to the private gateway's /private/v2/presence/ingest/traccar route. Use a dedicated per-device Authorization bearer credential, with bounded retry buffering. Traccar supports device-specific forward URLs and forwarding headers; check the installed version's [forwarding documentation](https://www.traccar.org/forward/). Do not place the token in the URL. A single mixed-device forwarding credential is intentionally rejected by the device-binding check.

Issue family grants only for chosen devices, with history and battery off unless explicitly granted. Family viewers cannot register/delete devices or change access. Use the private gateway URL in a browser for remote viewing. The native desktop's local private store is separate: automatic cloud-to-desktop synchronization is not configured. Host identity, network grants, phone permissions and end-to-end device/family tests remain deferred.

## Acceptance

See [V2 acceptance](earth-v2-acceptance.md) and its machine-readable record. Deterministic tests, real public-source responses, browser rendering and physical desktop acceptance are separate checks. A key-dependent adapter is not described as live verified until its source is actually configured.
