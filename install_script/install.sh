#!/usr/bin/env bash
#
# install.sh — install meshcore-watchlist as a systemd service.
#
# Usage (run from the project root):
#     sudo ./install_script/install.sh --port 8083 [--user USER] [--install-dir DIR]
#
# This script lives in the install_script/ subdirectory; the application
# sources (meshcore_watchlist/ package and requirements.txt) are expected
# in the parent directory.
#
# Required:
#     --port PORT        TCP port for the web UI / REST API.
#
# Optional:
#     --user USER        Run-as user (default: invoking user, or $SUDO_USER under sudo).
#     --install-dir DIR  Install location (default: /opt/meshcore-watchlist).
#

set -euo pipefail

# ---- Defaults --------------------------------------------------------------

DEFAULT_USER="${SUDO_USER:-${USER:-}}"
DEFAULT_INSTALL_DIR="/opt/meshcore-watchlist"

PORT=""
USER_NAME="$DEFAULT_USER"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"

# ---- Argument parsing ------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --user)
            USER_NAME="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '3,18p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$PORT" ]]; then
    echo "ERROR: --port is required" >&2
    sed -n '3,18p' "$0"
    exit 1
fi

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    echo "ERROR: --port must be a number between 1 and 65535" >&2
    exit 1
fi

# ---- Sanity checks ---------------------------------------------------------

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: this script must be run as root (use sudo)" >&2
    exit 1
fi

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    echo "ERROR: user '$USER_NAME' does not exist" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
TEMPLATE_FILE="$SCRIPT_DIR/meshcore-watchlist.service.template"

if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "ERROR: service template not found: $TEMPLATE_FILE" >&2
    exit 1
fi

if [[ ! -d "$PROJECT_ROOT/meshcore_watchlist" ]]; then
    echo "ERROR: package directory not found: $PROJECT_ROOT/meshcore_watchlist" >&2
    exit 1
fi

if [[ ! -f "$PROJECT_ROOT/requirements.txt" ]]; then
    echo "ERROR: requirements.txt not found: $PROJECT_ROOT/requirements.txt" >&2
    exit 1
fi

# ---- Install ---------------------------------------------------------------

echo ">>> Installing meshcore-watchlist"
echo "    Port:        $PORT"
echo "    User:        $USER_NAME"
echo "    Install dir: $INSTALL_DIR"
echo

mkdir -p "$INSTALL_DIR"
cp -r "$PROJECT_ROOT/meshcore_watchlist" "$INSTALL_DIR/"
cp "$PROJECT_ROOT/requirements.txt" "$INSTALL_DIR/"

# Optional: out-of-process helper scripts (channel_injector, …).
# Copied only when present so older trees without tools/ keep
# installing identically.
if [[ -d "$PROJECT_ROOT/tools" ]]; then
    cp -r "$PROJECT_ROOT/tools" "$INSTALL_DIR/"
fi

chown -R "$USER_NAME":"$USER_NAME" "$INSTALL_DIR"

# ---- Runtime data directory ------------------------------------------------
# The systemd unit's ReadWritePaths= requires this to exist before start,
# otherwise namespace setup fails (status=226/NAMESPACE).

USER_HOME="$( getent passwd "$USER_NAME" | cut -d: -f6 )"
if [[ -z "$USER_HOME" || ! -d "$USER_HOME" ]]; then
    echo "ERROR: could not determine home directory for user '$USER_NAME'" >&2
    exit 1
fi
DATA_DIR="$USER_HOME/.meshcore-watchlist"

echo ">>> Ensuring runtime data dir exists: $DATA_DIR"
mkdir -p "$DATA_DIR"
chown "$USER_NAME":"$USER_NAME" "$DATA_DIR"

echo ">>> Creating Python virtualenv"
sudo -u "$USER_NAME" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install \
    -r "$INSTALL_DIR/requirements.txt"

# ---- Systemd unit ----------------------------------------------------------

UNIT_PATH="/etc/systemd/system/meshcore-watchlist.service"

echo ">>> Generating systemd unit at $UNIT_PATH"
sed \
    -e "s|@USER@|$USER_NAME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@PORT@|$PORT|g" \
    "$TEMPLATE_FILE" > "$UNIT_PATH"

chmod 644 "$UNIT_PATH"

systemctl daemon-reload
# Clear any prior failed state from a previous (broken) install so the
# restart below isn't held back by an exceeded restart counter.
systemctl reset-failed meshcore-watchlist.service 2>/dev/null || true
systemctl enable meshcore-watchlist.service
systemctl restart meshcore-watchlist.service

sleep 1
systemctl --no-pager status meshcore-watchlist.service || true

echo
echo ">>> Done. UI available at:  http://localhost:$PORT"
echo ">>> View logs with:         journalctl -u meshcore-watchlist -f"
