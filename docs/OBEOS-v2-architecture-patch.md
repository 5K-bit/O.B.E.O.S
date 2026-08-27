# O.B.E.O.S. v2 Architecture Patch

**Status:** Architecture baseline / implementation plan
**Date:** 2026-08-27
**Target:** O.B.E.O.S. v2 evolution from the v0.9 stabilization architecture

## Purpose

This patch defines the next architectural layer for O.B.E.O.S. without replacing the existing Blackfong spine, Event Contract, Health Contract, D.A.I.S.E. reasoning boundary, or canonical child-project ownership model.

The v2 direction is to make O.B.E.O.S. a coordinated multimodal intelligence system rather than a collection of independent applications.

The central principle is:

> **Events describe what happened. Context describes what is happening. D.A.I.S.E. determines what it means and what to do.**

## v2 Architectural Principles

1. **`event_backbone` remains the common O.B.E.O.S. event fabric.** It is not replaced by BlackForge, D.A.I.S.E., or `context_fabric`.
2. **Events are not conversations.** Raw subsystem events must never automatically become D.A.I.S.E. prompts.
3. **D.A.I.S.E. is the operator-facing cognitive authority.** Child systems provide observations, capabilities, evidence, and execution results; they do not compete with D.A.I.S.E. as separate conversational brains.
4. **`context_fabric` becomes the interpretation boundary between event flow and D.A.I.S.E.** It filters, correlates, groups, summarizes, and preserves evidence before relevant situations reach D.A.I.S.E.
5. **Specialized perception remains specialized.** Project 3000, EarthGlass, sensor adapters, audio systems, and other components should produce observations rather than becoming general-purpose reasoning authorities.
6. **BlackForge evolves into a multimodal engineering and reasoning subsystem.** It can consume code, system telemetry, vision, audio, sensor observations, repository state, and other evidence when an engineering or diagnostic task requires them.
7. **D.A.I.S.E. becomes multimodal at the cognitive layer.** She should reason across text, voice, vision, audio, telemetry, spatial context, temporal context, memory, and structured observations without requiring every raw modality to be processed directly by the core model.
8. **Evidence remains traceable.** Conclusions should preserve source, timestamp, confidence, correlation, and evidence references.
9. **Large raw media does not travel through the event spine.** Images, audio, video, and large artifacts remain in appropriate storage; events carry structured observations and evidence references.
10. **No uncontrolled agent feedback loops.** Events must carry correlation/causation metadata and explicit visibility/target semantics so a subsystem can distinguish its own consequences from new work.

## Target Architecture

```text
                              O.B.E.O.S.
                                  |
                           BLACKFONG SPINE
                                  |
                         event_backbone
                                  |
                +-----------------+-----------------+
                |                 |                 |
                v                 v                 v
             D.A.I.S.E.      Context Fabric      Services
                ^                 |                 |
                |                 v                 v
                |          Situation / State    Specialized Systems
                |                 |              |
                |                 |       +------+-------+---------+
                |                 |       |      |       |         |
                |                 |    3000  EarthGlass BlackForge Sensors
                |                 |       |      |       |         |
                |                 +-------+------+-------+---------+
                |                         |
                +-------------------------+

BlackForge additionally contains a Model Router:

                 BLACKFORGE
                     |
                Model Router
          +----------+----------+
          |          |          |
       Architect  Implementer  Local Worker
          |          |          |
       frontier   frontier     local model
       model      model        / fast model
```

## Component Roles

### Blackfong

The governing O.B.E.O.S. spine and policy authority.

Responsibilities:

- component registration and discovery
- policy and routing boundaries
- event backbone ownership
- memory governance
- capability admission
- system-wide contracts

Blackfong does not become the multimodal model itself.

### D.A.I.S.E.

The visible operator-facing intelligence.

Responsibilities:

- understand operator intent
- maintain conversational continuity
- reason over relevant multimodal context
- query memory and system state
- delegate work to specialized components
- decide what information is meaningful to the operator
- communicate results and uncertainty

D.A.I.S.E. should receive **situations and relevant evidence**, not an unfiltered event firehose.

### `event_backbone`

The shared event transport and durable event fabric.

Responsibilities:

