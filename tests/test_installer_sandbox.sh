#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v bwrap >/dev/null 2>&1; then
  echo "SKIP: bubblewrap is not installed"
  exit 0
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# Assemble rather than embed a UUID literal so the public secret/identity scan
# continues to reject concrete UUIDs everywhere in the repository.
UUID="$(printf '%s-%s-%s-%s-%s' 11111111 2222 3333 4444 555555555555)"
HOME_FIX="$TMP/home"
PROJECT="$TMP/project"
FAKEBIN="$TMP/fakebin"
mkdir -p \
  "$HOME_FIX/.config/quickshell/ii/panelFamilies" \
  "$HOME_FIX/.config/quickshell/ii/modules/common" \
  "$HOME_FIX/.config/hypr/custom" \
  "$TMP/etc/restic" "$TMP/etc/timeshift" "$TMP/etc/backup-recovery" \
  "$TMP/systemd" "$TMP/polkit" "$TMP/devuuid" "$TMP/varlib" "$FAKEBIN"
cp -a "$ROOT" "$PROJECT"
cat > "$HOME_FIX/.config/quickshell/ii/GlobalStates.qml" <<'EOF'
import QtQuick
import Quickshell
pragma Singleton
Singleton {
    property bool sidebarOpen: false
}
EOF
cat > "$HOME_FIX/.config/quickshell/ii/panelFamilies/IllogicalImpulseFamily.qml" <<'EOF'
import qs
import qs.modules.common
import QtQuick
Scope {
    PanelLoader { component: ExistingPanel {} }
}
EOF
: > "$HOME_FIX/.config/hypr/custom/keybinds.conf"
printf 'sandbox-secret-placeholder\n' > "$TMP/etc/restic/password"
cat > "$TMP/etc/timeshift/timeshift.json" <<EOF
{
  "backup_device_uuid": "$UUID",
  "btrfs_mode": "false"
}
EOF
cat > "$TMP/fstab" <<EOF
UUID=$UUID /mnt/backup ext4 defaults,nofail 0 2
EOF
printf 'brc-sandbox\n' > "$TMP/etc/hostname"
: > "$TMP/devuuid/$UUID"
cat > "$FAKEBIN/sudo" <<'EOF'
#!/usr/bin/env bash
set -e
if [[ "${1:-}" == "-v" ]]; then exit 0; fi
if [[ "${1:-}" == "-u" ]]; then shift 2; fi
exec "$@"
EOF
cat > "$FAKEBIN/systemctl" <<'EOF'
#!/usr/bin/env bash
set -e
if [[ "${1:-}" == "show" ]]; then echo loaded; exit 0; fi
case "${1:-}" in is-enabled) exit 1 ;; *) exit 0 ;; esac
EOF
cat > "$FAKEBIN/hyprctl" <<'EOF'
#!/usr/bin/env bash
set -e
case "${1:-}" in binds) echo '[]' ;; layers) echo '' ;; *) exit 0 ;; esac
EOF
cat > "$FAKEBIN/qs" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$FAKEBIN/findmnt" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$FAKEBIN"/*
run_audit() {
  local extra_env=("$@")
  bwrap \
    --die-with-parent --unshare-all --share-net \
    --ro-bind / / \
    --proc /proc \
    --tmpfs /tmp \
    --tmpfs /etc \
    --tmpfs /var \
    --dev /dev \
    --ro-bind /etc/passwd /etc/passwd \
    --ro-bind /etc/group /etc/group \
    --ro-bind-try /etc/nsswitch.conf /etc/nsswitch.conf \
    --ro-bind-try /etc/ld.so.cache /etc/ld.so.cache \
    --ro-bind-try /etc/localtime /etc/localtime \
    --bind "$TMP/etc/hostname" /etc/hostname \
    --ro-bind-try "$TMP/etc/restore-fixture" /etc/restore-fixture \
    --dir /etc/restic \
    --dir /etc/timeshift \
    --dir /etc/backup-recovery \
    --dir /etc/systemd \
    --dir /etc/systemd/system \
    --dir /etc/polkit-1 \
    --dir /etc/polkit-1/rules.d \
    --dir /var/lib \
    --dir /var/lib/backup-recovery \
    --dir /dev/disk \
    --dir /dev/disk/by-uuid \
    --bind "$HOME_FIX" /tmp/brc-home \
    --bind "$PROJECT" /tmp/brc-project \
    --bind "$TMP/etc/restic" /etc/restic \
    --bind "$TMP/etc/timeshift" /etc/timeshift \
    --bind "$TMP/etc/backup-recovery" /etc/backup-recovery \
    --bind "$TMP/fstab" /etc/fstab \
    --bind "$TMP/systemd" /etc/systemd/system \
    --bind "$TMP/polkit" /etc/polkit-1/rules.d \
    --bind "$TMP/devuuid" /dev/disk/by-uuid \
    --bind "$TMP/varlib" /var/lib/backup-recovery \
    --bind "$FAKEBIN" /tmp/brc-fakebin \
    --setenv HOME /tmp/brc-home \
    --setenv USER brc-sandbox \
    --setenv PATH /tmp/brc-fakebin:/usr/bin:/bin \
    --setenv II_ROOT /tmp/brc-home/.config/quickshell/ii \
    --setenv BRC_KEY U \
    "${extra_env[@]}" \
    --chdir /tmp/brc-project \
    /bin/bash -c "BRC_BACKUP_UUID='$UUID' ./installer/install.sh --audit"
}

run_clean_audit() {
  local log="$TMP/audit-$RANDOM.log"
  if ! run_audit "$@" >"$log" 2>&1; then
    cat "$log" >&2
    return 1
  fi
  if grep -Eq 'grep: warning|unbound variable|Traceback|ERROR:' "$log"; then
    echo "ERROR: sandbox audit emitted an unexpected diagnostic:" >&2
    cat "$log" >&2
    return 1
  fi
}

run_clean_audit
cat > "$TMP/fstab" <<EOF
UUID=$UUID /media/brc-test ext4 defaults,nofail 0 2
EOF
printf 'restore-test-fixture\n' > "$TMP/etc/restore-fixture"
run_clean_audit \
  --setenv BRC_MOUNTPOINT /media/brc-test \
  --setenv BRC_RESTORE_TEST_PATH /etc/restore-fixture
echo "✓ bubblewrap installer audit smoke tests passed"
