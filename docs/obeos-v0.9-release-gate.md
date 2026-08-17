# OBEOS v0.9 Release Candidate Gate

A v0.9 RC may be tagged only when every mandatory gate below passes from a clean checkout with submodules initialized.

## Repository integrity

- `git submodule update --init --recursive` succeeds.
- Canonical child repos are pinned, not duplicated.
- Every shipped component has a valid `obeos.manifest.json`.
- Component registry reports zero manifest errors.

## Runtime integration

- Core boot succeeds.
- Component discovery succeeds.
- Health aggregation returns OBEOS Health Contract v1 records.
- Event propagation accepts OBEOS Event Contract v1 envelopes.
- DAISE simple general-knowledge conversation reaches a model lane without requiring RAG hits.
- DAISE memory queries use retrieval as context and preserve conversational continuity.
- MaxPayne diagnostics run with the OBEOS profile.
- Nightwatch health/task integration passes.
- The Assistant health integration passes.
- Thoughtglass can display component health/event state without owning that state.

## Platform

- Kubuntu 24.04 LTS + KDE Plasma is the only shipping desktop implementation.
- XFCE/Xubuntu is not executed by the shipping installer path.
- Blackfong Workspace launches as the canonical operator shell.
- TUI and Thoughtglass remain tested fallback/secondary surfaces.

## Identity

No operator-facing screen, help text, installer copy, notification, or diagnostic identifies the assistant as Jarvis. Internal compatibility identifiers may remain until v1.0 if they are not exposed.

## Release evidence

`blackfong verify automated`, `surfaces`, `integrations`, and `full` must pass, and release evidence must record the exact OBEOS commit plus every pinned component commit.
