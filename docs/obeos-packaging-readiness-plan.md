# O.B.E.O.S Packaging Readiness Plan

## Purpose

This document turns the current O.B.E.O.S repo state into a concrete branding and packaging roadmap.

The goal is not to redesign the product from scratch. The goal is to take the system that already exists, remove doctrine drift, finish the identity surfaces, and make the packaging path reliable enough for a repeatable installer/remaster workflow.

## Current State Snapshot

### What already exists

- A strong product identity already exists across the repo:
  - `O.B.E.O.S.`
  - `Blackfong`
  - `Blackcomputer`
  - `D.A.I.S.E.`
  - `Thoughtglass`
  - `Memory Chamber`
- The native PySide6 workspace is already a serious operator shell, not just a mockup.
- Thoughtglass HUD exists and is still useful as a secondary cockpit.
- The Kubuntu remaster/materialize path already exists.
- Branding asset generation already exists for:
  - GRUB
  - Plymouth
  - SDDM
  - Plasma color scheme
  - wallpaper
  - lock screen
  - terminal/login identity text
- A guest branding apply script already exists:
  - `/opt/obeos/bin/apply-obeos-branding.sh`
- An installer plan service already exists and can validate manifests against the external installer project.
- A doctrine/document foundation now exists for:
  - branding hierarchy
  - release surface hierarchy
  - release artifacts

### What the repo proves today

- O.B.E.O.S is no longer only a browser HUD project.
- O.B.E.O.S is no longer only a TUI concept.
- O.B.E.O.S now has:
  - a canonical native workspace direction
  - a remasterable Kubuntu session contract
  - a recognizable visual language
  - a real packaging doctrine beginning to form

## Core Readiness Finding

The main blocker is **not absence of packaging code**.

The main blocker is **consistency**:

- inconsistent product doctrine across docs and surfaces
- lingering legacy naming and routing assumptions
- an installer project that still describes an older XFCE/Xubuntu truth
- a product identity that is strong visually, but not yet unified operationally

In short:

O.B.E.O.S has enough implementation to package, but not yet enough consistency to package cleanly.

## Readiness Inventory

### Branding surfaces already implemented

- Native workspace shell
- Thoughtglass HUD
- Thoughtglass / Memory Chamber terminology
- Boot splash hooks
- Linux guest branding bundle
- GRUB theme output
- Plymouth theme output
- SDDM theme output
- Plasma colors
- lock screen config
- wallpaper metadata

### Packaging surfaces already implemented

- `blackfong obeos materialize`
- remaster bundle generation
- VM checklist and acceptance snapshot flow
- guest bootstrap scripts
- explicit branding apply script
- installer plan validation service
- Docker HUD fallback container path

### Validation surfaces already implemented

- packaging/remaster tests
- workspace probe
- verification commands
- VM acceptance snapshot contract

## Real Gaps

## 1. Product doctrine drift

The repo currently describes multiple truths at once.

Examples:

- Historical docs previously described the browser HUD as the primary interface, and remaining legacy copy still needs watching.
- The current product direction is that the PySide6 workspace is the canonical operator shell.
- Some language still frames the project as `Blackfong Ecosystem` first, while the shipping surface increasingly wants `O.B.E.O.S.` first.
- The installer project now warns that its XFCE/Xubuntu path is only a migration baseline, but its implementation still has not been upgraded to the main repo's Kubuntu + KDE Plasma doctrine.

This is the biggest readiness problem because packaging freezes doctrine.

## 2. Legacy `jarvis` drift is still too visible

The README correctly says `jarvis` is a legacy alias only.

But the codebase still contains many legacy `jarvis` identifiers in:

- routing
- config
- agent naming
- telemetry
- tests
- installer bundles
- voice wake logic

This may be technically acceptable internally for now, but it is not packaging-clean if exposed in:

- user docs
- installer bundles
- desktop messaging
- voice setup
- operator-facing diagnostics

Progress made:

- the install-plan export now carries doctrine-friendly bundle display names alongside raw compatibility ids
- installer manifest comments now explicitly mark `jarvis_agent` as a compatibility bundle id

## 3. Installer project is only partially aligned with O.B.E.O.S v1

The external installer repository exists and has real manifests.

However, the repo is only partially aligned:

