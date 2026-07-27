# O.B.E.O.S Release Artifacts

## Purpose

This document defines what the project currently considers release-relevant artifacts for packaging.

## Artifact Families

### 1. Python / local runtime

Contents:

- `blackfong_ecosystem` package
- CLI entrypoint
- native workspace shell
- HUD static assets
- supporting docs

Primary evidence:

- `pyproject.toml`
- `requirements.txt`
- package data for HUD assets

### 2. Native workspace shell

Primary artifact:

- `desktop_shell.py`

Release role:

- canonical local operator shell
- primary local packaging target on Windows and desktop-native environments

### 3. Thoughtglass HUD

Primary artifacts:

- `blackfong_ecosystem/ui/hud/dashboard.html`
- `blackfong_ecosystem/ui/hud/dashboard.css`
- `blackfong_ecosystem/ui/hud/dashboard.js`

Release role:

- supported secondary cockpit
- remote/debug/fallback access path

### 4. Kubuntu deployment bundle

Primary artifact command:

```powershell
blackfong obeos materialize --output data\obeos-remaster
```

Key bundle outputs include:

- `etc/obeos/manifest.json`
- `etc/obeos/bootstrap_stage.json`
- `opt/obeos/bin/bootstrap-kubuntu-guest.sh`
- `opt/obeos/bin/apply-obeos-branding.sh`
- `opt/obeos/share/branding/*`

### 5. Remaster scaffold

Primary artifact:

- `data/obeos-remaster/remaster/build-kubuntu-remaster.sh`

Release role:

- packaging scaffold for a branded Kubuntu-based O.B.E.O.S. image

### 6. Installer-plan validation path

Primary command:

```powershell
blackfong install-plan status
blackfong install-plan validate --profile amd64-pc
```

Current status:

- `install-plan status` explicitly reports the current installer path as migration/build input only
- validates against the external installer repo manifests
- useful for planning and migration
- not yet proof that the installer doctrine is fully aligned with O.B.E.O.S. v1 session truth

### 7. Docker HUD path

Primary artifacts:

- `compose.yaml`
- `Dockerfile`
- `requirements.docker-hud.txt`

Release role:

- optional fallback containerized HUD path
- not the primary packaging target

## Current Packaging Doctrine

For O.B.E.O.S. v1, the release story should prioritize:

1. native workspace shell
2. Thoughtglass HUD support
3. Kubuntu deployment/remaster path

The Docker HUD path remains useful, but it should be documented as optional rather than treated as the main product target.

## Pre-Release Checklist

Before calling the project packaging-ready, verify:

- README and user guide match current surface doctrine
- branding doctrine is frozen and documented
- release surface hierarchy is documented
- Linux guest branding assets materialize cleanly
- install-plan doctrine is honest about the current installer status
- legacy alias naming is not leaking into release-facing copy

Supporting docs:

- [Release Checklist v1](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-checklist-v1.md)
- [Release Screenshot Shot List](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-screenshot-shotlist.md)
- [Installer Path Comparison](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/installer-path-comparison.md)
