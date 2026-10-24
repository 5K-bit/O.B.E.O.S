# External Repo Intake Plan

Created: 2026-08-25

This packet captures the plan for reviewing three user-forked repositories for possible O.B.E.O.S compatibility:

| Repository | Branch | Ref checked | Intended role |
| --- | --- | --- | --- |
| https://github.com/5K-bit/graphify | `v8` | `b2cd36267456c166788c95be6e68574064a92a42` | First-pass architecture mapper and code graph tool |
| https://github.com/5K-bit/openhuman | `main` | `522112017564c8b7d904747f1129ddee1211ee22` | Local-first AI architecture reference |
| https://github.com/5K-bit/server | `master` | `693303453c79fc1235067a8121b9c8e849a54f83` | Future notification transport candidate |

## Review Goal

Produce a clean, source-backed compatibility review before adding runtime integration. The review should determine where Graphify, OpenHuman, and Gotify/server can coexist with O.B.E.O.S without replacing D.A.I.S.E, the event backbone, the Legion HUD, or existing component ownership boundaries.

## Commands Used To Check Refs

```powershell
git ls-remote https://github.com/5K-bit/graphify.git refs/heads/v8
git ls-remote https://github.com/5K-bit/openhuman.git refs/heads/main
git ls-remote https://github.com/5K-bit/server.git refs/heads/master
```

## O.B.E.O.S Compatibility Rules

- Keep canonical projects in their own repositories.
- Add integration through O.B.E.O.S component manifests, adapters, events, or health probes.
- Publish cross-boundary activity through Event Contract v1 with namespaced event types.
- Keep D.A.I.S.E as the authoritative conversational intelligence layer.
- Keep the Legion HUD stable while new integration research is underway.

## Planned Outputs

- `compatibility-matrix.md`: repo-by-repo fit, risks, and boundaries.
- `integration-roadmap.md`: recommended order of work.
- `graphify-findings/`: future generated Graphify reports and notes.