- publish and route namespaced events
- preserve correlation and causation
- provide durable event history
- support component-to-component communication
- carry structured observations and execution results

It must not automatically forward every event into the D.A.I.S.E. conversational context.

### `context_fabric`

New v2 architectural layer between event flow and cognitive reasoning.

Responsibilities:

- event relevance filtering
- temporal correlation
- spatial correlation
- entity correlation
- cross-component situation assembly
- deduplication
- evidence aggregation
- confidence handling
- context compression
- operator significance classification
- generation of D.A.I.S.E.-ready situation objects

Example:

```text
camera.person_detected
server.network_anomaly
server.process_started
door.opened
```

becomes:

```text
Situation: possible unauthorized server-room activity

Evidence:
- camera: unknown person
- door: opened
- server: unusual outbound connection
- process: unexpected process started

Confidence: 0.87
Correlation window: 02:13:41 - 02:14:08
Evidence references: [camera://..., telemetry://..., audit://...]
```

D.A.I.S.E. reasons over the situation and can request deeper evidence if necessary.

### Project 3000

Perception specialist.

Responsibilities:

- camera ingestion
- visual detection
- object/person/event observations
- optional tracking and scene metadata

3000 should answer **"What do I see?"**, not **"What does the entire O.B.E.O.S. system mean?"**

### EarthGlass

Environment and Earth-data specialist.

Responsibilities:

- environmental data ingestion
- spatial observations
- weather/environmental state
- Earth-scale context

EarthGlass provides observations and evidence to the shared system rather than becoming a competing assistant.

### BlackForge

O.B.E.O.S. engineering, diagnostic, automation, and multimodal reasoning subsystem.

Responsibilities:

- repository understanding
- architecture analysis
- implementation
- testing
- debugging
- deployment analysis
- infrastructure diagnostics
- system telemetry correlation
- multimodal engineering evidence
- model routing

BlackForge is not D.A.I.S.E.'s replacement. It is a specialist D.A.I.S.E. can delegate to.

## BlackForge v2 Model Router

Do not hard-code one model as the permanent BlackForge intelligence.

Use capability-based routing:

```yaml
models:
  architect:
    provider: configured
    model: configured

  implementer:
    provider: configured
    model: configured

  reviewer:
    provider: configured
    model: configured

  local_worker:
    provider: ollama
    model: configured
```

The router should select models based on task requirements rather than model brand.

Examples:

- architecture/refactor → high-capability reasoning model
- implementation → strong coding model
- security review → independent review model
- tests/formatting/boilerplate → local worker
- multimodal diagnosis → model with the required modality support

This keeps BlackForge provider-agnostic and prevents future model releases from requiring architectural rewrites.

## Multimodal D.A.I.S.E.

D.A.I.S.E. v2 should support a multimodal cognitive interface with these conceptual inputs:

- text
- voice
- images
- audio observations
- structured sensor observations
- system telemetry
- spatial context
- temporal context
- memory
- repository/system state
- evidence references

The architecture should prefer **specialized modality adapters + structured observations** over forcing one model to ingest every raw stream.

The model layer can then reason across a unified context representation.

## Event Visibility Model

Not every event is D.A.I.S.E.-visible.

Minimum conceptual classes:

| Class | Example | D.A.I.S.E. behavior |
|---|---|---|
| Internal | `blackforge.file_changed` | Ignore unless requested |
| Operational | `blackforge.build_failed` | Consider when relevant |
| Situation | `security.possible_incident` | Reason over it |
| Operator | `operator.requested_task` | Act on it |
| Completion | `engineering.task.completed` | Report when relevant |

Every event should preserve enough metadata for filtering and causality:

```text
event_id
source
component
event_type
timestamp
correlation_id
causation_id
visibility
priority
target
requires_action
payload/evidence_ref
```

These fields should extend the existing OBEOS Event Contract rather than create a competing event protocol.

## Evidence Model

A D.A.I.S.E. conclusion should be able to expose why it reached the conclusion.

```text
CONCLUSION
Possible security incident

EVIDENCE
- camera observation
- network anomaly
- process anomaly
- access event

CORRELATION
Events occurred in the same location and time window.

CONFIDENCE
0.87

RECOMMENDED ACTION
Request deeper telemetry and consider network isolation.
```

