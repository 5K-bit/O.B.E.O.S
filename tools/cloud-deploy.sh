#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${OBEOS_APP_DIR:-/opt/obeos}"
DEPLOY_KEY="${OBEOS_DEPLOY_KEY:-$HOME/.ssh/obeos_cloud_deploy_ed25519}"

log() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

wait_for_http() {
  local label="$1"
  local url="$2"
  local attempts="${3:-45}"
  local show_body="${4:-0}"
  local body
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if body="$(curl -fsS -m 5 "$url" 2>/dev/null)"; then
      if [[ "$show_body" == "1" ]]; then
        printf '%s\n' "$body"
      else
        printf '%s OK\n' "$label"
      fi
      return 0
    fi
    printf '%s not ready yet (%s/%s)\n' "$label" "$attempt" "$attempts" >&2
    sleep 2
  done

  printf 'ERROR: %s did not become ready at %s\n' "$label" "$url" >&2
  return 1
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
wait_for_http "Ollama API" "http://127.0.0.1:11434/api/tags" 15 0
wait_for_http "HUD /legion" "http://127.0.0.1:8080/legion" 45 0
wait_for_http "Event API /health" "http://127.0.0.1:8000/health" 45 1

if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  log "Ensuring private Tailscale Serve is active"
  sudo tailscale serve --bg --http=8080 http://127.0.0.1:8080 || true
  tailscale serve status || true
fi

log "Deploy complete"
