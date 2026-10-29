# OBEOS Earth — Mobility & Presence Update

## Live Transportation, Public Camera, Transit, and Private Location Tracking Layer

## 1. Objective

The Mobility & Presence update expands OBEOS Earth beyond environmental and planetary intelligence.

Its purpose is to represent **movement, transportation, infrastructure, public camera feeds, and authorized people/devices as live spatial objects on the Earth globe**.

This update introduces:

* personal live location tracking
* private family location viewing
* location history
* movement trails
* live road traffic
* traffic incidents
* public traffic cameras
* road construction
* real-time transit vehicles
* transit alerts
* transportation infrastructure
* authorized device presence

This subsystem remains separate from the primary Earth V2 weather, satellite, orbital, seismic, and environmental architecture.

The separation is intentional.

---

# 2. Product Definition

The core Earth V2 system answers:

> What is happening on Earth?

The Mobility & Presence subsystem answers:

> What is moving through Earth, and where are authorized people and devices right now?

Together:

```text
OBEOS EARTH
│
├── Planetary Intelligence
│   ├── Weather
│   ├── Satellite imagery
│   ├── Aircraft
│   ├── Satellites
│   ├── Earthquakes
│   ├── Fires
│   └── Environmental events
│
└── Mobility & Presence
    ├── Personal location
    ├── Device location
    ├── Traffic
    ├── Transit
    ├── Public cameras
    └── Mobility events
```

---

# 3. Core Architecture

```text
                    EXTERNAL SOURCES

  511NY ─────────────────────────────┐
  NYC DOT ───────────────────────────┤
  MTA / GTFS-RT ─────────────────────┤
  Public Transit APIs ───────────────┤
  Public Camera Feeds ───────────────┤
                                     │
                                     ▼
                           ┌──────────────────┐
                           │    EARTHGLASS    │
                           │ Mobility Layer   │
                           ├──────────────────┤
                           │ traffic          │
                           │ transit          │
                           │ cameras          │
                           │ incidents        │
                           │ normalization    │
                           │ caching          │
                           └─────────┬────────┘
                                     │
                                     │
                                     │
      PRIVATE PRESENCE               │
                                     │
 iPhone / GPS device                 │
        │                            │
        ▼                            │
 Traccar Client                      │
        │                            │
        ▼                            │
 Tailscale / HTTPS                   │
        │                            │
        ▼                            │
 ┌──────────────────────┐            │
 │ Private Tracker Node │            │
 │ Traccar Server       │            │
 └──────────┬───────────┘            │
            │                        │
            ▼                        ▼
        PRIVATE PRESENCE API
                 │
                 ▼
           OBEOS EARTH
                 │
                 ▼
      Cesium Mobility Renderer
```

---

# 4. Architectural Rule

Public mobility data and private presence data must not share the same access model.

Public data:

```text
/api/v2/mobility/*
```

Private location:

```text
/private/v2/presence/*
```

This separation is mandatory.

Personal GPS coordinates must never accidentally become available through public EarthGlass endpoints.

---

# 5. Mobility Provider Architecture

Suggested EarthGlass structure:

```text
earthglass/
│
├── mobility/
│   │
│   ├── providers/
│   │   ├── base.py
│   │   │
│   │   ├── traffic/
│   │   │   ├── ny511.py
│   │   │   └── nyc_dot.py
│   │   │
│   │   ├── transit/
│   │   │   ├── mta_gtfs.py
│   │   │   └── gtfs_rt.py
│   │   │
│   │   └── cameras/
│   │       └── public_camera.py
│   │
│   ├── services/
│   │   ├── traffic_service.py
│   │   ├── transit_service.py
│   │   ├── camera_service.py
│   │   └── mobility_cache.py
│   │
│   └── models/
│       ├── vehicle.py
│       ├── camera.py
│       ├── incident.py
│       └── roadway.py
│
└── presence/
    │
    ├── providers/
    │   └── traccar.py
    │
    ├── services/
    │   ├── presence_service.py
    │   ├── history_service.py
    │   ├── permissions.py
    │   └── retention.py
    │
    └── models/
        ├── device.py
        ├── position.py
        ├── trail.py
        └── viewer.py
```

