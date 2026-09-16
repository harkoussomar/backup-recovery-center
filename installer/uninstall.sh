#!/usr/bin/env bash
set -euo pipefail

PACKAGE_VERSION="0.1.0-alpha.6"
PURGE=0
case "${1:-}" in
  "") ;;
  --purge) PURGE=1 ;;
  --help|-h)
    cat <<'EOF'
Usage:
  ./installer/uninstall.sh
  ./installer/uninstall.sh --purge

Default uninstall removes the UI/backend/systemd/Polkit integration but keeps:
  /etc/backup-recovery/config.json
  /var/lib/backup-recovery/

--purge also removes those Backup & Recovery Center config/state files.

Restic repositories, Timeshift snapshots, /etc/restic, /etc/timeshift and
backup/recovery data on removable storage are NEVER deleted by this script.
EOF
    exit 0
    ;;
  *) echo "Usage: $0 [--purge]" >&2; exit 2 ;;
esac

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "ERROR: run as the desktop user, not with sudo" >&2
  exit 1
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
II_ROOT="${II_ROOT:-$HOME/.config/quickshell/ii}"
GLOBAL="$II_ROOT/GlobalStates.qml"
FAMILY="$II_ROOT/panelFamilies/IllogicalImpulseFamily.qml"
MODULE="$II_ROOT/modules/ii/backupRecovery"
HELPER="$II_ROOT/scripts/backup-recovery"
KEYBINDS="${BRC_KEYBINDS:-$HOME/.config/hypr/custom/keybinds.conf}"

SYSTEM_UNITS=(
  backup-recovery-state.service
  backup-recovery-state.timer
  backup-recovery-backup.service
  backup-recovery-timeshift.service
  backup-recovery-restic-check.service
  backup-recovery-restore-test.service
  backup-recovery-smart-short.service
  backup-recovery-smart-long.service
  backup-recovery-mount.service
  backup-recovery-eject.service
  backup-recovery-refresh-manifests.service
)

sudo -v
sudo systemctl disable --now backup-recovery-state.timer >/dev/null 2>&1 || true
for unit in "${SYSTEM_UNITS[@]}"; do
  sudo systemctl stop "$unit" >/dev/null 2>&1 || true
  sudo rm -f "/etc/systemd/system/$unit"
done
sudo rm -rf /usr/local/lib/backup-recovery
sudo rm -f /etc/polkit-1/rules.d/49-backup-recovery.rules
sudo rm -f /etc/backup-recovery/install.json

if [[ "$PURGE" -eq 1 ]]; then
  sudo rm -rf /etc/backup-recovery /var/lib/backup-recovery
else
  echo "Preserving /etc/backup-recovery/config.json and /var/lib/backup-recovery/"
fi

sudo systemctl daemon-reload

rm -rf "$MODULE" "$HELPER"
python3 "$ROOT_DIR/installer/shell_edit.py" remove \
  --global-states "$GLOBAL" \
  --family "$FAMILY"

if [[ -f "$KEYBINDS" ]]; then
  sed -i '/# >>> backup-recovery-center >>>/,/# <<< backup-recovery-center <<</d' "$KEYBINDS"
  # Compatibility cleanup for early unmarked public/private prototypes.
  sed -i '/^[[:space:]]*bind[[:space:]]*=.*ipc call backupRecovery toggle[[:space:]]*$/d' "$KEYBINDS"
fi

hyprctl reload >/dev/null 2>&1 || true
echo "✓ Backup & Recovery Center $PACKAGE_VERSION removed."
if [[ "$PURGE" -eq 1 ]]; then
  echo "✓ Backup & Recovery Center config/state purged."
fi
echo "Restic/Timeshift repositories and recovery data were not touched."
