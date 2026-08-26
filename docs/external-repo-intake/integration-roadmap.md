# External Repo Integration Roadmap

## Phase 1: Docs And Graph Review

- Keep this phase docs/artifacts only.
- Completed: use Graphify to generate a local, code-only graph for O.B.E.O.S.
- Completed: record the baseline result in `graphify-findings/obeos-code-scan-2026-08-26.md`.
- Use Graphify to generate smaller comparison graphs for Graphify, OpenHuman, and Gotify/server.
- Store generated reports under `docs/external-repo-intake/graphify-findings/`.
- Record exact repo refs and commands so the review is reproducible.

## Phase 2: Graphify As A Bounded Tool

- Decide whether Graphify remains a developer-only tool or becomes an O.B.E.O.S service/worker.
- If it becomes an O.B.E.O.S component, add a manifest with lifecycle `on-demand`.
- Add CLI entrypoints only after the review artifacts prove value.
- Publish scan lifecycle events through Event Contract v1:
  - `graphify.scan.started`
  - `graphify.scan.completed`
  - `graphify.scan.failed`
- Do not add Graphify output to D.A.I.S.E context until the generated reports are stable and small enough to be useful.

## Phase 3: OpenHuman Selective Review

- Review OpenHuman memory, workflow, MCP, and local-model patterns.
- Identify reusable concepts, not direct code imports.
- Compare OpenHuman memory concepts against D.A.I.S.E memory and O.B.E.O.S knowledge ingestion.
- Produce a separate recommendation before any code integration.
- Keep D.A.I.S.E authoritative for conversation, routing, and operator identity.

## Phase 4: Gotify Transport Review

- Review Gotify/server REST and WebSocket boundaries.
- Design a connector that maps Gotify messages to O.B.E.O.S notifications and Event Contract v1.
- Keep O.B.E.O.S notification storage and event history as source of truth.
- Add Gotify only if there is a clear operator benefit for real-time push notifications outside the current HUD.

## Acceptance Criteria

- The review can be reproduced from documented refs and commands.
- No external repo code is copied into O.B.E.O.S core.
- No existing Legion HUD behavior changes during the review phase.
- Every future runtime integration candidate has a clear owner, manifest path, health story, and event namespace.
- Risks are documented before any dependency or runtime surface is added.

## Current Default

Proceed in the order: Graphify review first, OpenHuman concept review second, Gotify/server transport review third.