The system should distinguish:

- observation
- inference
- confidence
- recommendation
- action

This is essential for debugging, auditability, and trustworthy multimodal reasoning.

## Raw Data vs Event Data

High-volume raw streams should remain outside the event backbone.

```text
Camera frame
   -> vision pipeline
   -> person_detected observation
   -> event_backbone
   -> context_fabric

Raw frame -> object/media storage
Event -> structured metadata + evidence_ref
```

The same pattern applies to:

- audio
- video
- large telemetry samples
- model artifacts
- logs
- screenshots
- documents

This prevents the event spine from becoming a high-bandwidth media bus.

## Feedback Loop Protection

A specialist action can generate another event without automatically triggering another agent action.

Required safeguards:

- correlation IDs
- causation IDs
- explicit target
- event visibility
- idempotency keys where actions are retried
- action ownership
- delegation depth / recursion limits
- clear distinction between observation and command

Example:

```text
DAISE
  -> engineering.task.requested
  -> BlackForge
  -> engineering.task.started
  -> engineering.task.completed
  -> context_fabric
  -> DAISE
```

The completion event returns as context. It does not automatically create another engineering task.

## Implementation Order

### Phase 1 — Preserve the spine

- Keep the existing OBEOS Event Contract v1.
- Keep the manifest-driven component registry.
- Keep Health Contract v1 and MaxPayne health aggregation.
- Do not fork the event protocol.

### Phase 2 — Introduce `context_fabric`

Create the first implementation as a small service/module that can:

1. subscribe to namespaced events
2. filter by relevance
3. correlate by time/session/entity/location
4. group related events
5. produce a situation object
6. attach evidence references
7. forward only relevant situations toward D.A.I.S.E.

### Phase 3 — Multimodal D.A.I.S.E. boundary

Extend the D.A.I.S.E. reasoning interface so its context contract can accept:

- text
- structured observations
- image references
- audio references
- system state
- situation objects
- memory context

Do not initially require every D.A.I.S.E. model to support every modality.

### Phase 4 — BlackForge model routing

Add capability-based model selection and provider configuration.

Start with:

- architect
- implementer
- reviewer
- local worker

Then add multimodal and specialized routes as needed.

### Phase 5 — Cross-system situations

Connect 3000, EarthGlass, BlackForge, system telemetry, and future sensor adapters through the existing event fabric and the new context layer.

### Phase 6 — Evidence-driven reasoning

Add confidence, evidence references, correlation windows, and explicit observation/inference/action separation to D.A.I.S.E. outputs.

## Non-Goals

v2 does **not** mean:

- replacing `event_backbone`
- turning every child project into an autonomous assistant
- sending raw camera/audio streams through the event bus
- hard-coding one frontier model into O.B.E.O.S.
- making BlackForge the new D.A.I.S.E.
- merging all repositories into one codebase
- allowing child projects to import D.A.I.S.E. internals directly

## Success Criteria

The v2 architecture is successful when the system can demonstrate:

1. A user asks D.A.I.S.E. for an engineering task.
2. D.A.I.S.E. delegates to BlackForge.
3. BlackForge selects an appropriate model.
4. BlackForge can combine repository state with relevant system/sensor evidence.
5. Project 3000 and/or other specialists can emit observations without directly controlling D.A.I.S.E.'s conversation.
6. `context_fabric` can turn related events into a coherent situation.
7. D.A.I.S.E. can reason across that situation and its evidence.
8. D.A.I.S.E. can return a result with appropriate uncertainty and evidence.
9. No raw media is pushed through the event backbone.
10. No uncontrolled agent recursion occurs.
11. Existing v0.9 contracts remain valid while v2 capabilities are introduced.

## Architectural Decision

**Accepted direction:**

> O.B.E.O.S. retains one governing spine and one shared event fabric while adding a context-fabric layer and multimodal D.A.I.S.E. cognitive boundary. BlackForge becomes a multimodal engineering/diagnostic specialist with capability-based model routing. Specialized systems remain producers of observations and providers of capabilities. D.A.I.S.E. remains the operator-facing cognitive authority.

This is an additive v2 architecture. It does not invalidate the v0.9 stabilization work; it defines the next layer built on top of it.
