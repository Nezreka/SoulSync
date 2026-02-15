#!/usr/bin/env bash

# ============================================================================
#  SoulSync Installer for Proxmox LXC
# ============================================================================
#  Installs SoulSync and all dependencies (slskd, Python, ffmpeg, etc.)
#  inside a Debian/Ubuntu-based Proxmox LXC container.
#
#  Usage:
#    bash -c "$(curl -fsSL https://raw.githubusercontent.com/tanujdargan/SoulSync/main/scripts/proxmox/soulsync-install.sh)"
#
#  Requirements:
#    - Debian 12+ or Ubuntu 22.04+ LXC container
#    - Root privileges
#    - Network connectivity
#    - x86_64 / amd64 architecture
# ============================================================================

set -Eeuo pipefail

# -- Constants ----------------------------------------------------------------

APP="SoulSync"
SOULSYNC_REPO="https://github.com/tanujdargan/SoulSync.git"
SOULSYNC_DIR="/opt/soulsync"
SLSKD_DIR="/opt/slskd"
SOULSYNC_USER="soulsync"
SOULSYNC_GROUP="soulsync"
LOGFILE="/var/log/soulsync-install.log"

SOULSYNC_PORT=8008
SLSKD_PORT=5030
SPOTIFY_CB_PORT=8888
TIDAL_CB_PORT=8889

# -- Colors & Formatting (Proxmox community scripts style) --------------------

RD='\033[01;31m'
GN='\033[1;92m'
YW='\033[33m'
YWB='\033[93m'
BL='\033[36m'
BGN='\033[4;92m'
DGN='\033[32m'
CL='\033[m'
BOLD='\033[1m'
DIM='\033[2m'
ITALIC='\033[3m'
TAB='  '
BFR="\\r\\033[K"

CM="${TAB}✔️${TAB}${CL}"
CROSS="${TAB}✖️${TAB}${CL}"
INFO="${TAB}💡${TAB}${CL}"
NETWORK="${TAB}📡${TAB}${CL}"
CREATING="${TAB}🚀${TAB}${CL}"
GEAR="${TAB}⚙️${TAB}${CL}"
LOCK="${TAB}🔒${TAB}${CL}"
MUSIC="${TAB}🎵${TAB}${CL}"
HOURGLASS="${TAB}⏳${TAB}${CL}"

SPINNER_PID=""

# -- Ensure log file exists ---------------------------------------------------

touch "$LOGFILE"

# -- Spinner & Message Functions -----------------------------------------------

spinner() {
  local chars="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
  local i=0
  while true; do
    printf "\r${TAB}${YWB}%s${CL} %s" "${chars:i++%${#chars}:1}" "$1" >&2
    sleep 0.1
  done
}

stop_spinner() {
  if [[ -n "${SPINNER_PID}" ]]; then
    kill "$SPINNER_PID" 2>/dev/null
    wait "$SPINNER_PID" 2>/dev/null || true
    SPINNER_PID=""
  fi
}

msg_info() {
  stop_spinner
  spinner "$1" &
  SPINNER_PID=$!
}

msg_ok() {
  stop_spinner
  printf "${BFR}${CM}${GN}%s${CL}\n" "$1" >&2
}

msg_error() {
  stop_spinner
  printf "${BFR}${CROSS}${RD}%s${CL}\n" "$1" >&2
}

msg_warn() {
  stop_spinner
  printf "${INFO}${YWB}%s${CL}\n" "$1" >&2
}

# -- Cleanup on exit -----------------------------------------------------------

cleanup() {
  stop_spinner
}
trap cleanup EXIT

# -- Error handler -------------------------------------------------------------

error_handler() {
  local exit_code=$?
  local line_number=${1:-unknown}
  stop_spinner
  msg_error "Installation failed on line ${line_number} (exit code: ${exit_code})"
  echo -e "\n${TAB}${GEAR}${DIM} Check the log for details: ${LOGFILE}${CL}\n" >&2
  exit "$exit_code"
}
trap 'error_handler ${LINENO}' ERR

# -- Helpers -------------------------------------------------------------------

# Escape special characters for JSON string values
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

generate_api_key() {
  openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 48
}

get_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "localhost"
}

# -- Header --------------------------------------------------------------------