---

# 6. Live Traffic

Initial providers:

* 511NY
* NYC DOT traffic data

Traffic layer should support:

```text
road speed
travel time
traffic congestion
incidents
closures
construction
weather-related road conditions
```

API:

```text
GET /api/v2/mobility/traffic
GET /api/v2/mobility/traffic/status
GET /api/v2/mobility/traffic/incidents
GET /api/v2/mobility/traffic/roads
```

Bounding-box requests should be supported.

Example:

```text
/api/v2/mobility/traffic
    ?west=
    &south=
    &east=
    &north=
```

Do not send the entire regional road network to the frontend when the user is viewing a small area.

---

# 7. Traffic Visualization

Traffic should render using road segments.

Example representation:

```text
FREE FLOW

───────────────

SLOW

━━━━━━━

STOPPED

██████
```

The visual system should be driven by:

```text
speed
expected speed
travel time
incident state
```

The client should not contain provider-specific traffic logic.

EarthGlass normalizes it first.

---

# 8. Traffic Incidents

Normalized incident:

```json
{
  "id": "",
  "type": "collision",
  "severity": "moderate",
  "latitude": 0,
  "longitude": 0,
  "road": "",
  "direction": "",
  "description": "",
  "started_at": "",
  "updated_at": "",
  "expected_end": null
}
```

Possible types:

```text
collision
road closure
disabled vehicle
construction
hazard
police activity
weather condition
special event
```

API:

```text
GET /api/v2/mobility/incidents
GET /api/v2/mobility/incidents/{id}
```

---

# 9. Public Traffic Cameras

Initial provider:

* 511NY

Future providers can be added through adapters.

Normalized camera object:

```json
{
  "id": "",
  "name": "",
  "latitude": 0,
  "longitude": 0,
  "road": "",
  "direction": "",
  "status": "online",
  "image_url": "",
  "video_url": "",
  "updated_at": ""
}
```

API:

```text
GET /api/v2/mobility/cameras
GET /api/v2/mobility/cameras/{camera_id}
GET /api/v2/mobility/cameras/status
```

---

# 10. Camera UI

Cameras appear as globe objects:

```text
📹
```

Clicking one opens:

```text
PUBLIC TRAFFIC CAMERA

Location
Broadway / W 96 St

Direction
Southbound

Status
LIVE

Provider
511NY

Updated
12 sec ago

[ VIEW CAMERA ]
```

The video/image feed opens inside an OBEOS Earth panel where supported.

Do not embed arbitrary discovered webcams.

Only integrate:

* public government feeds
* official transportation feeds
* explicitly authorized private cameras

---

# 11. Transit

Initial provider:

* MTA GTFS-Realtime / Bus Time

Future provider architecture should support generic GTFS-RT.

Supported objects:

```text
bus
train
subway
tram
ferry
```

Normalized vehicle:

```json
{
  "id": "",
  "type": "bus",
  "route": "",
  "trip_id": "",
  "latitude": 0,
  "longitude": 0,
  "bearing": 0,
  "speed": null,
  "destination": "",
  "status": "",
  "updated_at": ""
}
```

API:

```text
GET /api/v2/mobility/transit/vehicles
GET /api/v2/mobility/transit/routes
GET /api/v2/mobility/transit/alerts
GET /api/v2/mobility/transit/status
```

---

# 12. Transit Rendering

At global scale:

```text
do not render individual vehicles
```

Regional scale:

```text
cluster vehicles
```

City scale:

```text
display individual vehicles
```

Street scale:

```text
vehicle marker
route
direction
destination
next stop
```

Example:

```text
🚌 M104

Direction
Northbound

Destination
Harlem

Updated
7 sec ago
```

---

# 13. Private Presence System

The private presence system tracks explicitly authorized devices.

Initial device:

```text
Louis iPhone
```

Future devices may include:

```text
phone
watch
vehicle tracker
laptop
Raspberry Pi
LoRa tracker
drone
other OBEOS devices
```

Presence is not limited to humans.

It represents authenticated movable entities.

---

# 14. Tracking Engine

Recommended tracker:

```text
Traccar
```

Architecture:

```text
iPhone
   │
   ▼
Traccar Client
   │
   ▼
secure network
   │
   ▼
Traccar Server
   │
   ▼
EarthGlass Presence Adapter
   │
   ▼
OBEOS Earth
```

EarthGlass should not replace Traccar.

Traccar solves:

* GPS collection
* background location
* offline buffering
* position history
* device state
* battery reporting
* movement state

EarthGlass consumes and normalizes that information.

---

# 15. Presence Data Model

Normalized position:

```json
{
  "device_id": "",
  "owner_id": "",
  "latitude": 0,
  "longitude": 0,
  "altitude_m": null,
  "accuracy_m": 0,
  "speed_mps": 0,
  "heading_deg": 0,
  "battery_percent": null,
  "moving": true,
  "recorded_at": "",
  "received_at": ""
}
```

---

# 16. Presence API

Private endpoints:

```text
GET /private/v2/presence
GET /private/v2/presence/{device_id}
GET /private/v2/presence/{device_id}/history
GET /private/v2/presence/{device_id}/trail
GET /private/v2/presence/{device_id}/status
```

Administrative endpoints:

```text
POST /private/v2/presence/devices
PATCH /private/v2/presence/devices/{device_id}
DELETE /private/v2/presence/devices/{device_id}
```

---

# 17. Live Presence UI

OBEOS Earth layer:

```text
PRIVATE PRESENCE

[x] My Location
[ ] My Trail
[ ] Location History
[ ] Device Accuracy
```

Marker:

```text
     👤
   LOUIS
```

Inspector:

```text
LIVE PRESENCE

Louis

Status
● ONLINE

Location Updated
8 sec ago

Speed
3.2 mph

Heading
North

Battery
71%

Accuracy
±8 m

[ FOLLOW ]

[ HISTORY ]
```

---

# 18. Follow Mode

Follow mode locks the Cesium camera onto the selected device.

```text
SELECT USER
     │
     ▼
FOLLOW MODE
     │
     ▼
Cesium camera tracks position
```

Modes:

```text
FOLLOW CLOSE
FOLLOW CITY
FOLLOW REGIONAL
```

Optional heading lock:

```text
camera rotates in direction of travel
```

---

# 19. Movement Trail

Recent movement history can render behind a tracked device.

Example:

```text
                ● LIVE
               /
             •
            /
          •
        /
      •
    /
  •
START
```

Trail configuration:

```text
last 15 minutes
last hour
today
custom
off
```

---

# 20. Location History

Presence history integrates with the OBEOS Earth timeline.

Example:

```text
08:00 ─── 10:00 ─── 12:00 ─── 14:00 ─── NOW
              ▲
              │
         selected time
```

When timeline time changes:

```text
Earth imagery
traffic events
weather events
personal location
```

can eventually be reconstructed for the selected time.

---

# 21. Retention

Location history retention must be configurable.

Options:

```text
disabled
24 hours
7 days
30 days
90 days
1 year
custom
```

Default recommendation:

```text
30 days
```

No location history should be permanent by default.

---

# 22. Family Viewer

A family member may receive limited read-only access.

Example role:

```text
FAMILY_VIEWER
```

Permissions:

```text
view current location        YES
view online/offline status   YES
follow location              YES

view history                 OPTIONAL
view battery                 OPTIONAL

modify device                NO
delete history               NO
change permissions           NO
administrative access        NO
```

---

# 23. Authentication

Private presence should require:

```text
OBEOS identity
+
Tailscale identity
+
device authorization
+
viewer permissions
```

No anonymous presence requests.

No public coordinate endpoints.

No shareable permanent public link by default.

---

# 24. Trust Model

Each tracked device must be explicitly registered.

Example:

```json
{
  "device_id": "iphone-louis",
  "owner": "louis",
  "trusted": true,
  "tracking_enabled": true
}
```

Unknown devices are rejected.

---

# 25. Security Boundaries

Presence must operate using a zero-trust model.

Required:

* authenticated requests
* encrypted transport
* device-specific credentials
* viewer authorization
* rate limiting
* audit logs
* minimal data exposure
* history retention limits
* revocable device access

Never expose raw Traccar administration interfaces to general family viewers.

EarthGlass acts as the controlled gateway.

