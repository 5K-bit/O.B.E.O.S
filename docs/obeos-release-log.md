# OBEOS Release Log

This file is the repo-tracked product history for O.B.E.O.S.

Use it to record:

- major architecture changes
- operator-surface improvements
- retrieval/runtime behavior changes
- cloud rollout milestones
- notable regressions fixed

## Logging Rules

- Keep entries newest first.
- Reference the git revision when available.
- Prefer operator-facing language over raw implementation detail.
- Keep cloud runtime deployment history in `data/deploy-history.log`; do not commit runtime-only secrets or host-specific state here.

## 2026-08-13

### Unreleased

- Tightened shared runtime telemetry so traces now distinguish `live_model`, `grounded_memory`, `grounded_knowledge`, and fallback paths.
- Added richer model-lane breadcrumbs including selected model, attempted model chain, timeout budget, and health-forced fallback state.
- Hardened degraded-backend routing so `standard` requests can stay on a safer single-model lane instead of drifting into slower fallback chains during cloud instability.
- Documented the permanent local -> GitHub -> cloud release record workflow in the main README.
