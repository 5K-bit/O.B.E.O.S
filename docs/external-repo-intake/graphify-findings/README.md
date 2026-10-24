# Graphify Findings

## Completed Baseline Scan

The first O.B.E.O.S code scan completed on 2026-08-26. Its curated result is in
`obeos-code-scan-2026-08-26.md`.

The scan used Graphify `v8` at
`b2cd36267456c166788c95be6e68574064a92a42`, against O.B.E.O.S commit
`307b68cb5aac71a39347a277fa0df65cee5bbe19`.

It was a local, code-only AST scan: no model backend, API key, documents, media,
or credential data was used. The source workspace had local, uncommitted changes
at scan time; the report records the committed baseline for reproducibility and
does not treat the working tree as a release artifact.

## Reproduce The Baseline

Run this outside the O.B.E.O.S repository so scan output remains untracked:

```powershell
$reviewRoot = "C:\Users\New User\Documents\Projects 2026\external-repo-review"
$obeosRoot = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)"
git clone --depth 1 --branch v8 https://github.com/5K-bit/graphify.git "$reviewRoot\graphify"
uv tool run --from "$reviewRoot\graphify" graphify extract "$obeosRoot" --code-only --out "$reviewRoot\obeos-scan" --max-workers 4
uv tool run --from "$reviewRoot\graphify" graphify cluster-only "$reviewRoot\obeos-scan" --no-label --no-viz
```

The raw `graph.json` and generated `GRAPH_REPORT.md` remain in the separate
review workspace. They are deliberately not committed because the graph is about
9 MB and the no-label report mainly repeats anonymous community lists. The curated
report preserves the useful metrics, architecture findings, and reproduction steps.

## Future Passes

- `graphify-GRAPH_REPORT.md`
- `openhuman-GRAPH_REPORT.md`
- `gotify-server-GRAPH_REPORT.md`
- comparison notes connecting findings back to O.B.E.O.S component, event, health,
  and HUD boundaries.

Do not store credentials, private environment files, local pairing tokens, or large
generated caches here.