header_info() {
  clear
  echo ""
  cat <<"BANNER"
    ____              _  ____
   / ___|  ___  _   _| |/ ___| _   _ _ __   ___
   \___ \ / _ \| | | | |\___ \| | | | '_ \ / __|
    ___) | (_) | |_| | | ___) | |_| | | | | (__
   |____/ \___/ \__,_|_||____/ \__, |_| |_|\___|
                                |___/
BANNER
  echo ""
  echo -e "  ${BL}${BOLD}Proxmox LXC Installer${CL}"
  echo -e "  ${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}"
  echo ""
}

# -- Prerequisite Checks ------------------------------------------------------

root_check() {
  if [[ "$(id -u)" -ne 0 ]]; then
    msg_error "This script must be run as root"
    echo -e "\n${TAB}Run as root or with: ${GN}sudo bash soulsync-install.sh${CL}\n" >&2
    exit 1
  fi
}

os_check() {
  if [[ ! -f /etc/os-release ]]; then
    msg_error "Cannot detect operating system"
    exit 1
  fi
  # shellcheck source=/dev/null
  source /etc/os-release
  if [[ "$ID" != "debian" && "$ID" != "ubuntu" ]]; then
    msg_error "Unsupported OS: ${ID}. This script requires Debian or Ubuntu."
    exit 1
  fi
  msg_ok "Operating System: ${ID^} ${VERSION_ID}"
}

arch_check() {
  local arch
  arch=$(dpkg --print-architecture 2>/dev/null || uname -m)
  if [[ "$arch" != "amd64" && "$arch" != "x86_64" ]]; then
    msg_error "Unsupported architecture: ${arch}. This script requires amd64/x86_64."
    exit 1
  fi
}

network_check() {
  msg_info "Checking network connectivity"
  local tries=0
  while ! ping -c 1 -W 3 github.com &>/dev/null; do
    ((tries++))
    if [[ $tries -ge 3 ]]; then
      msg_error "No network connectivity (cannot reach github.com)"
      echo -e "${TAB}${DIM}Ensure your LXC has a working network configuration.${CL}" >&2
      exit 1
    fi
    sleep 2
  done
  msg_ok "Network connectivity verified"
}

existing_install_check() {
  if [[ -f /etc/systemd/system/soulsync.service ]]; then
    echo ""
    msg_warn "An existing SoulSync installation was detected!"
    echo ""
    if prompt_confirm "Do you want to reinstall? This will overwrite the current installation" "n"; then
      msg_info "Stopping existing services"
      systemctl stop soulsync 2>/dev/null || true
      systemctl stop slskd 2>/dev/null || true
      msg_ok "Stopped existing services"
    else
      echo -e "\n${TAB}${DIM}Installation cancelled.${CL}\n" >&2
      exit 0
    fi
  fi
}

# -- Prompt Functions ----------------------------------------------------------

prompt_input() {
  local prompt="$1"
  local default="${2:-}"
  local result

  if [[ -n "$default" ]]; then
    printf "\n${TAB}${GEAR} ${YW}%s ${DIM}[%s]${CL}: " "$prompt" "$default" >&2
  else
    printf "\n${TAB}${GEAR} ${YW}%s${CL}: " "$prompt" >&2
  fi
  read -r result </dev/tty
  echo "${result:-$default}"
}

prompt_password() {
  local prompt="$1"
  local result

  printf "\n${TAB}${LOCK} ${YW}%s${CL}: " "$prompt" >&2
  read -rs result </dev/tty
  echo "" >&2
  echo "$result"
}

prompt_confirm() {
  local prompt="$1"
  local default="${2:-y}"
  local hint result

  if [[ "$default" == "y" ]]; then
    hint="Y/n"
  else
    hint="y/N"
  fi

  printf "\n${TAB}${INFO} ${YW}%s ${DIM}[%s]${CL}: " "$prompt" "$hint" >&2
  read -r result </dev/tty
  result="${result:-$default}"
  [[ "${result,,}" =~ ^(y|yes)$ ]]
}

