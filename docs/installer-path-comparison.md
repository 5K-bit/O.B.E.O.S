# O.B.E.O.S. Installer Path Comparison

## Purpose

This document explains the current packaging and installer paths so release work does not accidentally present a migration tool as if it were already the final O.B.E.O.S. v1 installer.

## Current Paths

### 1. Windows Local Workspace

Entry points:

- `blackfong desktop start`
- `python desktop_shell.py`

Role:

- fastest local operator experience for development
- strongest current demonstration surface
- useful for branding iteration, Thoughtglass, Memory Chamber, and D.A.I.S.E. chat validation

Not by itself:

- a Linux installer
- a final guest packaging artifact

### 2. Thoughtglass HUD Browser Path

Entry point:

- `blackfong hud`

Role:

- secondary browser cockpit
- remote/debug/fallback surface
- useful for verification and operator access when the native workspace is not the right surface

Not by itself:

- the canonical product shell
- the final packaging target

### 3. Kubuntu Guest / Remaster Path

Entry points:

- `blackfong obeos materialize --output data\obeos-remaster`
- remaster scaffold under `data\obeos-remaster\remaster\`

Role:

- canonical O.B.E.O.S. v1 Linux packaging path
- current best path for a branded guest, remaster bundle, or future installer-backed release
- doctrine-aligned with Kubuntu 24.04 LTS + KDE Plasma

This is currently the most truthful packaging path for v1.

### 4. External Blackfong Installer Repo

Path:

- `C:\Users\New User\Documents\Projects 2026\Blackfong-Installer-main\Blackfong-Installer-main`

Role today:

- migration/build input
- planning and manifest validation source
- useful for future installer work
- current runtime status can be checked with `blackfong install-plan status`

Current limitation:

- its implementation still uses an XFCE/Xubuntu-style migration baseline
- it is not yet fully aligned with the Kubuntu/Plasma O.B.E.O.S. v1 session doctrine

Therefore:

- it should not be presented as the final shipping O.B.E.O.S. installer yet

### 5. Docker HUD Path

Artifacts:

- `compose.yaml`
- `Dockerfile`

Role:

- optional support path
- useful for fallback or containerized HUD experiments

Not by itself:

- the canonical packaging target

## Recommended Positioning Today

If someone asks "what is the real packaging path right now?", the most honest answer is:

1. native workspace for development and visual validation
2. Kubuntu guest/materialize/remaster for canonical Linux packaging work
3. external installer repo as migration/build input until upgraded

## Decision Gate Before Trial Packaging

Before a public packaging trial, decide one of these:

1. upgrade the external installer so it truly targets Kubuntu/Plasma and then promote it
2. keep the external installer in migration-only status and ship the remaster/materialize path first

Either decision is valid.

What is not valid is leaving the docs ambiguous.

## Short Recommendation

For O.B.E.O.S. v1, the safest and clearest packaging story is:

- **Primary packaging truth**: Kubuntu guest + materialize/remaster path
- **Primary operator surface**: Blackfong Workspace
- **Secondary browser surface**: Thoughtglass HUD
- **Installer repo status**: migration/build input until fully aligned
