# O.B.E.O.S User Guide

## Overview

O.B.E.O.S. is the **Official Blackfong Environment Operating System**.

It combines:

- **Blackfong** as governing spine and routing authority
- **D.A.I.S.E.** as the visible assistant
- **Blackcomputer** as the host/orchestrator identity
- **Blackfong Workspace** as the canonical native operator shell
- **Thoughtglass** as the live cognition surface
- **Memory Chamber** as the deeper graph/archive workbench

The system is designed to be:

- offline-first
- append-only at the event layer
- graph-aware and memory-aware
- multi-surface without splitting authority

## Surface Guide

Use the surfaces like this:

- **Blackfong Workspace**:
  - canonical local operator shell
  - best for Thoughtglass, Memory Chamber, desktop-native workflows, and rich control
- **Thoughtglass HUD**:
  - browser-based secondary cockpit
  - good for remote/debug/operator access and quick inspection
- **TUI**:
  - fallback/home surface
  - good for terminal-first access and degraded mode

Important rule:

- O.B.E.O.S. owns the session
- Plasma is a managed workspace, not a second authority

## Quick Start

### Windows local workspace

```powershell
cd "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m blackfong_ecosystem.cli init
python -m blackfong_ecosystem.cli verify automated
python -m blackfong_ecosystem.cli desktop workspace
```

### Optional Thoughtglass HUD

```powershell
python -m blackfong_ecosystem.cli hud
```

Default HUD address:

- `http://127.0.0.1:8080`

## Core Commands

### System lifecycle

```powershell
blackfong init
blackfong verify automated
blackfong verify surfaces
blackfong verify integrations
blackfong verify full
```

### Surfaces

```powershell
blackfong desktop status
blackfong desktop start
blackfong desktop workspace
blackfong desktop stop

blackfong hud
blackfong tui
blackfong home
```

### Memory and reasoning

```powershell
blackfong event-log --source user --type agent_query --text "hello world" --confidence 1.0 --session-id demo-session --correlation-id demo-correlation --tag manual
blackfong memory-query --keyword "hello"
blackfong daise-session "Analyze this system state"
```

### Spine and agents

```powershell
blackfong spine status --mode learning
blackfong agent list --mode full
blackfong agent admit vision
blackfong tiny status
blackfong vision smoke
```

### O.B.E.O.S packaging and VM commands

```powershell
blackfong obeos manifest
blackfong obeos shell-compat
blackfong obeos materialize --output data\obeos-remaster
blackfong obeos vm-checklist
blackfong obeos vm-acceptance-snapshot
blackfong obeos activate-home
blackfong install-plan status
blackfong install-plan validate --profile amd64-pc
```

## Desktop Mode Contract

`blackfong desktop start` should be understood as:

**enter managed Blackcomputer workspace mode**

It is not just "launch a GUI."

It should:

1. run preflight checks
2. start missing services where appropriate
3. restore workspace profile state
4. transition into desktop mode
5. launch the native workspace shell where available
6. preserve Blackfong session authority

## Kubuntu / Plasma Guest Workflow

O.B.E.O.S. v1 standardizes on:

- `Kubuntu 24.04 LTS`
- `KDE Plasma`

### Stage A

Stage A is the safe bootstrap phase.

Run this first:

- keep `graphical.target`
- validate HUD
- validate workspace shell
- validate other operator surfaces

### Stage B

Stage B is explicit activation of TUI-home boot:

```bash
blackfong obeos activate-home
```

Only run Stage B after Stage A checks pass.

### Important rule

If TUI-home fails:

- the machine must not be trapped
- the platform must fall back to normal shell login
- the state should be reported as degraded

## Branding Notes

The identity hierarchy is:

- product: `O.B.E.O.S.`
- spine authority: `Blackfong`
- machine: `Blackcomputer`
- assistant: `D.A.I.S.E.`
- live cognition surface: `Thoughtglass`
- archive/analysis surface: `Memory Chamber`

The system may still contain compatibility references to `jarvis`, but that is a legacy alias, not the current product identity.

## TinyProgrammer Notes

TinyProgrammer is a companion participant, not the session authority.

It may be:

- active
- warming
- degraded
- offline

If Tiny is degraded or offline, the rest of O.B.E.O.S. should continue honestly rather than pretending everything is healthy.

## Troubleshooting

### Workspace does not launch

Try:

```powershell
blackfong verify surfaces
python desktop_shell.py
```

### Thoughtglass HUD does not respond

Try:

```powershell
blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser
```

Then open:

- `http://127.0.0.1:8080`

### TinyProgrammer is offline

Check:

```powershell
blackfong tiny status
blackfong verify surfaces
```

### Linux guest branding was not applied

After materializing and bootstrapping the guest, run:

```bash
/opt/obeos/bin/apply-obeos-branding.sh
```

## Related Docs

- [README](C:\Users\New%20User\Documents\Projects%202026\O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)\README.md)
- [O.B.E.O.S Packaging Readiness Plan](C:\Users\New%20User\Documents\Projects%202026\O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)\docs\obeos-packaging-readiness-plan.md)
- [Branding Doctrine](C:\Users\New%20User\Documents\Projects%202026\O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)\docs\branding-doctrine.md)
- [Release Surface Hierarchy](C:\Users\New%20User\Documents\Projects%202026\O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)\docs\release-surface-hierarchy.md)
- [Release Artifacts](C:\Users\New%20User\Documents\Projects%202026\O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)\docs\release-artifacts.md)
