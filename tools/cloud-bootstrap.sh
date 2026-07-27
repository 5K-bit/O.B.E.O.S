#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${OBEOS_REPO:-git@github.com:5K-bit/obeos-cloud.git}"
APP_DIR="${OBEOS_APP_DIR:-/opt/obeos}"
HOSTNAME="${OBEOS_TAILSCALE_HOSTNAME:-obeos-cloud}"
DEPLOY_KEY="${OBEOS_DEPLOY_KEY:-$HOME/.ssh/obeos_cloud_deploy_ed25519}"
MODELS="${OBEOS_OLLAMA_MODELS:-llama3.2:1b llama3.2:3b deepseek-r1:1.5b}"

log() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

apt_install_base() {
  log "Installing base packages"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl gnupg git jq lsb-release openssh-client wget
}

install_docker() {
  if need_cmd docker && docker compose version >/dev/null 2>&1; then
    log "Docker already installed"
    return
  fi

  log "Installing Docker Engine and Compose plugin"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  local codename
  codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  if ! sudo apt-get update || ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
    log "Docker apt repository path failed; falling back to Docker convenience installer"
    curl -fsSL https://get.docker.com | sudo sh
  fi

  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER" || true
}

install_gh() {
  if need_cmd gh; then
    log "GitHub CLI already installed"
    return
  fi

  log "Installing GitHub CLI"
  sudo mkdir -p -m 755 /etc/apt/keyrings
  wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg |
    sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" |
    sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y gh
}

install_tailscale() {
  if need_cmd tailscale; then
    log "Tailscale already installed"
  else
    log "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  sudo systemctl enable --now tailscaled

  if tailscale status >/dev/null 2>&1; then
    log "Tailscale already authenticated"
  else
    log "Starting Tailscale login for hostname ${HOSTNAME}"
    sudo tailscale up --hostname "$HOSTNAME"
  fi
}

install_ollama() {
  if need_cmd ollama; then
    log "Ollama already installed"
  else
    log "Installing Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
  fi

  sudo systemctl enable --now ollama

  for model in $MODELS; do
    log "Ensuring Ollama model ${model}"
    ollama pull "$model"
  done
}

checkout_repo() {
  log "Preparing ${APP_DIR}"
  sudo mkdir -p "$APP_DIR"
  sudo chown "$USER:$USER" "$APP_DIR"

  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  ssh-keyscan github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
  sort -u "$HOME/.ssh/known_hosts" -o "$HOME/.ssh/known_hosts" || true

  local git_ssh=""
  if [[ -f "$DEPLOY_KEY" ]]; then
    chmod 600 "$DEPLOY_KEY"
    git_ssh="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes"
  fi

  if [[ -d "$APP_DIR/.git" ]]; then
    log "Updating existing checkout"
    if [[ -n "$git_ssh" ]]; then
      GIT_SSH_COMMAND="$git_ssh" git -C "$APP_DIR" pull --ff-only
    else
      git -C "$APP_DIR" pull --ff-only
    fi
    return
  fi

  log "Cloning ${REPO}"
  if [[ -n "$git_ssh" ]]; then
    GIT_SSH_COMMAND="$git_ssh" git clone "$REPO" "$APP_DIR"
  elif need_cmd gh && gh auth status >/dev/null 2>&1; then
    gh repo clone 5K-bit/obeos-cloud "$APP_DIR"
  else
    cat >&2 <<MSG
No GitHub deploy key was found at:
  $DEPLOY_KEY

Either copy the deploy key there or run:
  gh auth login

Then re-run this script.
MSG
    exit 12
  fi
}

deploy_stack() {
  log "Deploying O.B.E.O.S cloud stack"
  cd "$APP_DIR"
  cp -n .env.cloud.example .env.cloud
  sudo docker compose -f compose.cloud.yaml up -d --build
  sudo docker compose -f compose.cloud.yaml ps

  if need_cmd tailscale && tailscale status >/dev/null 2>&1; then
    log "Publishing HUD privately with Tailscale Serve"
    sudo tailscale serve --bg --http=8080 http://127.0.0.1:8080 || true
    tailscale serve status || true
  fi
}

verify() {
  log "Verification"
  uname -a
  free -h
  df -h /
  docker compose version
  ollama list || true
  curl -fsS http://127.0.0.1:11434/api/tags | jq '.models | length' || true
  curl -fsS http://127.0.0.1:8080/legion >/dev/null && echo "HUD /legion OK"
  curl -fsS http://127.0.0.1:8000/health | jq .
}

main() {
  apt_install_base
  install_docker
  install_gh
  install_tailscale
  install_ollama
  checkout_repo
  deploy_stack
  verify
  log "O.B.E.O.S cloud node bootstrap complete"
}

main "$@"
