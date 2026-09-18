# Earth integration implementation record

Historical checkpoint from before the V2 and Mobility/Presence specifications. The later user decision allows optional free keys. For current behavior and unfinished acceptance gates, see [V2 acceptance](earth-v2-acceptance.md) and [operator setup](earth-v2-operator.md). The descriptions and test counts below record that earlier checkpoint. The implementation is present in the working repositories. Fixture coverage, live source access, native rendering, and platform acceptance are recorded separately; a passing fixture is not proof of an available feed.

## Implemented

- [x] QuietDrop no-clobber/recoverable operations, queued Qt warmup notifications, cancellation propagation and late-result suppression.
- [x] Canonical component discovery, owned startup/shutdown, lightweight Earth launcher, extracted desktop workers, memory interfaces and D.A.I.S.E. orchestration.
- [x] Atomic event outbox, canonical envelope deduplication, bounded durable delivery for 3000 and Nightwatch.
- [x] Blackforge package validation/build checks, bounded MaxPayne diagnostics and Blackfong-bound remediation grants, isolated Assistant safety repairs.
- [x] Versioned Python/TypeScript spatial contracts, snapshot/stream interfaces, history, quotas, pagination, resumable updates and earthquake compatibility.
- [x] Anonymous public/local catalog: aircraft, public military observations, orbital predictions, earthquakes, weather/temperature, infrastructure, launch sites, bikeshare, road geometry, public cameras and 3000 perception. Keyed AIS/FIRMS adapters are unregistered, and the TomTom credential path is removed.
- [x] Native Earth home, classic workspaces, typed controls, persistent offline/public-update selection, situation projection and explicit on-machine model explanations. Earth has no provider-key setup.
- [x] Incremental contacts, selection, models, trails, follow/orbit/overhead, map selection, annotations, saved investigations, image snapshots and replay.
- [x] Locked Windows/Linux dependency sets, clean wheel builds, installed-package smoke checks and an acceptance workflow.

## Corrections from hands-on desktop acceptance

The original build was not finished when the user first tried it. Online startup still chose the coarse offline basemap. That explained poor detail when zooming. It now chooses detailed imagery, persists map preferences, reports tile failures, and retains an offline fallback. Adaptive rendering restores full resolution when idle. The expensive panel backdrop filter was removed after full-screen WebEngine compositing artifacts on Intel graphics.

Regional layers could wait up to one hour after navigation. They now refresh within bounded navigation intervals; zoom-required errors retry immediately, while upstream failure backoff stays intact. OSM error remarks are no longer counted as successful empty responses. Layers show contacts in the view, key/zoom requirements and coverage. Orbital elements survive restarts through a bounded public-data cache.

Trails previously affected Cesium's entity bounds, displacing the selection/tracking center. Separate trail entities fix this. Incoming observations update the inspector, tracking reports its changing view, reload restores the previous zoom, and repeated camera commands recenter correctly. Search now includes an attributed offline index of 34,135 cities.

## Validation

- Offline-first correction: **120 Earth backend tests passed**, followed by **2 focused local-model/timeout checks**; **13 frontend tests passed**, TypeScript/build passed, and installed wheels verified the 14 anonymous/local providers and offline browser policy. A fresh native offline session rendered the bundled globe and revised controls, with no key dialog or shutdown errors. See [offline acceptance](validation/offline-first-2026-09-11.json).

- Earlier combined EarthGlass, Earth Control, outbox, background delivery and desktop cancellation suite: **117 passed** in the clean locked environment.
- Frontend: **12 passed**, TypeScript and production build passed. Focused source checks found no undefined names or syntax errors.
- The Assistant: **3 current safety tests passed** after applying the isolated changes. Its earlier isolated suite passed 28 tests; pre-existing edits were preserved.
- Earlier affected-component run: 176 passed and one 3000 CLI output-format failure; that failure was fixed and the relevant 49-test follow-up passed. Ten Blackforge generated templates passed isolated build/import checks across the initial and corrected runs.
- Canonical paths and both generated-contract checks passed. Clean installed-wheel results are in [wheel smoke](validation/wheel-smoke.json): bundled assets and offline city search passed, with all 14 anonymous/local providers registered.
- Cached spatial-query p95: **140.05 ms** for 10,000 backend tracks, below the 250 ms target. See [benchmark](validation/spatial-benchmark.json).
- Windows native startup, actual detailed imagery, live aircraft, selection and camera controls were exercised. A maximized 1920×1032 window displayed aircraft, weather samples and infrastructure together. See [native acceptance](validation/native-acceptance-2026-09-11.json), [online zoom metrics](validation/native-online-zoom.json), and [maximized live metrics](validation/native-maximized-live.json). The final latest-build reload retained the view and enabled layers. Save scene persisted a PNG with detailed imagery, contacts and wrapped attribution; the image was visually inspected. The isolated session closed with no shutdown errors. See the [saved native snapshot](validation/native-snapshot-2026-09-11.png) and [final session metrics](validation/native-reload-snapshot-2026-09-11.json).

## Live source evidence and remaining acceptance gates

The [September 11 complete live probe](validation/live-provider-2026-09-11-all.json) returned aircraft, earthquakes, weather/temperature, launch metadata, bikeshare, road catalog and public camera records. Military coverage was valid but empty in the sampled area. A [separate same-day satellite probe](validation/live-provider-2026-09-11.json) returned 5,000 predictions; a later request was denied. OSM availability varied; error remarks are now rejected correctly and native regional ingestion is checked separately. Records from earlier probes remain available in this directory.

- [ ] Future anonymous public alternatives for vessel/fire coverage, additional transit feeds, and regional offline map/timetable packs will be reviewed separately. Credential setup is no longer an acceptance gate.
- [ ] The 3000 bridge requires an operator-configured camera location and a running publisher for live perception acceptance. Earth does not activate cameras itself.
- [ ] Kubuntu GUI/GPU, multiple display-scaling settings and the remote CI run still require those environments. The Linux dependency lock resolves; this is not a Kubuntu GUI pass.
- [ ] The approximately 30 FPS target must be assessed with the recorded window size, adaptive resolution, visible-detail budget and machine load. An offline 10,000-track run reached median 33.2 ms / p95 36.7 ms at a 750×443 framebuffer. A maximized live run reached median 33.3 ms / p95 39.6 ms at a 960×472 framebuffer and 0.5 adaptive resolution, with no shutdown errors. Neither is a native-resolution 1080p pass.

## Ownership and scope

EarthGlass owns acquisition, normalization and queries; OBEOS Earth owns presentation; 3000 owns perception; D.A.I.S.E. explains and suggests; Blackfong authorizes commands. Earth does not read or request provider credentials. Local model routing is unchanged. No OpenAI API or Realtime agent was added. BlackRelay and PYBUS have explicit scaffold contracts, not replacement product implementations.

Rolling history is seven days within a 5 GiB quota; saved investigations are separate. Cameras expose metadata and supported periodic stills. Satellite positions are predictions; weather uses nine model samples; launch records identify scheduled sites. Global photorealistic building meshes and paid imagery services are not enabled. No God's Eye third-party model assets were copied.

See [launcher and operator guide](earth-desktop.md) and [architecture](earth-architecture.md). Changes remain available for review in the root and component working trees; no remote publication is implied.