prompt_select() {
  local prompt="$1"
  shift
  local options=("$@")

  echo "" >&2
  echo -e "${TAB}${GEAR} ${YW}${prompt}${CL}" >&2
  local i=1
  for opt in "${options[@]}"; do
    echo -e "${TAB}${TAB}${GN}${i})${CL} ${opt}" >&2
    ((i++))
  done

  local result
  while true; do
    printf "${TAB}${TAB}${YW}Select [1-%d]${CL}: " "${#options[@]}" >&2
    read -r result </dev/tty
    if [[ "$result" =~ ^[0-9]+$ ]] && ((result >= 1 && result <= ${#options[@]})); then
      echo "$result"
      return
    fi
    echo -e "${TAB}${TAB}${RD}Invalid selection, try again${CL}" >&2
  done
}

# -- Installation Steps -------------------------------------------------------

update_os() {
  msg_info "Updating system packages"
  apt-get update -qq >>"$LOGFILE" 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get -y -qq dist-upgrade >>"$LOGFILE" 2>&1
  msg_ok "System packages updated"
}

install_dependencies() {
  msg_info "Installing system dependencies"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl \
    wget \
    git \
    unzip \
    gcc \
    libc6-dev \
    libffi-dev \
    libssl-dev \
    openssl \
    ffmpeg \
    libchromaprint-tools \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    >>"$LOGFILE" 2>&1
  msg_ok "Installed system dependencies"
}

create_user_and_dirs() {
  msg_info "Creating SoulSync user and directories"

  if ! id -u "$SOULSYNC_USER" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "$SOULSYNC_DIR" "$SOULSYNC_USER" >>"$LOGFILE" 2>&1
  fi

  mkdir -p \
    "$SOULSYNC_DIR"/{config,data,logs,downloads,Transfer,Staging} \
    "$SLSKD_DIR"

  msg_ok "Created user '${SOULSYNC_USER}' and directories"
}

install_slskd() {
  msg_info "Fetching latest slskd release info"
  local slskd_version download_url

  slskd_version=$(curl -fsSL "https://api.github.com/repos/slskd/slskd/releases/latest" \
    | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')

  if [[ -z "$slskd_version" ]]; then
    msg_error "Failed to determine latest slskd version"
    exit 1
  fi
  msg_ok "Latest slskd release: ${slskd_version}"

  msg_info "Downloading slskd ${slskd_version}"
  download_url="https://github.com/slskd/slskd/releases/download/${slskd_version}/slskd-${slskd_version}-linux-x64.zip"
  wget -q "$download_url" -O /tmp/slskd.zip >>"$LOGFILE" 2>&1
  msg_ok "Downloaded slskd ${slskd_version}"

  msg_info "Extracting slskd"
  unzip -o -q /tmp/slskd.zip -d "$SLSKD_DIR" >>"$LOGFILE" 2>&1
  chmod +x "$SLSKD_DIR/slskd"
  rm -f /tmp/slskd.zip
  msg_ok "Installed slskd to ${SLSKD_DIR}"
}

configure_slskd() {
  echo "" >&2
  echo -e "${TAB}${MUSIC} ${BOLD}${BL}Soulseek Configuration${CL}" >&2
  echo -e "${TAB}${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2
  echo -e "${TAB}${DIM}These credentials connect slskd to the Soulseek P2P network.${CL}" >&2
  echo -e "${TAB}${DIM}Use your existing Soulseek account or create a new one.${CL}" >&2

  local slskd_user slskd_pass

  slskd_user=$(prompt_input "Soulseek username")
  while [[ -z "$slskd_user" ]]; do
    msg_error "Username cannot be empty"
    slskd_user=$(prompt_input "Soulseek username")
  done

  slskd_pass=$(prompt_password "Soulseek password")
  while [[ -z "$slskd_pass" ]]; do
    msg_error "Password cannot be empty"
    slskd_pass=$(prompt_password "Soulseek password")
  done

  SLSKD_API_KEY=$(generate_api_key)

  echo "" >&2
  msg_info "Writing slskd configuration"

  # Escape values for YAML (double-quoted strings)
  local esc_user esc_pass esc_key
  esc_user=$(json_escape "$slskd_user")
  esc_pass=$(json_escape "$slskd_pass")
  esc_key=$(json_escape "$SLSKD_API_KEY")

  cat >"$SLSKD_DIR/slskd.yml" <<YAML
soulseek:
  username: "${esc_user}"
  password: "${esc_pass}"
  listen_port: 50300

web:
  port: ${SLSKD_PORT}
  authentication:
    api_keys:
      soulsync:
        key: "${esc_key}"
        role: administrator
        cidr: "0.0.0.0/0"

directories:
  downloads: ${SOULSYNC_DIR}/downloads
  incomplete: ${SLSKD_DIR}/incomplete

shares:
  directories:
    - ${SOULSYNC_DIR}/downloads

flags:
  no_logo: true
YAML

  mkdir -p "$SLSKD_DIR/incomplete"
  chown -R "$SOULSYNC_USER:$SOULSYNC_GROUP" "$SLSKD_DIR"
  chmod 600 "$SLSKD_DIR/slskd.yml"

  msg_ok "Configured slskd (API key generated automatically)"
}

install_soulsync() {
  msg_info "Cloning SoulSync repository"

  if [[ -d "${SOULSYNC_DIR}/.git" ]]; then
    git -C "$SOULSYNC_DIR" pull -q origin main >>"$LOGFILE" 2>&1
  else
    git clone -q "$SOULSYNC_REPO" /tmp/soulsync-src >>"$LOGFILE" 2>&1
    # Copy repo contents into install dir (preserving our created subdirs)
    cp -a /tmp/soulsync-src/. "$SOULSYNC_DIR/"
    rm -rf /tmp/soulsync-src
  fi
  msg_ok "Cloned SoulSync repository"

  msg_info "Creating Python virtual environment"
  python3 -m venv "$SOULSYNC_DIR/venv" >>"$LOGFILE" 2>&1
  msg_ok "Created Python virtual environment"

  msg_info "Installing Python dependencies (this may take a minute)"
  "$SOULSYNC_DIR/venv/bin/pip" install --upgrade pip >>"$LOGFILE" 2>&1
  "$SOULSYNC_DIR/venv/bin/pip" install -r "$SOULSYNC_DIR/requirements-webui.txt" >>"$LOGFILE" 2>&1
  msg_ok "Installed Python dependencies"
}

configure_soulsync() {
  local ip
  ip=$(get_ip)

  # ── Spotify ──────────────────────────────────────────────────
  echo "" >&2
  echo -e "${TAB}${MUSIC} ${BOLD}${BL}Spotify Configuration${CL}" >&2
  echo -e "${TAB}${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2
  echo -e "${TAB}${DIM}Create a Spotify app at:${CL}" >&2
  echo -e "${TAB}  ${BGN}https://developer.spotify.com/dashboard${CL}" >&2
  echo -e "${TAB}${DIM}Set the redirect URI in your Spotify app to:${CL}" >&2
  echo -e "${TAB}  ${BGN}http://${ip}:${SPOTIFY_CB_PORT}/callback${CL}" >&2

  local spotify_id="SpotifyClientID"
  local spotify_secret="SpotifyClientSecret"
  local spotify_redirect="http://${ip}:${SPOTIFY_CB_PORT}/callback"

  if prompt_confirm "Configure Spotify now?"; then
    spotify_id=$(prompt_input "Spotify Client ID")
    while [[ -z "$spotify_id" ]]; do
      msg_error "Client ID cannot be empty"
      spotify_id=$(prompt_input "Spotify Client ID")
    done
    spotify_secret=$(prompt_password "Spotify Client Secret")
    while [[ -z "$spotify_secret" ]]; do
      msg_error "Client Secret cannot be empty"
      spotify_secret=$(prompt_password "Spotify Client Secret")
    done
  else
    msg_warn "Skipped — configure Spotify later via the web UI"
  fi

  # ── Tidal (optional) ─────────────────────────────────────────
  echo "" >&2
  echo -e "${TAB}${MUSIC} ${BOLD}${BL}Tidal Configuration ${DIM}(optional)${CL}" >&2
  echo -e "${TAB}${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2

  local tidal_id="TidalClientID"
  local tidal_secret="TidalClientSecret"
  local tidal_redirect="http://${ip}:${TIDAL_CB_PORT}/tidal/callback"

  if prompt_confirm "Configure Tidal now?" "n"; then
    tidal_id=$(prompt_input "Tidal Client ID")
    tidal_secret=$(prompt_password "Tidal Client Secret")
  else
    msg_warn "Skipped — configure Tidal later via the web UI"
  fi

  # ── Media Server ─────────────────────────────────────────────
  echo "" >&2
  echo -e "${TAB}${MUSIC} ${BOLD}${BL}Media Server Configuration${CL}" >&2
  echo -e "${TAB}${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2

  local media_server="plex"
  local plex_url="http://localhost:32400"
  local plex_token="PLEX_API_TOKEN"
  local jellyfin_url="http://localhost:8096"
  local jellyfin_key="JELLYFIN_API_KEY"
  local navidrome_url="http://localhost:4533"
  local navidrome_user="NAVIDROME_USERNAME"
  local navidrome_pass="NAVIDROME_PASSWORD"

  local ms_choice
  ms_choice=$(prompt_select "Which media server do you use?" "Plex" "Jellyfin" "Navidrome" "Skip (configure later)")

  case "$ms_choice" in
  1)
    media_server="plex"
    plex_url=$(prompt_input "Plex server URL" "http://localhost:32400")
    plex_token=$(prompt_input "Plex API token")
    while [[ -z "$plex_token" ]]; do
      msg_error "Token cannot be empty"
      plex_token=$(prompt_input "Plex API token")
    done
    ;;
  2)
    media_server="jellyfin"
    jellyfin_url=$(prompt_input "Jellyfin server URL" "http://localhost:8096")
    jellyfin_key=$(prompt_input "Jellyfin API key")
    while [[ -z "$jellyfin_key" ]]; do
      msg_error "API key cannot be empty"
      jellyfin_key=$(prompt_input "Jellyfin API key")
    done
    ;;
  3)
    media_server="navidrome"
    navidrome_url=$(prompt_input "Navidrome server URL" "http://localhost:4533")
    navidrome_user=$(prompt_input "Navidrome username" "admin")
    navidrome_pass=$(prompt_password "Navidrome password")
    while [[ -z "$navidrome_pass" ]]; do
      msg_error "Password cannot be empty"
      navidrome_pass=$(prompt_password "Navidrome password")
    done
    ;;
  4)
    msg_warn "Skipped — configure your media server later via the web UI"
    ;;
  esac

  # ── Timezone ─────────────────────────────────────────────────
  echo "" >&2
  echo -e "${TAB}${GEAR} ${BOLD}${BL}General Settings${CL}" >&2
  echo -e "${TAB}${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2

  local timezone
  timezone=$(prompt_input "Timezone" "America/New_York")

  # ── Write config.json ────────────────────────────────────────
  echo "" >&2
  msg_info "Writing SoulSync configuration"

  # Pass all values via environment variables for safety (handles special chars
  # in passwords, API keys, etc.) and use a quoted heredoc so bash doesn't
  # interfere with the Python code.
  _SS_MEDIA_SERVER="$media_server" \
  _SS_SPOTIFY_ID="$spotify_id" \
  _SS_SPOTIFY_SECRET="$spotify_secret" \
  _SS_SPOTIFY_REDIRECT="$spotify_redirect" \
  _SS_TIDAL_ID="$tidal_id" \
  _SS_TIDAL_SECRET="$tidal_secret" \
  _SS_TIDAL_REDIRECT="$tidal_redirect" \
  _SS_PLEX_URL="$plex_url" \
  _SS_PLEX_TOKEN="$plex_token" \
  _SS_JELLYFIN_URL="$jellyfin_url" \
  _SS_JELLYFIN_KEY="$jellyfin_key" \
  _SS_NAVIDROME_URL="$navidrome_url" \
  _SS_NAVIDROME_USER="$navidrome_user" \
  _SS_NAVIDROME_PASS="$navidrome_pass" \
  _SS_SLSKD_PORT="$SLSKD_PORT" \
  _SS_SLSKD_API_KEY="$SLSKD_API_KEY" \
  _SS_INSTALL_DIR="$SOULSYNC_DIR" \
  "$SOULSYNC_DIR/venv/bin/python3" - <<'PYEOF'
import json, os

e = os.environ
d = e["_SS_INSTALL_DIR"]

config = {
    "active_media_server": e["_SS_MEDIA_SERVER"],
    "spotify": {
        "client_id": e["_SS_SPOTIFY_ID"],
        "client_secret": e["_SS_SPOTIFY_SECRET"],
        "redirect_uri": e["_SS_SPOTIFY_REDIRECT"]
    },
    "tidal": {
        "client_id": e["_SS_TIDAL_ID"],
        "client_secret": e["_SS_TIDAL_SECRET"],
        "redirect_uri": e["_SS_TIDAL_REDIRECT"]
    },
    "plex": {
        "base_url": e["_SS_PLEX_URL"],
        "token": e["_SS_PLEX_TOKEN"],
        "auto_detect": True
    },
    "jellyfin": {
        "base_url": e["_SS_JELLYFIN_URL"],
        "api_key": e["_SS_JELLYFIN_KEY"],
        "auto_detect": True
    },
    "navidrome": {
        "base_url": e["_SS_NAVIDROME_URL"],
        "username": e["_SS_NAVIDROME_USER"],
        "password": e["_SS_NAVIDROME_PASS"],
        "auto_detect": True
    },
    "soulseek": {
        "slskd_url": f"http://localhost:{e['_SS_SLSKD_PORT']}",
        "api_key": e["_SS_SLSKD_API_KEY"],
        "download_path": f"{d}/downloads",
        "transfer_path": f"{d}/Transfer",
        "search_timeout": 60,
        "search_timeout_buffer": 15
    },
    "logging": {
        "path": f"{d}/logs/app.log",
        "level": "INFO"
    },
    "database": {
        "path": f"{d}/data/music_library.db",
        "max_workers": 5
    },
    "metadata_enhancement": {
        "enabled": True,
        "embed_album_art": True
    },
    "file_organization": {
        "enabled": True,
        "templates": {
            "album_path": "$albumartist/$albumartist - $album/$track - $title",
            "single_path": "$artist/$artist - $title/$title",
            "compilation_path": "Compilations/$album/$track - $artist - $title",
            "playlist_path": "$playlist/$artist - $title"
        }
    },
    "playlist_sync": {
        "create_backup": True
    },
    "listenbrainz": {
        "token": "LISTENBRAINZ_TOKEN"
    },
    "download_source": {
        "mode": "soulseek",
        "hybrid_primary": "soulseek",
        "youtube_min_confidence": 0.65
    },
    "acoustid": {
        "api_key": "",
        "enabled": False
    },
    "musicbrainz": {
        "embed_tags": True
    },
    "settings": {
        "audio_quality": "flac"
    },
    "import": {
        "staging_path": f"{d}/Staging"
    }
}

with open(f"{d}/config/config.json", "w") as f:
    json.dump(config, f, indent=2)
PYEOF

  chown -R "$SOULSYNC_USER:$SOULSYNC_GROUP" "$SOULSYNC_DIR"
  chmod 600 "$SOULSYNC_DIR/config/config.json"

  # Write timezone to environment
  echo "TZ=${timezone}" >>"$SOULSYNC_DIR/.env"
  chown "$SOULSYNC_USER:$SOULSYNC_GROUP" "$SOULSYNC_DIR/.env"

  msg_ok "Configured SoulSync"
}

