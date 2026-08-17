#!/usr/bin/env bash
set -uo pipefail

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures+1)); }
check_cmd() { local label="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$label"; else fail "$label"; fi; }
check_http() { local label="$1" url="$2"; if curl -fsS --max-time 8 "$url" >/dev/null; then pass "$label"; else fail "$label"; fi; }

echo 'OBEOS CLOUD RUNTIME ACCEPTANCE'
echo '------------------------------'

check_cmd 'Docker available' docker --version
check_cmd 'Tailscale connected' tailscale status
check_http 'Ollama reachable' 'http://127.0.0.1:11434/api/tags'
check_http 'OBEOS HUD / Legion surface' 'http://127.0.0.1:8080/legion'
check_http 'OBEOS Event API health' 'http://127.0.0.1:8000/health'

for service in obeos-hud obeos-event-api obeos-telegram; do
  if docker inspect -f '{{.State.Status}}' "$service" 2>/dev/null | grep -qx running; then
    pass "container $service running"
  else
    fail "container $service running"
  fi
done

if docker exec obeos-telegram python -m blackfong_ecosystem.cli telegram status >/tmp/obeos-telegram-status.json 2>/dev/null; then
  pass 'Telegram gateway status command'
  cat /tmp/obeos-telegram-status.json
else
  fail 'Telegram gateway status command'
fi

if docker compose -f compose.cloud.yaml ps >/tmp/obeos-compose-ps.txt 2>/dev/null; then
  pass 'compose.cloud.yaml readable'
  cat /tmp/obeos-compose-ps.txt
else
  fail 'compose.cloud.yaml readable'
fi

echo '------------------------------'
if [ "$failures" -eq 0 ]; then
  echo 'RESULT: PASS'
  exit 0
fi

echo "RESULT: FAIL ($failures failed checks)"
exit 1
