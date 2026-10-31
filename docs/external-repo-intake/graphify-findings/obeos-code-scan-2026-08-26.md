# O.B.E.O.S Graphify Code Scan

## Scope

- Date: 2026-08-26
- Graphify source: `https://github.com/5K-bit/graphify`, branch `v8`, commit `b2cd36267456c166788c95be6e68574064a92a42`
- O.B.E.O.S baseline: `307b68cb5aac71a39347a277fa0df65cee5bbe19`
- Mode: `graphify extract <obeos-root> --code-only --out <review-root> --max-workers 4`
- Follow-up: `graphify cluster-only <review-root> --no-label --no-viz`
- Network and model use: none after the Graphify source clone. Code was parsed locally with tree-sitter AST.

This is a navigation and integration-boundary review, not an authorization to add
Graphify to the O.B.E.O.S runtime.

## Corpus Result

| Measure | Result |
| --- | --- |
| Supported code files parsed | 397 |
| Non-code files skipped | 141 (76 docs, 65 images) |
| Unclassified files skipped | 37 |
| Graph nodes | 5,773 |
| Graph links | 15,336 |
| Communities after clustering | 247 |
| Edge origin | 96% extracted, 4% inferred |
| Inferred-edge average confidence | 0.93 |
| Model token cost | 0 input, 0 output |

One SQL source file did not contribute graph data because the optional
`tree_sitter_sql` dependency was not installed. This is immaterial to the first
architecture pass, but a future database-schema review should install Graphify's
`sql` extra and rescan.

## Confirmed Architectural Anchors

The following findings are based on explicit AST extraction and direct inspection
of the corresponding O.B.E.O.S sources:

| Anchor | Graph evidence | Integration implication |
| --- | --- | --- |
| `EventStore` | 216 connected links; used by the device registry, HUD dashboard, D.A.I.S.E default construction, and Event V1 ingestion | Keep external integrations event-backed and retain O.B.E.O.S storage/history as the source of truth. |
| `WebDashboard` | 135 connected links in `blackfong_ecosystem/operational_interfaces.py` | A future Graphify result belongs behind an explicit HUD/API route, never inside a renderer or page-load path. |
| `DAISE` | 93 connected links in `blackfong_ecosystem/daise/core.py` | Graph data can be optional, bounded context; Graphify must not replace D.A.I.S.E reasoning, identity, or final response ownership. |
| `DeviceRegistryService` | 37 connected links in `blackfong_ecosystem/device_registry.py` | The Legion/Sentinel cockpit is already a coherent boundary and should remain independent of analysis tooling. |
| `LegionAuthService` | 18 connected links in `blackfong_ecosystem/device_registry.py` | Pairing/session controls remain authoritative; Graphify does not need Legion credentials or browser access. |
| `EventEnvelope` | 13 connected links in `blackfong_ecosystem/contracts/event_v1.py` | Any later scan lifecycle must use namespaced Event Contract v1 events. |
| `ComponentRegistry` | 10 connected links in `blackfong_ecosystem/component_registry.py` | A runtime adoption needs a separate manifest-driven component, rather than code copied into the core package. |

Graphify also isolated the local Legion stack into one community containing
`DeviceRegistryService`, `LegionAuthService`, Legion CLI commands, the dashboard
session helpers, the PWA assets, startup/autostart helpers, and Legion tests. That
supports keeping the current cockpit stable while analysis work happens elsewhere.

## Structural Observations

- The most connected system nodes are `ConversationMemoryService` (227 links),
  `EventStore` (216), `MemoryChamberService` (136), `WebDashboard` (135),
  `JARVISAgent` (97), and `DAISE` (93).
- The graph reported two three-file import cycles under
  `blackfong_ecosystem/knowledge_intel/`: `__init__.py -> gaps.py ->
  graph_producer.py -> __init__.py` and `__init__.py -> gaps.py -> timeline.py ->
  __init__.py`.
- These cycles are review leads, not failures from this scan. They should be
  checked only when that package is next edited; this intake phase makes no
  production-code change.

## Decision

Graphify is a strong fit as an offline developer and architecture-review tool. It
should stay outside the O.B.E.O.S runtime for now. The smallest possible future
runtime experiment would be an on-demand, manifest-backed worker that scans an
explicitly approved path, writes results to an approved artifact location, exposes
only a status/report reference to the HUD, and emits:

- `graphify.scan.started`
- `graphify.scan.completed`
- `graphify.scan.failed`

That experiment should begin only after the OpenHuman and Gotify reviews establish
whether a broader external-integration layer is justified.

## Limits

- Graphify inferred 642 links. Treat those as questions to verify, not facts.
- Documents and media were intentionally excluded, so this report does not map
  O.B.E.O.S design prose or images.
- The raw graph is a point-in-time engineering artifact. Re-run the documented
  commands after a meaningful architecture change instead of treating it as live
  system state.