create_services() {
  msg_info "Creating systemd services"

  # ── slskd service ──
  cat >/etc/systemd/system/slskd.service <<UNIT
[Unit]
Description=slskd - Soulseek Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SOULSYNC_USER}
Group=${SOULSYNC_GROUP}
WorkingDirectory=${SLSKD_DIR}
ExecStart=${SLSKD_DIR}/slskd --app-dir ${SLSKD_DIR}
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

  # ── SoulSync service ──
  cat >/etc/systemd/system/soulsync.service <<UNIT
[Unit]
Description=SoulSync - Music Library Manager
After=network-online.target slskd.service
Wants=network-online.target slskd.service

[Service]
Type=simple
User=${SOULSYNC_USER}
Group=${SOULSYNC_GROUP}
WorkingDirectory=${SOULSYNC_DIR}
EnvironmentFile=${SOULSYNC_DIR}/.env
Environment=PYTHONPATH=${SOULSYNC_DIR}
Environment=FLASK_APP=web_server.py
Environment=FLASK_ENV=production
Environment=DATABASE_PATH=${SOULSYNC_DIR}/data/music_library.db
Environment=SOULSYNC_CONFIG_PATH=${SOULSYNC_DIR}/config/config.json
ExecStart=${SOULSYNC_DIR}/venv/bin/python ${SOULSYNC_DIR}/web_server.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload >>"$LOGFILE" 2>&1
  msg_ok "Created systemd services"
}

