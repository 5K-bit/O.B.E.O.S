# OBEOS Earth V2 — Live Planetary Intelligence

## 1. Objective

Upgrade OBEOS Earth from a live-weather globe into a **local-first planetary intelligence client** capable of displaying multiple real-world data streams simultaneously.

V2 adds:

* global weather
* satellite imagery
* orbiting satellites
* live aircraft
* earthquakes
* severe weather alerts
* wildfires
* global natural events
* aviation weather
* space weather

OBEOS Earth remains the visualization client.

EarthGlass becomes the unified geospatial intelligence backend.

---

# 2. Architecture

```text
                    EXTERNAL DATA SOURCES

 NOAA GFS ──────────────────────┐
 NOAA GOES ─────────────────────┤
 NOAA NWS ──────────────────────┤
 NOAA Aviation Weather ─────────┤
 NOAA SWPC ─────────────────────┤
 NASA GIBS ─────────────────────┤
 NASA FIRMS ────────────────────┤
 NASA EONET ────────────────────┤
 CelesTrak ─────────────────────┤
 OpenSky ───────────────────────┤
 USGS Earthquakes ──────────────┘
                               │
                               ▼
              ┌─────────────────────────────┐
              │          EARTHGLASS         │
              │ Geospatial Intelligence API │
              ├─────────────────────────────┤
              │ Provider adapters           │
              │ Validation                  │
              │ Normalization               │
              │ Provenance                  │
              │ Caching                     │
              │ Rate-limit protection       │
              │ Offline fallback            │
              │ Health monitoring           │
              └──────────────┬──────────────┘
                             │
                      localhost:8000
                             │
                             ▼
                ┌─────────────────────────┐
                │       OBEOS EARTH       │
                │ Cesium Planetary Client │
                ├─────────────────────────┤
                │ Globe rendering         │
                │ Layer controls          │
                │ Timeline                │
                │ Object tracking         │
                │ Information panels      │
                │ Alerts                  │
                └─────────────────────────┘
```

---

# 3. V2 Core Data Layers

## Layer 1 — Weather

Provider:

* NOAA GFS

Existing functionality remains.

Expand to support:

* temperature
* wind
* precipitation
* pressure
* cloud cover

EarthGlass:

```text
/api/v2/weather/status
/api/v2/weather/layers
/api/v2/weather/temperature
/api/v2/weather/wind
/api/v2/weather/precipitation
```

---

# 4. Satellite Imagery

Providers:

* NASA GIBS
* NOAA GOES

Supported imagery categories:

* true-color Earth
* cloud imagery
* infrared
* water vapor
* aerosols
* snow/ice
* vegetation
* sea-surface products

EarthGlass:

```text
/api/v2/imagery/catalog
/api/v2/imagery/layers
/api/v2/imagery/status
```

Whenever practical, imagery should remain tile-based rather than being downloaded and converted into giant local images.

EarthGlass handles provider metadata and caching.

Cesium handles visualization.

---

# 5. Orbital Satellite Tracking

Provider:

* CelesTrak

Initial groups:

* ISS
* weather satellites
* GPS
* scientific satellites
* Starlink
* OneWeb

EarthGlass periodically downloads orbital elements.

Local SGP4 propagation calculates positions between updates.

```text
CelesTrak
   ↓
TLE / OMM
   ↓
EarthGlass cache
   ↓
SGP4
   ↓
ECEF / lat / lon / altitude
   ↓
OBEOS Earth
```

API:

```text
/api/v2/orbits/groups
/api/v2/orbits/objects
/api/v2/orbits/{norad_id}
/api/v2/orbits/{norad_id}/track
```

Cesium should animate satellites continuously without repeatedly contacting CelesTrak.

---

# 6. Aircraft Tracking

Provider:

* OpenSky Network

Normalize aircraft into:

```json
{
  "icao24": "",
  "callsign": "",
  "latitude": 0,
  "longitude": 0,
  "altitude_m": 0,
  "velocity_mps": 0,
  "heading_deg": 0,
  "vertical_rate_mps": 0,
  "on_ground": false,
  "timestamp": ""
}
```

API:

```text
/api/v2/aircraft
/api/v2/aircraft/{icao24}
/api/v2/aircraft/status
```

Support bounding-box requests:

```text
/api/v2/aircraft?west=&south=&east=&north=
```

This is mandatory.

Do not download every visible aircraft globally when the user is zoomed into New York.

---

# 7. Earthquakes

Provider:

* USGS

EarthGlass endpoint:

```text
/api/v2/seismic/events
/api/v2/seismic/events/{event_id}
/api/v2/seismic/status
```

Filters:

```text
minimum magnitude
maximum age
bounding box
depth
```

