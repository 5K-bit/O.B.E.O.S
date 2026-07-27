# O.B.E.O.S Release Surface Hierarchy

## Purpose

This document defines how the O.B.E.O.S. operator surfaces relate to each other for packaging and release.

## Canonical Hierarchy

### 1. Blackfong Workspace

Type:

- primary local operator shell
- native PySide6 surface

Role:

- canonical richest operator experience
- local Thoughtglass and Memory Chamber work
- native desktop-native controls
- pop-out panels, layout persistence, and richer workstation behavior

Primary commands:

```powershell
blackfong desktop workspace
blackfong desktop start
```

### 2. Thoughtglass HUD

Type:

- browser-based secondary cockpit

Role:

- remote/debug/fallback operator access
- quick command execution
- surface verification
- lighter cockpit use

Primary command:

```powershell
blackfong hud
```

### 3. TUI

Type:

- fallback/home authority surface

Role:

- terminal-first home
- degraded access path
- lower-overhead interaction path

Primary commands:

```powershell
blackfong tui
blackfong home
```

## Linux Session Rule

On the Kubuntu guest:

- O.B.E.O.S. owns the session
- Plasma is a managed workspace
- Plasma is not a second authority

That means:

- `blackfong desktop start` is not just "launch GUI"
- it means "enter managed Blackcomputer workspace mode"

## Release Positioning

For v1 packaging:

- **Blackfong Workspace** is the canonical local product shell
- **Thoughtglass HUD** remains officially supported
- **TUI** remains supported, but it is not the flagship surface

## Thoughtglass / Memory Chamber Split

### Thoughtglass

- live operational cognition surface
- cockpit view
- active state, faults, warming, ready/degraded truth

### Memory Chamber

- deep archive / graph exploration workbench
- cluster browser
- neighborhood exploration
- analysis and recall

They share one backend cognition graph, but they are not the same UX tier.

## Packaging Implication

Release docs, screenshots, and installer positioning should present the surfaces in this order:

1. Blackfong Workspace
2. Thoughtglass HUD
3. TUI

Any doc that still implies Thoughtglass HUD is the primary product surface should be considered outdated.
