# Blackfong Engineering Standard (BES-1)

**Version:** 1.0 (proposed)  
**Owner:** Blackfong / OBEOS  
**Status:** Draft until the standard is accepted and merged.  
**Applies to:** Maintained software repositories; implementations are adapted to each language, runtime, and risk.

## Purpose

Move work from *code complete* to *operationally proven* without claiming that a successful CI run is equivalent to real-world acceptance. Reuse existing quality gates; do not replace working project-specific pipelines with generic tooling.

## Lifecycle status (not interchangeable)

| Status | Required evidence |
| --- | --- |
| Built | Change is committed, described, and reviewed. |
| Verified | Relevant automated checks pass at the exact commit; failures and omissions are stated. |
| Released | Versioned artifact or deployed revision exists; install/deploy procedure, checksums where applicable, and rollback are documented. |
| Operational | A real installation/runtime has passed the applicable acceptance checks; failures, recovery behavior, and environment/date are recorded. |

A repo may be **Verified** while **not Released** or **not Operational**. Do not advance status using PR comments, a green badge, or simulated testing alone.

## Required change record

Each substantive PR documents:
1. Problem, scope and affected interfaces, including any schema/API/command changes.
2. Safety/security boundaries, threat considerations and possible regressions.
3. Exact tests run, commands, environment, results and commit SHA.
4. Manual acceptance that was actually performed, plus acceptance still outstanding.
5. Failure modes, recovery/rollback procedure, and operational monitoring signals.
6. AI-assisted development disclosure: `AI assistance: <scope of assistance>`; `Human verification: <what was independently checked and by whom>`.

An unrun check is **not run**, never "passed"; an unavailable environment is **not verified**. Do not expose secrets, personal data, production tokens, or unredacted logs in PR evidence.

## Minimum engineering gates

**All maintained repos**
- Reproducible setup instructions and pinned/locked dependencies where relevant.
- Static checks and tests for the supported runtime; CI uses least-privilege permissions.
- Secrets are neither committed nor printed; external side effects need explicit authorization.
- Dependency/security scans are evaluated, triaged and tracked (not necessarily blocking until baseline is established).
- User-visible behavior, error states, and degraded/unavailable states are distinguishable.
- Tagged releases use clean-checkout build/installation checks; rollback or recovery is documented.
- Tests identify simulated data, live data, and hardware/browser-specific acceptance separately.

**CLI / library:** clean install, command/help smoke test, functional integration and artifact verification.  
**Web / PWA:** static checks, browser E2E where supported, deployment verification, and platform-specific manual acceptance for Safari/iOS claims.  
**Services / integrations:** health checks, network failure/dependency unavailability, stale-data/clock-skew behavior where relevant, restart/persistence and recovery.  
**Security / agent systems:** target authorization, least privilege, approval gates, replay/audit evidence, and safe failure.  
**Trading / consequential automation:** paper/demo/live separation, deterministic replay, and explicitly authorized execution; never promote from backtest to live automatically.  
**OS / installer / hardware:** VM/CI results do not replace UEFI, physical device, installer or model-backend acceptance.

## Standard developer interface

Where appropriate, use an optional root `justfile` with these task names:
- `just setup` — set up dependencies with explicit local prerequisites.
- `just check` — run the existing automated project checks.
- `just integration` — run relevant integration tests where they exist.
- `just smoke` — run a safe local smoke check (no destructive side effects).
- `just release-check` — perform repeatable prerelease checks; **never publish automatically**.

Do not invent commands or add no-op gates to fill every slot. Document why a task is absent, and preserve direct commands for operators without `just`. Build and local runtime prerequisites may differ by OS.

## Tool adoption

- **Immediate:** task runner on pilot repos; existing CI/test runners; dependency-update policy; PR evidence and release gates.
- **Evaluate separately:** Trivy, zizmor and Renovate, after checking existing Dependabot/scanner coverage and alert volume. Avoid duplicate bots, redundant scans, and unreviewed mass-update PRs.
- **Workload specific:** Playwright for browser surfaces; fault injection for services; OpenTelemetry where cross-process tracing adds meaningful value.
- **Not a default requirement:** adding an LLM, changing package managers, blanket instrumentation, new cloud services, or replacing validated build systems.

## Rollout, exceptions and governance

1. Pilot one Python tool (Blackbox), one no-build PWA (Pocket) and one mixed service/client (EarthGlass).
2. Add documentation and optional task aliases first; retain existing workflows and deployment behavior.
3. Review PRs and validate commands locally or via CI. If no live tests ran, say so.
4. After pilot acceptance, adopt BES-1 across maintained repos by risk/category. Archived/experimental repos can document an exception.
5. Do not mass-merge, auto-enable auto-merge, or change branch protection/deployment credentials as part of adoption.
6. Changes to this standard require a new version, migration notes and an explicit review; individual repos may add **stricter** checks.

**Non-goal:** labeling an entire portfolio production-ready. BES-1 is an evidence system, not a marketing badge.
