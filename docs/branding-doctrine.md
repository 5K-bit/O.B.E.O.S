# O.B.E.O.S Branding Doctrine

## Purpose

This document freezes the identity hierarchy for O.B.E.O.S. so packaging, docs, surfaces, and installer work all speak with one voice.

## Canonical Identity Hierarchy

### Product

- `O.B.E.O.S.`
- Expansion: `Official Blackfong Environment Operating System`

This is the product name that should appear in:

- release docs
- packaging docs
- Linux guest branding
- installer doctrine
- product-level identity statements

### Governing spine

- `Blackfong`

Blackfong is the governing authority, routing spine, and memory/policy owner.

Blackfong is not the desktop environment and not the visible assistant persona.

### Machine identity

- `Blackcomputer`

Blackcomputer is the machine or host identity.

Use it when referring to:

- the managed workspace mode
- the host shell/session identity
- device-level deployment personality

### Visible assistant

- `D.A.I.S.E.`

D.A.I.S.E. is the visible operator-facing assistant.

Use D.A.I.S.E. for:

- chat surfaces
- spoken assistant references
- request acceptance / explanation messages
- assistant-facing UX copy

### Native operator shell

- `Blackfong Workspace`

This is the canonical native PySide6 operator shell.

### Live cognition surface

- `Thoughtglass`

This is the live operational cognition surface.

### Deep archive / analysis surface

- `Memory Chamber`

This is the archive/analysis workbench over the same cognition graph.

## Naming Rules

### Use these names as product truth

- `O.B.E.O.S.`
- `Blackfong`
- `Blackcomputer`
- `D.A.I.S.E.`
- `Blackfong Workspace`
- `Thoughtglass`
- `Memory Chamber`

### Legacy compatibility names

- `jarvis`

Rule:

- `jarvis` may remain as an internal compatibility alias where runtime stability still requires it
- `jarvis` should not be presented as the current visible product identity
- new docs, new branding copy, new installer text, and new UI copy should prefer `D.A.I.S.E.`

## Surface Doctrine

### Primary local shell

- `Blackfong Workspace`

### Secondary cockpit

- `Thoughtglass HUD`

### Fallback/home surface

- TUI

Thoughtglass HUD is the browser-based secondary cockpit. It is still supported, but it is not the canonical richest operator shell anymore.

## Linux Guest Branding Doctrine

The Kubuntu/Plasma guest should express:

- product: `O.B.E.O.S.`
- machine: `Blackcomputer`
- assistant: `D.A.I.S.E.`

Branding layers expected:

- GRUB
- Plymouth
- SDDM
- Plasma splash/color identity
- wallpaper
- lock screen
- terminal/login identity text

## Packaging Rule

Branding follows stability.

That means:

- do not freeze legacy naming drift into release artifacts
- do not let old installer doctrine contradict current session doctrine
- do not let one surface tell a different identity story than another

## Copy Direction

Preferred tone:

- restrained
- sovereign
- precise
- atmospheric without becoming vague
- operator-focused

Avoid:

- generic dashboard language
- throwaway "AI app" phrasing
- mixing too many product names at the same hierarchy level