start_services() {
  msg_info "Starting slskd"
  systemctl enable -q slskd >>"$LOGFILE" 2>&1
  systemctl start slskd >>"$LOGFILE" 2>&1
  msg_ok "Started slskd"

  msg_info "Starting SoulSync"
  systemctl enable -q soulsync >>"$LOGFILE" 2>&1
  systemctl start soulsync >>"$LOGFILE" 2>&1
  msg_ok "Started SoulSync"
}

setup_motd() {
  local ip
  ip=$(get_ip)

  cat >/etc/motd <<MOTD

   ____              _  ____
  / ___|  ___  _   _| |/ ___| _   _ _ __   ___
  \___ \ / _ \| | | | |\___ \| | | | '_ \ / __|
   ___) | (_) | |_| | | ___) | |_| | | | | (__
  |____/ \___/ \__,_|_||____/ \__, |_| |_|\___|
                               |___/

  SoulSync:  http://${ip}:${SOULSYNC_PORT}
  slskd:     http://${ip}:${SLSKD_PORT}

  Manage services:
    systemctl {start|stop|restart|status} soulsync
    systemctl {start|stop|restart|status} slskd

  Config:     ${SOULSYNC_DIR}/config/config.json
  slskd:      ${SLSKD_DIR}/slskd.yml
  Logs:       ${SOULSYNC_DIR}/logs/
  Downloads:  ${SOULSYNC_DIR}/downloads/
  Transfer:   ${SOULSYNC_DIR}/Transfer/

MOTD
}

