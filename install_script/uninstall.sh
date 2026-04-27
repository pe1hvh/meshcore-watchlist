#!/usr/bin/env bash
#
# uninstall.sh — remove meshcore-watchlist systemd service and files.
#
# Usage (run from the project root):
#     sudo ./install_script/uninstall.sh [--user USER] [--install-dir DIR] [--purge] [--yes]
#
# Optional:
#     --user USER         User whose data directory is targeted
#                         (default: invoking user, or $SUDO_USER under sudo).
#                         Only used together with --purge.
#     --install-dir DIR   Install location to remove (default: /opt/meshcore-watchlist).
#     --purge             Also remove user runtime data: ~USER/.meshcore-watchlist
#                         (watchlist.json, state.json, archive/). Off by default
#                         so configuration survives a reinstall.
#     --yes, -y           Don't prompt for confirmation.
#

set -euo pipefail

# ---- Defaults --------------------------------------------------------------

DEFAULT_USER="${SUDO_USER:-${USER:-}}"
DEFAULT_INSTALL_DIR="/opt/meshcore-watchlist"

USER_NAME="$DEFAULT_USER"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
PURGE="no"
ASSUME_YES="no"

UNIT_NAME="meshcore-watchlist.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"

# ---- Argument parsing ------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            USER_NAME="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --purge)
            PURGE="yes"
            shift
            ;;
        -y|--yes)
            ASSUME_YES="yes"
            shift
            ;;
        -h|--help)
            sed -n '3,17p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ---- Sanity checks ---------------------------------------------------------

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: this script must be run as root (use sudo)" >&2
    exit 1
fi

# Refuse to wipe paths that aren't an absolute, sufficiently-specific directory.
# This protects against a typo like --install-dir /  or  --install-dir /opt.
if [[ "$INSTALL_DIR" != /* ]]; then
    echo "ERROR: --install-dir must be an absolute path, got: $INSTALL_DIR" >&2
    exit 1
fi

case "$INSTALL_DIR" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib32|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
        echo "ERROR: refusing to remove protected path: $INSTALL_DIR" >&2
        exit 1
        ;;
esac

# Resolve user home only if we may need it (--purge).
DATA_DIR=""
if [[ "$PURGE" == "yes" ]]; then
    if ! id -u "$USER_NAME" >/dev/null 2>&1; then
        echo "ERROR: user '$USER_NAME' does not exist (needed for --purge)" >&2
        exit 1
    fi
    USER_HOME="$( getent passwd "$USER_NAME" | cut -d: -f6 )"
    if [[ -z "$USER_HOME" || ! -d "$USER_HOME" ]]; then
        echo "ERROR: could not determine home directory for user '$USER_NAME'" >&2
        exit 1
    fi
    DATA_DIR="$USER_HOME/.meshcore-watchlist"
fi

# ---- Plan ------------------------------------------------------------------

echo ">>> Uninstall plan for meshcore-watchlist"
echo "    Stop + disable unit:   $UNIT_NAME"
echo "    Remove unit file:      $UNIT_PATH"
echo "    Remove install dir:    $INSTALL_DIR"
if [[ "$PURGE" == "yes" ]]; then
    echo "    Remove user data dir:  $DATA_DIR   (--purge)"
else
    echo "    Keep user data dir:    ~$USER_NAME/.meshcore-watchlist  (use --purge to also remove)"
fi
echo

if [[ "$ASSUME_YES" != "yes" ]]; then
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

# ---- Stop & disable --------------------------------------------------------
# All systemctl calls are best-effort: a partial previous install (or no
# previous install at all) shouldn't make the script fail.

if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^$UNIT_NAME"; then
    echo ">>> Stopping $UNIT_NAME"
    systemctl stop "$UNIT_NAME" || true

    echo ">>> Disabling $UNIT_NAME"
    systemctl disable "$UNIT_NAME" || true
else
    echo ">>> $UNIT_NAME not registered with systemd, skipping stop/disable"
fi

# Clear any failed state so a future install starts from a clean slate.
systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true

# ---- Remove unit file ------------------------------------------------------

if [[ -f "$UNIT_PATH" ]]; then
    echo ">>> Removing $UNIT_PATH"
    rm -f "$UNIT_PATH"
    systemctl daemon-reload
else
    echo ">>> $UNIT_PATH not present, skipping"
fi

# ---- Remove install dir ----------------------------------------------------

if [[ -d "$INSTALL_DIR" ]]; then
    echo ">>> Removing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
else
    echo ">>> $INSTALL_DIR not present, skipping"
fi

# ---- Optionally purge user data --------------------------------------------

if [[ "$PURGE" == "yes" ]]; then
    if [[ -d "$DATA_DIR" ]]; then
        echo ">>> Removing user data dir $DATA_DIR"
        rm -rf "$DATA_DIR"
    else
        echo ">>> $DATA_DIR not present, skipping"
    fi
fi

echo
echo ">>> Done. meshcore-watchlist has been removed."
if [[ "$PURGE" != "yes" ]]; then
    echo ">>> User data preserved in ~$USER_NAME/.meshcore-watchlist"
    echo ">>> (re-run with --purge to also remove it)"
fi
