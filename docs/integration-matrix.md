# OBEOS Integration Matrix

OBEOS v0.9 cannot leave draft status until the automated matrix passes and the manual hardware/VM gates have fresh evidence.

## Automated gates

- Ubuntu Python 3.11: DAISE cutover, contracts, existing DAISE regression, Thoughtglass, automated/surface/integration verification.
- Windows Python 3.11: same core matrix.
- Canonical child components: MaxPayne, Nightwatch, EarthGlass, BlackForge, 3000 test suites from pinned submodules.
- Full OBEOS verification after all preceding jobs pass.
- Recursive submodule status must contain no uninitialized (`-`) or drifted (`+`) entries.

## Manual/runtime gates

- Kubuntu 24.04 LTS UEFI VM boots the installer media.
- KDE Plasma + SDDM are present; XFCE/Xubuntu are absent from the shipping session.
- Blackfong Workspace, Thoughtglass, DAISE, Event API and MaxPayne health aggregation start on the installed guest.
- Nightwatch publishes a task event through Event Contract v1 and the event appears in the OBEOS event spine.
- 3000 publishes a bounded synthetic/safe test event without requiring a real camera for acceptance.
- Telegram/cloud private deployment smoke test passes.
- Legion/Thoughtglass client can read system state over the existing private path.

## Merge rule

PR #6 and installer PR #7 remain draft until all applicable automated jobs are green and the manual/runtime evidence above is recorded. A passing static installer workflow alone is not sufficient evidence for the VM gate.