cleanup_install() {
  msg_info "Cleaning up"
  apt-get -y -qq autoremove >>"$LOGFILE" 2>&1
  apt-get -y -qq autoclean >>"$LOGFILE" 2>&1
  msg_ok "Cleaned up"
}

# -- Completion Banner ---------------------------------------------------------

show_completion() {
  local ip
  ip=$(get_ip)

  echo "" >&2
  echo -e "  ${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2
  echo -e "  ${CREATING}${GN}${BOLD}SoulSync has been successfully installed!${CL}" >&2
  echo -e "  ${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${CL}" >&2
  echo "" >&2

  echo -e "  ${NETWORK}${YW}SoulSync Web UI${CL}     ${BGN}http://${ip}:${SOULSYNC_PORT}${CL}" >&2
  echo -e "  ${NETWORK}${YW}slskd Web UI${CL}        ${BGN}http://${ip}:${SLSKD_PORT}${CL}" >&2

  echo "" >&2
  echo -e "  ${GEAR}${BOLD}Paths${CL}" >&2
  echo -e "  ${TAB}${DIM}Config:${CL}      ${SOULSYNC_DIR}/config/config.json" >&2
  echo -e "  ${TAB}${DIM}slskd:${CL}       ${SLSKD_DIR}/slskd.yml" >&2
  echo -e "  ${TAB}${DIM}Downloads:${CL}   ${SOULSYNC_DIR}/downloads/" >&2
  echo -e "  ${TAB}${DIM}Transfer:${CL}    ${SOULSYNC_DIR}/Transfer/" >&2
  echo -e "  ${TAB}${DIM}Logs:${CL}        ${SOULSYNC_DIR}/logs/" >&2
  echo -e "  ${TAB}${DIM}Install log:${CL} ${LOGFILE}" >&2

  echo "" >&2
  echo -e "  ${GEAR}${BOLD}Service Management${CL}" >&2
  echo -e "  ${TAB}${DIM}systemctl {start|stop|restart|status} soulsync${CL}" >&2
  echo -e "  ${TAB}${DIM}systemctl {start|stop|restart|status} slskd${CL}" >&2

  echo "" >&2
  echo -e "  ${INFO}${YW}Next steps:${CL}" >&2
  echo -e "  ${TAB}1. Open the SoulSync web UI at ${BGN}http://${ip}:${SOULSYNC_PORT}${CL}" >&2
  echo -e "  ${TAB}2. Complete Spotify OAuth by clicking 'Connect' in Settings" >&2
  echo -e "  ${TAB}3. Start syncing your music library!" >&2
  echo "" >&2
}

# -- Main Execution ------------------------------------------------------------

main() {
  header_info
  root_check
  arch_check
  os_check
  network_check
  existing_install_check

  echo "" >&2
  echo -e "  ${CREATING}${BOLD}${GN}Starting SoulSync installation${CL}" >&2
  echo "" >&2

  update_os
  install_dependencies
  create_user_and_dirs
  install_slskd
  configure_slskd
  install_soulsync
  configure_soulsync
  create_services
  start_services
  setup_motd
  cleanup_install
  show_completion
}

main "$@"
