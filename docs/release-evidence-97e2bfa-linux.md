# O.B.E.O.S. Release Evidence

> **Scope of this evidence:** Linux only (Ubuntu-class container, Python 3.12.3), `-m quick` profile. It has **not** been run on Windows 11 for this commit, and the `integration`, `packaging`, `remaster` and `vision` profiles, the submodules' own tests, and the manual checks below are not covered. Earlier Windows runs on `main` and on `fix/numpy-pin` are not evidence for this commit.

Generated 2026-10-31T14:10:08+00:00 by `tools/release_evidence.py`. This file supersedes `docs/release-evidence-latest.md` for any claim it makes. Tested commit: `97e2bfa`; this file was committed afterwards.

## Summary

| | |
|---|---|
| Commit | `97e2bfad9cfea74315902be8d901b9ac5a9dbe81` |
| Branch | `detached HEAD (requested ref: origin/evidence/main-plus-workspace)` |
| Mode | fresh clone |
| Working tree | clean |
| OS | Linux-6.18.44-fc-v185-x86_64-with-glibc2.39 |
| Python | Python 3.12.3 |
| pytest | pytest 9.2.0 |
| Automated tests | **passed** |
| Manual checks | **6 of 6 pending** |

## Automated tests

Command: `/home/claude/envobeos/bin/python -m pytest tests/ -m quick -v --junitxml=/home/claude/evidence-linux2/junit.xml`

Collection: 581 selected, 0 collection error(s). Exit code 0; 581 tests, 0 failed, 0 errors, 0 skipped, 3 expected failures (xfail), 60s. Full output: `pytest-run.log`, `junit.xml`; installed packages: `pip-freeze.txt`.

## Known failing tests (marked xfail): 3

These tests are expected to fail and do not fail the run. Each is a known gap, not a pass.

- `tests.test_blackfong_cortex_thoughtglass::test_cognition_gesture_route_updates_focus_and_layer`: Triage B04: selecting node 'tinyprogrammer' returns no selected_node here. Unverified whether the node only exists when TinyProgrammer is online (environmental) or the graph changed; needs owner triage.
- `tests.test_daise_file_chat_and_cognition::test_daise_handles_governed_file_proposal_from_chat`: Triage B04: DAISE now defers file mutation until approval/consent is present, so no proposal is created from chat. Owner must decide whether chat should still create proposals (then update the code) or pass consent (then update this test). The policy was NOT loosened to satisfy the test.
- `tests.test_memory_chamber_unified_graph::test_memory_chamber_routes_and_daise_graph_context`: Triage B04: the request now routes to singular DAISE and no memory_retrieval event is emitted, so the graph_neighborhoods['jarvis'] assertions cannot hold. Unverified whether dropping that event is intended; needs owner triage.

## Pinned components

| Path | Pinned commit | Initialized | Matches pin |
|---|---|---|---|
| `3000-main` | `50984cc768e0d0fb7cd7be5e1c120c39bd3f9195` | yes | yes |
| `Blackforge` | `e30c74bad1e18fec4fb8b8a68b7f1ef6c9c21945` | yes | yes |
| `EarthGlass` | `adc421b3847f99d0ce3fe0203627a5e10f8a6a3e` | yes | yes |
| `Max-Payne` | `cd53877200d1a1f6d3e2efcd844ac36eaaadf9db` | yes | yes |
| `Night-watch-OS` | `e0ee75364bbdf37081f5952250e21b15700f206b` | yes | yes |

Component tests under `3000-main/`, `Blackforge/`, `EarthGlass/` and the other submodules are not run by `tests/`; a component that is not initialized was not tested at all.

## Manual checks

_docs/release-manual-checks.json read from the evidence script's checkout; the tested commit has none_

## Run notes

- submodule init exit code 0
- --no-install: used the invoking Python and its existing packages, not a new venv

## What this does and does not show

- Shows: the tests selected by the marker expression ran against the commit above, in the environment above.
- Does not show: that the release gate in `docs/obeos-v0.9-release-gate.md` is met. Pending manual checks, uninitialized components, and any unselected marker profile (`integration`, `packaging`, `remaster`, `vision`) remain unverified.

## Reproduce

```
python tools/release_evidence.py --fresh --markers quick
```
