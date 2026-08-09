# O.B.E.O.S Cloud Release Flow

This is the working release contract for O.B.E.O.S across the three real
surfaces we now have:

- local Windows repo: build, test, inspect, approve
- private GitHub repo `5K-bit/obeos-cloud`: history, sync, rollback point
- EC2 runtime at `/opt/obeos`: private deployed node

The rule is simple:

1. edit locally
2. validate locally
3. push approved commits to GitHub
4. pull and restart on EC2

Do not treat the EC2 copy as a second development checkout unless there is an
emergency. Runtime drift is what this flow is meant to prevent.

## Current authority map

- Local source root: `C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)`
- GitHub source repo: `5K-bit/obeos-cloud`
- Cloud app root: `/opt/obeos`
- Private cloud URL: `https://obeos-cloud.tail12c001.ts.net/`

## Recommended release rhythm

### 1. Validate the local checkout

Run the release helper with no push or deploy step first:

```powershell
cd "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)"
.\tools\release-obeos-cloud.ps1
```

By default this:

- shows local git status
- runs a bounded release smoke pytest profile for the active UI, retrieval, and
  cloud-facing contracts
- fetches `origin/main`
- tells you whether local HEAD is ahead of or behind GitHub

For a deeper pre-release pass, use:

```powershell
.\tools\release-obeos-cloud.ps1 -FullVerify
```

That runs the existing `blackfong verify automated` workflow, which is broader
and intentionally heavier than the default smoke pass.

### 2. Push the approved revision

When the local revision is the one you want to keep, push it:

```powershell
.\tools\release-obeos-cloud.ps1 -Push
```

The helper refuses to push if the working tree is dirty or if the branch is
behind `origin/main`.

### 3. Deploy to the private cloud node

When GitHub contains the approved revision, deploy it to EC2:

```powershell
.\tools\release-obeos-cloud.ps1 -Push -Deploy -RemoteHost "ubuntu@obeos-cloud.tail12c001.ts.net"
```

You can also store the host once:

```powershell
$env:OBEOS_CLOUD_SSH_TARGET = "ubuntu@obeos-cloud.tail12c001.ts.net"
.\tools\release-obeos-cloud.ps1 -Push -Deploy
```

The deploy step SSHs to the EC2 node and runs:

```bash
cd /opt/obeos
bash tools/cloud-deploy.sh
```

That remote script:

- pulls the latest committed source
- rebuilds the compose stack
- verifies the HUD, Event API, and knowledge status routes
- reasserts private Tailscale Serve when available

## Fast manual fallback

If you ever want to bypass the local helper and deploy manually on EC2, use:

```bash
cd /opt/obeos
bash tools/cloud-deploy.sh
```

## Suggested approval discipline

Use this checklist before `-Push`:

- the local workspace behavior is the one you want to preserve
- the git diff reflects intentional changes only
- verification completed cleanly
- there are no emergency edits living only on EC2

## What not to do

- Do not edit EC2 first and hope to backfill Git later.
- Do not push half-approved work just because it passes locally.
- Do not deploy from a dirty local checkout unless you intentionally want the
  last committed revision only.

## Related docs

- [cloud-runbook.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/cloud-runbook.md)
- [release-checklist-v1.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/release-checklist-v1.md)
