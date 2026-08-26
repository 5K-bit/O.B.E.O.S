# External Repo Compatibility Matrix

This matrix records the initial compatibility position for Graphify, OpenHuman, and Gotify/server against the current O.B.E.O.S architecture.

| Area | Graphify | OpenHuman | Gotify/server |
| --- | --- | --- | --- |
| Best fit | Strong | Selective | Moderate |
| First use | Map O.B.E.O.S and external repos into architecture graphs | Study local-first memory, workflow, MCP, and agent patterns | Study self-hosted real-time notification transport |
| Integration style | External tool or bounded O.B.E.O.S service/worker | Reference architecture, not a replacement subsystem | Connector or sidecar transport |
| Should replace O.B.E.O.S core? | No | No | No |
| Should replace D.A.I.S.E? | No | No | No |
| Should replace event backbone? | No | No | No |
| Near-term value | High, because it can improve system understanding quickly | Medium, because it is broad and needs deeper review | Medium, useful after event/notification boundaries are clearer |
| Runtime risk | Low if used offline for review artifacts | High if integrated directly | Medium if introduced before event contract mapping |

## O.B.E.O.S Touchpoints

| Touchpoint | Current O.B.E.O.S authority | Compatibility rule |
| --- | --- | --- |
| Component ownership | `obeos.manifest.json` and component registry | External repos stay separate and declare clear manifests if adopted |
| Events | Event Contract v1 | New events use namespaced types such as `graphify.scan.completed` or `gotify.message.received` |
| Health | MaxPayne and health probes | External services expose bounded health/readiness signals |
| Operator surfaces | Blackfong Workspace, Thoughtglass HUD, Legion HUD, TUI | New data appears as status, events, reports, or tools without destabilizing existing surfaces |
| Reasoning | D.A.I.S.E model-led pipeline | External memory or graph data can provide context, not final authority |
| Notifications | O.B.E.O.S notification service and event backbone | Gotify can become transport, not source of truth |

## Repo Notes

### Graphify

Graphify has completed its first code-only O.B.E.O.S review. The local AST scan
mapped 397 supported source files into 5,773 nodes and 15,336 links without a model
backend or runtime change. Its strongest evidence is that the current event store,
dashboard, D.A.I.S.E, device registry, Event Contract v1, and component registry
are distinct, inspectable boundaries. See
`graphify-findings/obeos-code-scan-2026-08-26.md`.

Graphify remains an offline developer and architecture-review tool. A later runtime
experiment, if justified, must be an on-demand manifest-backed worker that publishes
only namespaced scan lifecycle events and bounded report references. It must not read
Legion credentials, become a D.A.I.S.E authority, or add work to HUD page loads.

### OpenHuman

OpenHuman should be treated as a concept and architecture reference until licensing, dependency size, and integration boundaries are fully reviewed. Its local-first memory and workflow ideas may inspire future D.A.I.S.E context tools, but it should not be imported wholesale.

### Gotify/server

Gotify/server should be considered later as a notification delivery option. If adopted, it should sit behind a connector that translates between Gotify messages and O.B.E.O.S Event Contract v1/notification semantics.
