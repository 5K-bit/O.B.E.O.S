# O.B.E.O.S. v1 Release Checklist

## Purpose

This checklist is the concrete operator handoff for calling an O.B.E.O.S. build packaging-ready enough for a trial installer or remaster release.

Use this after doctrine, branding, and runtime work are mostly stable.

When you run this checklist for a real packaging pass, generate and archive a fresh evidence snapshot with:

```powershell
blackfong verify evidence --profile amd64-pc
```

The latest generated Markdown snapshot will be written to [release-evidence-latest.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-evidence-latest.md).

## 1. Doctrine Freeze

Confirm all of these are true:

- product name is `O.B.E.O.S.`
- governing spine is `Blackfong`
- machine identity is `Blackcomputer`
- visible assistant is `D.A.I.S.E.`
- canonical local shell is `Blackfong Workspace`
- live browser cockpit is `Thoughtglass HUD`
- graph/archive workbench is `Memory Chamber`

Evidence:

- [README.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/README.md)
- [branding-doctrine.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/branding-doctrine.md)
- [release-surface-hierarchy.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-surface-hierarchy.md)

## 2. Surface Branding

Verify visible labels and titles on:

- Blackfong Workspace window title
- Thoughtglass Home tab
- Memory Chamber tab
- D.A.I.S.E. chat labels
- Thoughtglass HUD title and heading
- TUI boot-home header
- degraded/fallback shell messaging

Pass criteria:

- no visible `jarvis` identity on release-facing surfaces
- no visible `BLACKFONG CORTEX` branding unless intentionally reintroduced as doctrine
- no surface implies the browser cockpit is the primary product shell

## 3. Runtime Verification

Run:

```powershell
blackfong verify automated
blackfong verify surfaces
blackfong verify integrations
blackfong desktop start
blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser
```

Confirm:

- Blackfong Workspace launches
- Thoughtglass and Memory Chamber are visible
- Thoughtglass HUD responds
- status truth is honest for warming, ready, degraded, and offline states
- TinyProgrammer state is reported honestly

## 4. Packaging / Artifact Verification

Run:

```powershell
blackfong install-plan status
blackfong obeos manifest
blackfong obeos materialize --output data\obeos-remaster
blackfong obeos vm-checklist
blackfong obeos vm-acceptance-snapshot
blackfong install-plan validate --profile amd64-pc
```

Confirm:

- installer status clearly reports whether the external installer is shipping truth or migration-only input
- remaster bundle materializes successfully
- branding assets are emitted
- VM checklist and acceptance snapshot exist
- install-plan output is honest about installer status
- no artifact implies the external installer is already Kubuntu/Plasma-aligned if it is not

## 5. Linux Guest Branding

Confirm the materialized guest bundle includes:

- GRUB branding
- Plymouth branding
- SDDM branding
- Plasma splash / wallpaper / lock-screen defaults
- TUI home service
- degraded boot fallback contract

Evidence:

- [release-artifacts.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-artifacts.md)
- generated `data\obeos-remaster` bundle

## 6. Docs Completeness

Confirm these docs are current:

- [README.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/README.md)
- [OBEOS user guide.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/OBEOS%20user%20guide.md)
- [branding-doctrine.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/branding-doctrine.md)
- [release-surface-hierarchy.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-surface-hierarchy.md)
- [release-artifacts.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-artifacts.md)
- [release-screenshot-shotlist.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-screenshot-shotlist.md)
- [installer-path-comparison.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/installer-path-comparison.md)

## 7. Screenshot Set

Before a public packaging trial, capture:

- Blackfong Workspace landing on Thoughtglass Home
- Memory Chamber cluster view
- D.A.I.S.E. chat in Workspace
- Thoughtglass HUD browser view
- TUI boot-home
- Kubuntu guest branded login / wallpaper / splash

Use `blackfong verify shotset` for the native workspace captures before falling back to manual screenshots.

See:

- [release-screenshot-shotlist.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-screenshot-shotlist.md)

## 8. Installer Decision Gate

Before packaging, explicitly decide one of these:

1. external installer is now a real shipping installer path
2. external installer remains migration/build input only

If option 2 is still true, every release-facing doc must say so clearly.

## 9. Trial Release Sign-Off

O.B.E.O.S. is only ready for a trial packaged release when:

- the doctrine is singular
- the visible surfaces tell the same story
- the remaster/materialize path is repeatable
- the installer story is honest
- the docs are complete enough for a first external operator
- the screenshot set matches the actual current product