- README and manifest comments now clearly state that the current XFCE/Xubuntu path is a migration baseline only
- code defaults and desktop installation logic still actually target that older baseline
- Code Warden assumptions are still written around that baseline
- the implementation has not yet been upgraded to a true Kubuntu/Plasma desktop path

That makes it useful as migration/build input, but not yet a final O.B.E.O.S v1 installer truth.

## 4. Documentation is not packaging-ready

The docs are functional, but not yet release-grade.

Current issues:

- the core docs are now aligned to the native-shell-first doctrine, but deeper code/UI/installer copy still needs a consistency pass
- the user-facing documentation set is stronger now, and the release checklist/shot list exist, but it still needs real screenshots and final install/deployment walkthrough polish
- the new doctrine docs exist, and major installer, HUD, TUI, guest, and fallback runtime wording has been aligned, but they are not yet fully enforced across all runtime messaging and legacy text
- a dated command snapshot now exists in [release-evidence-2026-06-01.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-evidence-2026-06-01.md), and the repo now has an automated regeneration path through `blackfong verify evidence`

## 5. Surface identity is close, but still split

The visual language is strong, but the naming and hierarchy still need a final tightening pass:

- major browser HUD, emergency fallback, guest bundle, TUI header, and Thoughtglass ASCII branding now align more closely with `Thoughtglass HUD`, `Blackfong Workspace`, `Blackcomputer`, and `D.A.I.S.E.`
- native shell uses `Blackfong Workspace`
- Linux branding emphasizes `O.B.E.O.S.`
- system doctrine references `Blackcomputer`

These are all valid concepts, but they need a formal hierarchy before packaging:

- what is the product name?
- what is the machine name?
- what is the shell name?
- what is the assistant name?
- what is the cognition surface name?

## 6. Python packaging metadata is only partially aligned with shipping reality

`pyproject.toml` includes HUD static assets, which is good.

But the packaging story is still split across:

- Python package install
- native desktop shell script
- Linux materialize bundle
- Docker HUD image
- external installer repo

That is workable, but it needs one documented release contract.

## Canonical Identity Hierarchy To Freeze

Before packaging, the project should explicitly freeze this hierarchy:

- Product:
  - `O.B.E.O.S.`
- Governing spine / ecosystem authority:
  - `Blackfong`
- Machine / host identity:
  - `Blackcomputer`
- Visible assistant:
  - `D.A.I.S.E.`
- Native operator shell:
  - `Blackfong Workspace`
- Live cognition surface:
  - `Thoughtglass`
- Deep archive / analysis surface:
  - `Memory Chamber`

This should become the rule across:

- README
- user guide
- operator docs
- workspace shell
- HUD
- Linux guest branding
- installer text

## Packaging Doctrine To Freeze

For O.B.E.O.S v1, the release doctrine should be:

- Kubuntu 24.04 LTS
- KDE Plasma
- O.B.E.O.S owns the session
- Plasma is a managed workspace, not a second authority
- PySide6 workspace is the canonical operator shell
- browser HUD is a supported secondary cockpit
- TUI remains supported, but not the primary product surface
- branding follows stability
- installer legacy XFCE assumptions are migration input only, not product truth

## Release Blockers

These should be treated as true blockers before calling the system packaging-ready.

### Blocker A: docs and doctrine alignment

Required:

- keep README, user guide, and doctrine docs aligned as product truth
- audit remaining runtime/UI copy against the new branding hierarchy
- stop any remaining release-facing docs from implying the browser HUD is the primary interface
- add a short release checklist once the deployment path is frozen
- keep producing dated release-evidence snapshots with `blackfong verify evidence` instead of relying on informal local memory

### Blocker B: installer doctrine alignment

Required:

- decide whether the external installer project is:
  - still the planned shipping installer, or
  - only a historical migration/build tool
- if it is shipping:
  - update it from XFCE/Xubuntu assumptions to Kubuntu/Plasma doctrine
- if it is not shipping:
  - say so explicitly in O.B.E.O.S docs and stop presenting it as near-final product truth

### Blocker C: operator-facing naming cleanup

Required:

- remove or hide legacy `jarvis` naming from operator-facing surfaces
- keep internal compatibility aliases only where still required
- ensure installer bundles and docs do not imply `jarvis` is still a first-class visible identity

