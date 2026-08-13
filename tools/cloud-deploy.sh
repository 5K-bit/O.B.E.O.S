#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${OBEOS_APP_DIR:-/opt/obeos}"
DEPLOY_KEY="${OBEOS_DEPLOY_KEY:-$HOME/.ssh/obeos_cloud_deploy_ed25519}"
BRANCH="$(git -C "${OBEOS_APP_DIR:-/opt/obeos}" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'main')"
DEPLOY_HISTORY_FILE="${OBEOS_DEPLOY_HISTORY_FILE:-$APP_DIR/data/deploy-history.log}"

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

before_ref="$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
log "Current revision: ${before_ref} on branch ${BRANCH}"

log "Updating O.B.E.O.S source"
if [[ -f "$DEPLOY_KEY" ]]; then
  chmod 600 "$DEPLOY_KEY"
  GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes" git pull --ff-only
else
  git pull --ff-only
fi
after_ref="$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
log "Updated revision: ${after_ref}"
git status --short --branch || true

mkdir -p "$(dirname "$DEPLOY_HISTORY_FILE")"
printf '%s branch=%s before=%s after=%s host=%s\n' \
  "$(date -Is)" \
  "$BRANCH" \
  "$before_ref" \
  "$after_ref" \
  "$(hostname)" >>"$DEPLOY_HISTORY_FILE"

log "Deploying containers"
cp -n .env.cloud.example .env.cloud
sudo docker compose -f compose.cloud.yaml up -d --build --remove-orphans
sudo docker compose -f compose.cloud.yaml ps

log "Checking local endpoints"
wait_for_http "Ollama API" "http://127.0.0.1:11434/api/tags" 15 0
wait_for_http "HUD /legion" "http://127.0.0.1:8080/legion" 45 0
wait_for_http "HUD /api/knowledge/status" "http://127.0.0.1:8080/api/knowledge/status?ensure_ecosystem=false" 45 0
wait_for_http "Event API /health" "http://127.0.0.1:8000/health" 45 1

knowledge_status_json="$(curl -fsS http://127.0.0.1:8080/api/knowledge/status?ensure_ecosystem=false)"
if python3 - "$knowledge_status_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
source_count = int(payload.get("registered_source_count") or 0)
collection_count = int(payload.get("collection_count") or 0)
raise SystemExit(0 if source_count == 0 or collection_count == 0 else 1)
PY
then
  log "Hydrating knowledge runtime because the live authority is empty"
  curl -fsS -X POST "http://127.0.0.1:8080/api/knowledge/sync" \
    -H "Content-Type: application/json" \
    -d '{"background": false, "max_files_per_root": 80}' >/tmp/obeos-knowledge-sync.json
  cat /tmp/obeos-knowledge-sync.json
fi

if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  log "Ensuring private Tailscale Serve is active"
  sudo tailscale serve --bg --yes 8080 || true
  tailscale serve status || true
fi

log "Deploy complete at revision ${after_ref}"