---

# 26. OBEOS Cloud Node

Recommended deployment:

```text
OBEOS CLOUD NODE

Traccar Server
Presence Adapter
Presence Database
Authentication
Tailscale access
```

Reason:

Blackcomputer should not need to remain powered on for live tracking to continue.

Architecture:

```text
Phone
  │
  ▼
OBEOS Cloud Node
  │
  ├── records position
  │
  └── provides private API
          │
          ▼
Blackcomputer
OBEOS Earth
```

---

# 27. Offline Behavior

Phone loses connection:

```text
GPS continues locally
      │
      ▼
positions buffered
      │
connection returns
      │
      ▼
buffer uploaded
```

OBEOS Earth should display:

```text
Louis

STATUS
STALE

Last position
11 minutes ago
```

Never pretend stale coordinates are live.

---

# 28. Presence Status Model

Possible status:

```text
live
recent
stale
offline
unknown
```

Example logic:

```text
0–60 sec
LIVE

1–5 min
RECENT

5–30 min
STALE

30+ min
OFFLINE
```

Thresholds should be configurable.

---

# 29. Update Frequency

Do not attempt continuous one-second GPS reporting.

Recommended operating targets:

```text
moving quickly
15–30 sec

walking
30–60 sec

stationary
5–10 min

offline
buffer locally
```

This preserves battery while maintaining meaningful tracking.

---

# 30. Mobility Layer UI

```text
MOBILITY

TRAFFIC
[x] Traffic Flow
[x] Incidents
[ ] Construction
[ ] Closures

CAMERAS
[x] Traffic Cameras

TRANSIT
[x] Buses
[ ] Trains
[ ] Transit Alerts

PRIVATE
[x] My Location
[ ] My Trail
[ ] History
```

---

# 31. Combined Globe Example

```text
                   🛰
                    \
                     \
              ✈      \
                    EARTH
             🚌       │
                      │
              📹      👤 Louis
                      │
                  ━━━━━━━
                   traffic

          🔥                  ⚠
```

Every object remains independently selectable.

---

# 32. Common Object Inspector

Mobility and presence should use the same inspector architecture as Earth V2.

Base object:

```json
{
  "id": "",
  "object_type": "",
  "latitude": 0,
  "longitude": 0,
  "updated_at": "",
  "source": ""
}
```

Specific data extends the base object.

Supported object types:

```text
aircraft
satellite
bus
train
camera
traffic incident
tracked person
tracked device
earthquake
fire
weather alert
```

---

# 33. Performance Requirements

Mobility data can become extremely dense.

Required:

* bounding-box requests
* clustering
* distance-based visibility
* maximum entity counts
* throttled position updates
* client-side interpolation
* server-side filtering
* provider caching
* WebSocket/SSE only where justified

Do not continuously rebuild every Cesium object.

Update existing entities in place.

---

# 34. Real-Time Transport Strategy

Initial implementation can use polling.

Example:

```text
traffic
30–60 seconds

buses
10–30 seconds

cameras
30–60 seconds

presence
15–60 seconds
```

Later:

```text
EarthGlass
    │
    ▼
SSE / WebSocket
    │
    ▼
OBEOS Earth
```

Do not introduce WebSockets until polling is proven inadequate.

---

# 35. Mobility Health

Add:

```text
GET /api/v2/mobility/status
```

Example:

```json
{
  "traffic": "online",
  "transit": "online",
  "cameras": "degraded",
  "presence": "online"
}
```

Provider-specific health:

```text
GET /api/v2/mobility/providers
```

---

# 36. Provenance

Public mobility responses must retain provider attribution.

Example:

```json
{
  "data": [],
  "provenance": {
    "provider": "511NY",
    "retrieved_at": "",
    "source_updated_at": "",
    "cached": false
  }
}
```

Private presence records should instead record:

```text
device
tracking provider
received timestamp
position timestamp
```

Do not expose sensitive infrastructure details to family viewers.

---

# 37. Implementation Order

## Mobility 1.0 — Foundation

Build:

```text
mobility provider abstraction
normalized mobility models
bounding-box filtering
mobility health
mobility cache
```

---

## Mobility 1.1 — Traffic

Implement:

```text
511NY
NYC DOT
traffic speeds
traffic incidents
closures
construction
```

---

## Mobility 1.2 — Cameras

Implement:

```text
public traffic camera ingestion
camera map markers
camera inspector
embedded camera viewer
provider status
```

---

## Mobility 1.3 — Transit

Implement:

```text
MTA GTFS-RT
vehicle position normalization
bus rendering
route metadata
transit alerts
```

---

## Presence 1.0 — Tracking Backend

Deploy:

```text
Traccar Server
OBEOS Cloud Node
Tailscale
presence adapter
private API
presence database
```

---

## Presence 1.1 — Personal Tracking

Connect:

```text
Louis iPhone
```

Implement:

```text
live location
battery state
speed
heading
accuracy
online status
```

---

## Presence 1.2 — Earth Integration

Implement:

```text
presence marker
object inspector
follow mode
movement trail
```

---

## Presence 1.3 — History

Implement:

```text
location history
timeline integration
retention rules
historical playback
```

---

## Presence 1.4 — Family Viewer

Implement:

```text
viewer accounts
read-only presence permission
current location
follow mode
optional history permission
audit logging
```

---

# 38. Out of Scope

Do not include the following in the initial Mobility & Presence release:

* unauthorized CCTV
* private cameras without owner authorization
* facial recognition against public cameras
* automated tracking of unknown individuals
* scraping arbitrary webcams
* police surveillance feeds requiring restricted access
* hidden tracking
* third-party tracking without consent
* long-term permanent location retention
* real-time rendering of every transportation object globally
* custom iPhone tracking application
* commercial traffic feeds
* autonomous geofencing actions

These would either create unnecessary technical scope, privacy risk, or both.

---

# 39. Future Expansion

Future versions may add:

```text
vehicle GPS
LoRa trackers
Apple Watch / wearable location
drone telemetry
bike/scooter feeds
ferry tracking
rail tracking
ship AIS
parking availability
EV charging availability
weather-aware routing
mobility prediction
geofences
arrival detection
home/away state
OBEOS automation triggers
```

Example:

```text
Louis enters HOME geofence
        │
        ▼
OBEOS event backbone
        │
        ├── update presence
        ├── change HUD
        └── trigger home automation
```

This should come later.

Tracking and visualization must work reliably first.

---

# 40. Definition of Done

Mobility & Presence V1 is complete when:

1. EarthGlass has a dedicated mobility subsystem.
2. Traffic information renders geographically.
3. Traffic incidents render as selectable objects.
4. Public traffic cameras appear on the globe.
5. Camera feeds can be opened from OBEOS Earth.
6. MTA buses render using real-time positions.
7. Transit vehicles update without recreating the entire layer.
8. Traccar runs independently of Blackcomputer.
9. The registered iPhone reports location.
10. EarthGlass exposes the private presence API.
11. OBEOS Earth displays current personal location.
12. Presence state distinguishes live/stale/offline.
13. Follow mode works.
14. Movement trails work.
15. Location history works.
16. Retention is configurable.
17. Family viewer access is read-only.
18. Private coordinates cannot be retrieved anonymously.
19. Provider failure does not crash OBEOS Earth.
20. Cached/degraded state is clearly displayed.

---

# 41. System Identity

The Mobility & Presence update turns OBEOS Earth into more than a planetary viewer.

It establishes OBEOS Earth as a **live spatial operating environment**.

The globe can simultaneously understand:

```text
what the atmosphere is doing
what satellites are doing
what aircraft are doing
what roads are doing
what transit is doing
what environmental events are occurring
where authorized OBEOS devices are
where authorized people are
```

The long-term architecture becomes:

```text
                 OBEOS EARTH

              PLANETARY WORLD MODEL
                       │
        ┌──────────────┼──────────────┐
        │              │              │
    ENVIRONMENT      MOBILITY      PRESENCE
        │              │              │
     weather         traffic         people
     imagery         transit         devices
     hazards         cameras         vehicles
     seismic         aircraft        trackers
     orbital         roads           history
```

**EarthGlass collects and normalizes the world.**

**OBEOS Earth renders and interacts with that world.**

**Presence adds the authorized user into that world model.**
