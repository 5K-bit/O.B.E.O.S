# O.B.E.O.S. Release Screenshot Shot List

## Purpose

This shot list defines the minimum branded screenshots needed before packaging, installer trials, or release-facing docs are treated as polished.

For the native workspace surfaces, start with the automated capture path:

```powershell
blackfong verify shotset
```

That command currently generates offscreen workspace captures into [docs/release-shots](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-shots). Keep using manual capture for any surfaces that still require a live browser, guest boot screen, or real hardware state.

## Core Surface Shots

### 1. Blackfong Workspace Home

Capture:

- app title visible
- Thoughtglass Home active
- top-level status truth visible
- graph readable and not overloaded

Must show:

- `Blackfong Workspace`
- `Thoughtglass`
- startup truth such as `WARMING`, `READY`, or `DEGRADED`

### 2. Memory Chamber

Capture:

- Memory Chamber tab active
- cluster or archive-focused view
- inspector visible
- search or neighborhood tools visible

Must communicate:

- this is the deeper archive/analysis surface
- it is not visually identical to Thoughtglass Home

### 3. D.A.I.S.E. Chat

Capture:

- D.A.I.S.E. chat active in the native workspace
- agent/status line visible
- a believable example conversation

Must avoid:

- `jarvis` as visible assistant name

### 4. Thoughtglass HUD

Capture:

- browser-based cockpit
- `Thoughtglass HUD` heading
- session or cognition status visible
- chat or graph panel visible

Must communicate:

- browser-based secondary cockpit
- consistent family resemblance with Blackfong Workspace

### 5. TUI Boot-Home

Capture:

- boot-home menu
- O.B.E.O.S. / Blackcomputer / D.A.I.S.E. identity
- clean terminal presentation

Must communicate:

- fallback/home surface
- not just a generic Linux shell

## Guest Branding Shots

### 6. Login / Boot Identity

Capture at least one of:

- GRUB
- Plymouth
- SDDM

Best case:

- show more than one branding layer if the guest bundle is stable enough

### 7. Plasma Session Branding

Capture:

- wallpaper
- color identity
- launcher or return-home integration

Must communicate:

- O.B.E.O.S. is enhancing Kubuntu/Plasma, not pretending to be an unrelated desktop

### 8. Degraded Fallback Truth

Capture:

- a degraded/fallback surface or diagnostic state
- honest wording about what failed and what remains available

Must communicate:

- the platform tells the truth when a subsystem is degraded

## Supporting Shots

### 9. Installer / Remaster Evidence

Capture:

- materialized bundle structure
- manifest or checklist output
- install-plan validation output

Use for:

- packaging docs
- installer status notes
- doctrine honesty when the installer path is still transitional

### 10. TinyProgrammer State

Capture if stable:

- Tiny active or warming inside workspace/HUD surfaces
- honest offline/degraded state if unavailable

## Shot Quality Rules

- avoid placeholder lorem ipsum or empty demos where possible
- prefer truthful real system states over mocked perfection
- avoid crowded or broken Thoughtglass views
- capture dark surfaces cleanly with readable contrast
- crop to the meaningful branded area, but do not hide status truth

## Suggested Release Order

Present screenshots in this order:

1. Blackfong Workspace Home
2. Memory Chamber
3. D.A.I.S.E. chat
4. Thoughtglass HUD
5. TUI boot-home
6. Kubuntu/Plasma branding
7. installer/remaster evidence