### Blocker D: define the release artifact contract

Required:

- explicitly define the release outputs for v1:
  - Python/dev install
  - Windows local workspace launch
  - browser HUD
  - Kubuntu materialized bundle
  - remastered ISO or guest bootstrap path
  - optional installer integration path

## Recommended Packaging Milestones

## Milestone 1: Doctrine freeze

Goal:

Create one canonical product truth across docs and code messaging.

Tasks:

- update README
- repair and simplify the user guide
- add `docs/branding-doctrine.md`
- add `docs/release-surface-hierarchy.md`
- add `docs/release-artifacts.md`

Exit criteria:

- no core doc still claims browser HUD is primary
- no core doc still presents XFCE/Xubuntu as O.B.E.O.S v1 truth
- product naming hierarchy is explicit and stable

## Milestone 2: Surface branding consistency

Goal:

Make the workspace, HUD, and Linux guest feel like one product family.

Tasks:

- standardize top-level brand language across HUD and workspace
- finish the remaining diagnostics, fallback, and guest-surface naming sweep so `Thoughtglass HUD` and `Blackfong Workspace` read as one family everywhere
- ensure native shell, HUD, and guest branding all use the same identity hierarchy
- review Thoughtglass / Memory Chamber labels for consistency

Exit criteria:

- all operator surfaces feel like one system
- no contradictory title/header/identity strings remain

## Milestone 3: Installer and deployment alignment

Goal:

Make the install/deploy path honest and repeatable.

Tasks:

- keep `installer_plan.py` doctrine notes and bundle display names aligned with current visible branding
- keep `install-plan status` aligned with the real current installer truth
- decide whether the raw `jarvis_agent` bundle id should remain a compatibility-only internal artifact or be renamed at the installer layer later
- either modernize the external installer repo to Kubuntu/Plasma or downgrade it to migration-only status
- add one canonical deployment guide for:
  - dev machine
  - Windows workspace
  - Kubuntu guest
  - remaster bundle

Exit criteria:

- install-plan status and validation honestly report the current installer path and doctrine gap
- no deployment path implies outdated desktop assumptions

## Milestone 4: Packaging artifact hardening

Goal:

Make the release artifacts dependable.

Tasks:

- verify all required branding assets materialize every time
- verify Linux guest branding is applied idempotently
- verify package-data and runtime assets are present in installed Python builds
- verify Docker HUD path is explicitly optional and documented as secondary
- maintain the sample release checklist and screenshot shot list for v1

Exit criteria:

- a release operator can identify exactly what artifacts to build and validate

## Milestone 5: Final acceptance pass

Goal:

Decide whether O.B.E.O.S is ready for a packaging trial release.

Required acceptance areas:

- native workspace launch
- HUD launch
- Thoughtglass / Memory Chamber availability
- Linux materialize bundle
- branding asset application
- VM checklist and acceptance snapshot flow
- installer doctrine decision
- docs completeness

## Immediate To-Do List

### Highest priority

- Decide whether the external installer is shipping truth or migration-only
- Finish the remaining guest, fallback, and diagnostics naming sweep
- Verify branding assets and remaster outputs materialize repeatably
- Capture the release screenshot set from real current surfaces
- Keep the release checklist and installer path docs aligned with the actual packaging path

### Next priority

- Clean up operator-facing `jarvis` traces
- Finish any remaining surface-title/header drift that survives the current wording pass
- Audit workspace/HUD/guest wording against the canonical identity hierarchy after the next runtime changes
- Align installer bundle names with current product language

### Nice to have after blockers

- richer branded screenshots for docs
- keep the polished release checklist template current
- a packaging/version manifest for v1
- keep the installer path comparison note current for Windows local use vs Kubuntu guest packaging

## What "Ready For Packaging" Should Mean

O.B.E.O.S should be considered ready for packaging when:

- the product doctrine is singular and documented
- the shipped surfaces tell the same story
- the deployment path is explicit and honest
- branding assets are complete and repeatable
- the installer/remaster path no longer points to conflicting desktop assumptions
- legacy alias baggage is no longer visible at the product layer

At that point, packaging becomes an execution step.

Before that point, packaging will keep freezing confusion into artifacts.
