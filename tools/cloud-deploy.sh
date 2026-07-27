#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${OBEOS_APP_DIR:-/opt/obeos}"
DEPLOY_KEY="${OBEOS_DEPLOY_KEY:-$HOME/.ssh/obeos_cloud_deploy_ed25519}"

log() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

cd "$APP_DIR"

log "Updating O.B.E.O.S source"
if [[ -f "$DEPLOY_KEY" ]]; then
  chmod 600 "$DEPLOY_KEY"
  GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes" git pull --ff-only
else
  git pull --ff-only
fi

log "Deploying containers"
cp -n .env.cloud.example .env.cloud
sudo docker compose -f compose.cloud.yaml up -d --build
sudo docker compose -f compose.cloud.yaml ps

log "Checking local endpoints"
curl -fsS http://127.0.0.1:8080/legion >/dev/null && echo "HUD /legion OK"
curl -fsS http://127.0.0.1:8000/health
echo

if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  log "Ensuring private Tailscale Serve is active"
  sudo tailscale serve --bg --http=8080 http://127.0.0.1:8080 || true
  tailscale serve status || true
fi

log "Deploy complete"
