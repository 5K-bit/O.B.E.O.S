#!/usr/bin/env bash
set -euo pipefail

HUB_URL="${BLACKFONG_HUB_URL:-${1:-http://blackcomputer:8080}}"
DEVICE_ID="${BLACKFONG_SENTINEL_DEVICE_ID:-sentinel}"
DEVICE_LABEL="${BLACKFONG_SENTINEL_DEVICE_LABEL:-Sentinel}"
INTERVAL="${BLACKFONG_SENTINEL_INTERVAL:-10}"
TOKEN="${BLACKFONG_SENTINEL_AGENT_TOKEN:-${2:-}}"

if [[ -z "${TOKEN}" ]]; then
  echo "Set BLACKFONG_SENTINEL_AGENT_TOKEN or pass the token as the second argument." >&2
  exit 2
fi

python -m blackfong_ecosystem.cli sentinel agent \
  --hub "${HUB_URL}" \
  --device-id "${DEVICE_ID}" \
  --label "${DEVICE_LABEL}" \
  --token "${TOKEN}" \
  --interval "${INTERVAL}"
