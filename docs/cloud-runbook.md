# O.B.E.O.S Cloud Node Runbook

This runbook is the operating contract for the first O.B.E.O.S cloud node.

## Current target

- EC2 instance: `i-01e8713cf54c786d1`
- Public IPv4: dynamic unless an Elastic IP is attached; use Tailscale for stable access.
- Private IPv4: `172.31.24.250`
- OS: Ubuntu 26.04 LTS (`resolute`)
- Instance type: `m7i-flex.large`
- Storage: 80GB gp3 EBS
- SSH user: `ubuntu`
- Repo slug: `obeos-cloud`
- Tailscale hostname: `obeos-cloud`
- Tailscale IPv4: `100.80.6.98`
- Private HUD URL: `https://obeos-cloud.tail12c001.ts.net/`

Do not open public inbound access to O.B.E.O.S app ports. Keep `8080`, `8000`,
`8780`, `8090`, and `11434` private.

## Verified deployment record

Last verified: 2026-07-28.

- HUD/Legion source service: `http://127.0.0.1:8080`
- Event API source service: `http://127.0.0.1:8000`
- Ollama source service: `http://127.0.0.1:11434`
- Tailscale Serve target: `https://obeos-cloud.tail12c001.ts.net/`
- Exact Tailscale Serve rebuild command:

```bash
sudo tailscale serve --bg --yes 8080
```

Expected Serve status:

```text
https://obeos-cloud.tail12c001.ts.net (tailnet only)
|-- / proxy http://127.0.0.1:8080
```

Access rule: the private HUD URL only works from devices signed into the same
Tailscale tailnet. The iPhone can load it because it is on Tailscale. Legion
cannot load it until Legion itself joins Tailscale or we add a separate bridge.

## Local update workflow

When we say “update O.B.E.O.S on the server,” use this flow:

1. Commit local source changes.
2. Push to the private GitHub repo `obeos-cloud`.
3. Pull on EC2.
4. Rebuild and restart the cloud compose stack.

Recommended local helper:

```powershell
cd "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)"
.\tools\release-obeos-cloud.ps1
.\tools\release-obeos-cloud.ps1 -Push
.\tools\release-obeos-cloud.ps1 -Push -Deploy -RemoteHost "ubuntu@obeos-cloud.tail12c001.ts.net"
```

This helper keeps the release lane honest:

- local verification first
- push only from an approved revision
- deploy only from the GitHub-backed commit that EC2 can actually pull

The default helper path uses a bounded release-smoke test profile so everyday
cloud updates stay practical. Use `-FullVerify` when you want the broader
`blackfong verify automated` pass before a bigger release.

See also:

- [cloud-release-flow.md](C:/Users/New%20User/Documents/Projects%202026/O.B.E.O.S%20(official%20blackfong%20ecosystem%20operating%20system)/docs/cloud-release-flow.md)

```bash
cd /opt/obeos
git pull --ff-only
cp -n .env.cloud.example .env.cloud
sudo docker compose -f compose.cloud.yaml up -d --build
sudo docker compose -f compose.cloud.yaml ps
```

Local first-push command after `gh auth login`:

```powershell
cd "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)"
gh repo create obeos-cloud --private --source . --remote origin --push --description "O.B.E.O.S cloud node source and deployment"
```

## First EC2 bootstrap

Preferred path: authorize Codex/local SSH once, then run the bootstrap from this
machine over SSH. If using EC2 Instance Connect manually, run the script version
instead of pasting the full package-install recipe.

```bash
bash /opt/obeos/tools/cloud-bootstrap.sh
```

The script is idempotent: it installs/updates base tools, Docker, GitHub CLI,
Tailscale, Ollama, clones or pulls the repo, deploys containers, and verifies
the local endpoints.

The older fully expanded command sequence is below for audit/debugging.

```bash
set -euo pipefail

sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg git jq lsb-release wget

# Docker Engine + Compose plugin, installed from Docker's Ubuntu repository.
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu

# GitHub CLI for private-repo clone/pull on EC2.
sudo mkdir -p -m 755 /etc/apt/keyrings
wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg |
  sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" |
  sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt-get update
sudo apt-get install -y gh

# Tailscale. This prints a login URL; open it and authorize the EC2 node.
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname obeos-cloud

# Ollama. Keep it bound to localhost for D.A.I.S.E and O.B.E.O.S services.
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull llama3.2:1b
ollama pull llama3.2:3b
ollama pull deepseek-r1:1.5b
ollama run llama3.2:1b "Reply with one short sentence confirming D.A.I.S.E backend is online."
```

After the bootstrap, log out and back in if you want to run Docker without
`sudo`. Until then, use `sudo docker ...`.

## First source checkout

After the private GitHub repo exists and contains this project, authenticate the
EC2 host to GitHub:

```bash
gh auth login
```

Choose GitHub.com, HTTPS, and browser/device-code login.

Then clone:

```bash
sudo mkdir -p /opt/obeos
sudo chown ubuntu:ubuntu /opt/obeos
gh repo clone <OWNER>/obeos-cloud /opt/obeos
cd /opt/obeos
cp .env.cloud.example .env.cloud
sudo docker compose -f compose.cloud.yaml up -d --build
```

Replace `<OWNER>` with the GitHub account or organization that owns the private
repo.

## Private HUD access through Tailscale

The stack binds the HUD to the EC2 loopback address. To publish it only inside
your tailnet:

```bash
sudo tailscale serve --bg --yes 8080
tailscale serve status
tailscale status
```

Then open the Tailscale URL or MagicDNS name from a device signed into the same
tailnet:

```text
https://obeos-cloud.tail12c001.ts.net/legion
```

If MagicDNS is not enabled, use the EC2 node’s Tailscale IP from
`tailscale status`.

## Verification

Run these on EC2:

```bash
uname -a
free -h
df -h
docker compose version
tailscale status
ollama list
curl -fsS http://127.0.0.1:11434/api/tags | jq .
curl -fsS http://127.0.0.1:8080/legion >/dev/null
curl -fsS http://127.0.0.1:8000/health | jq .
sudo docker compose -f /opt/obeos/compose.cloud.yaml ps
```

Expected result:

- Ollama lists `llama3.2:1b`, `llama3.2:3b`, and `deepseek-r1:1.5b`.
- HUD serves `/legion` from localhost on EC2.
- Event API returns `{"service":"blackfong_control_plane","status":"healthy"}`.
- App ports are reachable over Tailscale only, not from the public internet.

## Safety notes

- Do not copy `data/legion_pairing_token.txt` from the Windows machine.
- Do not commit `.env.cloud`.
- Do not publish Ollama port `11434`.
- Keep Blackcomputer Security local until the camera/Agent DVR tunnel is planned.
