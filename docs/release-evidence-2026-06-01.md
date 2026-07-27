# O.B.E.O.S. Release Evidence Snapshot (2026-06-01)

## Purpose

This note captures dated packaging-readiness evidence from the current repo state instead of relying only on doctrine docs or remembered local runs.

This snapshot predates the automated evidence command. The current regeneration path is:

```powershell
blackfong verify evidence --profile amd64-pc
```

The latest generated automation output is expected at [release-evidence-latest.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-evidence-latest.md).

## Commands Captured

### 1. Installer status

Command:

```powershell
.\.venv\Scripts\python.exe -m blackfong_ecosystem.cli install-plan status
```

Observed result:

- `state`: `migration_input_only`
- `shipping_ready`: `false`
- `recommended_v1_path`: `kubuntu_materialize_remaster`
- external installer root exists

Meaning:

- the external `Blackfong-Installer-main` project is still useful input
- it is not yet the shipping O.B.E.O.S. v1 installer truth

### 2. Installer manifest validation

Command:

```powershell
.\.venv\Scripts\python.exe -m blackfong_ecosystem.cli install-plan validate --profile amd64-pc
```

Observed result:

- validation status: `ok: true`
- required manifest files were present
- bundle display names reflect doctrine-clean visible labels such as `D.A.I.S.E Service` and `D.A.I.S.E Compatibility Assistant Path`

Meaning:

- installer-plan validation is currently honest and functional
- compatibility bundle ids still exist internally, but the visible export layer is cleaner than before

### 3. O.B.E.O.S. manifest

Command:

```powershell
.\.venv\Scripts\python.exe -m blackfong_ecosystem.cli obeos manifest
```

Observed result:

- base distribution: `Kubuntu 24.04 LTS`
- desktop environment: `KDE Plasma`
- session authority rule: `OBEOS owns the session. Plasma is a managed workspace.`
- packaging rule: `Packaging follows stability.`
- native shell doctrine and guest bootstrap contract were returned successfully

Meaning:

- the repo's current release doctrine is now clearly Kubuntu + Plasma, not the older XFCE/Xubuntu baseline

### 4. Surface verification launcher note

Attempted command:

```powershell
.\.venv\Scripts\python.exe -m blackfong_ecosystem.cli verify surfaces
```

Observed result inside the Codex shell:

- launch failed before verification completed
- reported message: `Unable to create process using '"C:\Users\New User\AppData\Local\Programs\Python\Python311\python.exe" -m blackfong_ecosystem.cli verify surfaces'`

Additional environment notes from this same capture:

- `python` was not available on `PATH`
- `py` launcher was not available on `PATH`
- direct invocation of `C:\Users\New User\AppData\Local\Programs\Python\Python311\python.exe` from this shell reported `Access is denied`

Interpretation:

- this evidence currently points to a local launcher/environment limitation inside the capture shell, not a proven regression in the verification contract itself
- however, packaging-ready operator workflows should not depend on a fragile interpreter path, so launcher reliability still deserves tracking

## Current Packaging Truth

What this evidence now supports:

- the repo has a coherent O.B.E.O.S. v1 doctrine
- the native-shell-first Kubuntu/Plasma path is explicit
- the external installer is now honestly labeled as migration/build input only
- installer-plan validation is operational

What this evidence does not prove yet:

- a fresh screenshot set has been captured
- the release checklist has been walked end-to-end on a clean operator path
- the external installer has been promoted to Kubuntu/Plasma shipping truth
- the verification command launcher path is robust across operator environments

## Linked Follow-Up

- [obeos-packaging-readiness-plan.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/obeos-packaging-readiness-plan.md)
- [release-checklist-v1.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-checklist-v1.md)
- [release-screenshot-shotlist.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-screenshot-shotlist.md)