Visual representation:

```text
small event      → small pulse
large event      → large pulse

height/depth indicator
magnitude label
age indicator
```

Selecting an event opens:

```text
Magnitude
Location
Depth
UTC/local time
USGS event identifier
```

---

# 8. Severe Weather Alerts

Provider:

* NWS

Endpoint:

```text
/api/v2/alerts/weather
/api/v2/alerts/weather/{alert_id}
```

Display polygons directly on the globe.

Examples:

```text
Tornado Warning
Flash Flood Warning
Severe Thunderstorm Warning
Hurricane Warning
Winter Storm Warning
```

Alert information panel:

```text
Severity
Certainty
Urgency
Effective
Expires
Area
Description
Instructions
Source
```

---

# 9. Wildfire Detection

Provider:

* NASA FIRMS

API:

```text
/api/v2/fires
/api/v2/fires/status
```

Normalized fire record:

```json
{
  "latitude": 0,
  "longitude": 0,
  "brightness": 0,
  "confidence": "",
  "satellite": "",
  "detected_at": "",
  "frp": 0
}
```

Cesium renders detected fire hotspots.

Clustering must be supported when zoomed out.

---

# 10. Natural Events

Provider:

* NASA EONET

Categories:

```text
wildfires
volcanoes
severe storms
floods
sea/lake ice
landslides
dust/haze
snow
```

API:

```text
/api/v2/events
/api/v2/events/categories
/api/v2/events/{event_id}
```

EONET GeoJSON should remain close to its native representation whenever possible.

---

# 11. Aviation Weather

Provider:

* NOAA Aviation Weather

Data:

```text
METAR
TAF
SIGMET
G-AIRMET
PIREP
```

API:

```text
/api/v2/aviation/metar
/api/v2/aviation/taf
/api/v2/aviation/sigmet
/api/v2/aviation/pirep
```

Long-term goal:

Selecting an aircraft can display relevant atmospheric conditions around it.

---

# 12. Space Weather

Provider:

* NOAA SWPC

V2 initial support:

```text
Kp index
geomagnetic storm state
solar wind
space weather alerts
```

API:

```text
/api/v2/space-weather/current
/api/v2/space-weather/alerts
/api/v2/space-weather/history
```

Later versions may drive:

* aurora visualization
* magnetosphere visualization
* solar activity overlays

---

# 13. EarthGlass Provider Architecture

Create a provider abstraction.

Suggested structure:

```text
earthglass/
│
├── providers/
│   ├── base.py
│   ├── noaa/
│   │   ├── gfs.py
│   │   ├── nws.py
│   │   ├── goes.py
│   │   ├── aviation.py
│   │   └── swpc.py
│   │
│   ├── nasa/
│   │   ├── gibs.py
│   │   ├── firms.py
│   │   └── eonet.py
│   │
│   ├── usgs/
│   │   └── earthquakes.py
│   │
│   ├── opensky/
│   │   └── aircraft.py
│   │
│   └── celestrak/
│       └── satellites.py
│
├── services/
│   ├── cache.py
│   ├── scheduler.py
│   ├── provenance.py
│   ├── normalization.py
│   └── health.py
│
└── api/
    └── v2/
```

Every provider implements a common interface:

```python
class Provider:
    name: str

    async def fetch(self):
        ...

    async def health(self):
        ...

    async def normalize(self, payload):
        ...

    async def refresh(self):
        ...
```

---

# 14. Provenance

Every EarthGlass response should expose its origin.

Example:

```json
{
  "data": {},
  "provenance": {
    "provider": "USGS",
    "product": "earthquake-feed",
    "retrieved_at": "2026-09-11T12:00:00Z",
    "source_timestamp": "2026-09-11T11:58:42Z",
    "cache": false
  }
}
```

This fits EarthGlass's existing provenance philosophy and becomes mandatory for V2.

---

# 15. Cache Strategy

EarthGlass must protect upstream services.

```text
Provider
   ↓
Fetcher
   ↓
Validation
   ↓
Local cache
   ↓
Normalized state
   ↓
EarthGlass API
```

If a provider goes offline:

```text
provider unavailable
      ↓
serve last valid cached dataset
      ↓
mark response stale
      ↓
display degraded status
```

OBEOS Earth should continue operating.

Example:

```json
{
  "status": "degraded",
  "provider": "OpenSky",
  "using_cache": true,
  "cache_age_seconds": 480
}
```

---

# 16. EarthGlass System Health

Create:

```text
GET /api/v2/system/providers
```

Response:

```json
[
  {
    "provider": "NOAA_GFS",
    "status": "online",
    "last_success": "",
    "last_attempt": "",
    "cache_age": 0
  },
  {
    "provider": "OPEN_SKY",
    "status": "degraded",
    "last_success": "",
    "last_attempt": "",
    "cache_age": 300
  }
]
```

The client must never silently fail.

---

# 17. OBEOS Earth V2 UI

Main layer panel:

```text
OBEOS EARTH

ATMOSPHERE
[x] Temperature
[ ] Wind
[ ] Precipitation
[ ] Pressure

SATELLITE
[x] True Color
[ ] Infrared
[ ] Water Vapor

TRAFFIC
[x] Aircraft
[x] Satellites

EVENTS
[x] Earthquakes
[ ] Fires
[ ] Natural Events
[ ] Weather Alerts

SPACE
[ ] Space Weather
```

---

# 18. Object Inspector

Clicking anything on the globe opens the same standardized inspector.

```text
OBJECT INSPECTOR

TYPE
Aircraft

CALLSIGN
DAL142

ALTITUDE
34,100 ft

VELOCITY
472 kt

HEADING
083°

SOURCE
OpenSky

UPDATED
14 sec ago
```

Same panel supports:

* aircraft
* satellites
* earthquakes
* fires
* natural events
* weather alerts

---

# 19. Timeline

Introduce a global Earth timeline.

```text
◀──────────────●──────────────▶

-24h        NOW             +24h
```

Different datasets respond differently.

Weather:

```text
past analysis → forecast
```

Satellite imagery:

```text
historical frames
```

Earthquakes/fires/events:

```text
hide events newer than selected timestamp
```

Satellite orbits:

```text
propagate position at selected timestamp
```

This establishes the foundation for full time-machine functionality later.

---

# 20. Performance Requirements

Blackcomputer's Intel UHD graphics means performance discipline is mandatory.

Do not attempt unrestricted rendering of every object.

Required:

* viewport-based queries
* Cesium clustering
* distance-based visibility
* entity limits
* Web Workers where beneficial
* throttled updates
* imagery layer limits
* data simplification
* server-side filtering

Example:

```text
Globe view:
aircraft clusters

Continental view:
regional aircraft

City view:
individual aircraft + labels
```

The same applies to fires and earthquakes.

---

# 21. V2 Implementation Order

## V2.0 — Foundation

Build:

* `/api/v2`
* provider interface
* caching
* provenance
* provider health
* scheduler
* normalized error model

Do this before adding new feeds.

---

## V2.1 — Planetary Tracking

Add:

1. USGS earthquakes
2. CelesTrak satellites
3. OpenSky aircraft

Why first:

All three are point/object datasets and exercise the same Cesium entity architecture.

---

## V2.2 — Satellite Earth

Add:

4. NASA GIBS
5. NOAA GOES

This creates the live-Earth visual layer.

---

## V2.3 — Planetary Events

Add:

6. NWS alerts
7. NASA FIRMS
8. NASA EONET

This establishes real-world event intelligence.

---

## V2.4 — Environmental Intelligence

Add:

9. NOAA Aviation Weather
10. NOAA SWPC

---

# 22. Explicitly Out of Scope

Do not put these into V2:

* military aircraft identification
* paid flight APIs
* paid satellite imagery
* ship tracking/AIS unless a reliable free source is selected later
* global surveillance camera feeds
* unrestricted historical archives
* AI interpretation of every event
* photorealistic atmospheric simulation
* full magnetosphere simulation

Those can become later modules.

The goal is to finish V2 instead of creating an endless integration project.

---

# 23. Definition of Done

OBEOS Earth V2 is complete when:

1. Existing NOAA GFS functionality still works.
2. EarthGlass exposes `/api/v2`.
3. Every provider has health reporting.
4. Every response includes provenance.
5. Cached data survives temporary upstream failures.
6. Aircraft render and move correctly.
7. Satellites orbit using local SGP4 propagation.
8. Earthquakes render globally.
9. NASA satellite imagery renders as a globe layer.
10. GOES imagery can be displayed.
11. Weather warnings render geographically.
12. Active fire detections render.
13. EONET events render.
14. Layer controls can independently enable/disable every system.
15. Clicking an object opens the common inspector.
16. Performance remains usable on Intel UHD hardware.
17. One dead provider cannot crash EarthGlass or OBEOS Earth.
18. Existing offline/degraded behavior remains functional.

---

# 24. Product Definition

V1:

> A local 3D Earth displaying live NOAA weather.

V2:

> A local-first planetary intelligence system combining atmospheric, orbital, aviation, satellite-imagery, seismic, wildfire, hazard, and space-weather information through EarthGlass and presenting it through the OBEOS Earth 3D globe.

That distinction should become the official V2 direction.
