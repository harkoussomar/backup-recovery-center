#!/usr/bin/env bash
set -euo pipefail

PACKAGE_VERSION="0.1.0-alpha.6"
UI_PROTOCOL="2"
SCHEMA_VERSION="2"
MODE="${1:---install}"

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "ERROR: run the installer as your desktop user, not with sudo." >&2
  echo "The installer elevates only the fixed root-side installation steps." >&2
  exit 1
fi

USER_NAME="$USER"
USER_HOME="$HOME"
USER_GROUP="$(id -gn)"
II_ROOT="${II_ROOT:-$USER_HOME/.config/quickshell/ii}"
KEY="${BRC_KEY:-U}"
KEYBINDS="${BRC_KEYBINDS:-$USER_HOME/.config/hypr/custom/keybinds.conf}"

GLOBAL="$II_ROOT/GlobalStates.qml"
FAMILY="$II_ROOT/panelFamilies/IllogicalImpulseFamily.qml"
MODULE_DST="$II_ROOT/modules/ii/backupRecovery"
HELPER_DST="$II_ROOT/scripts/backup-recovery"

CONFIG_DST="/etc/backup-recovery/config.json"
INSTALL_META="/etc/backup-recovery/install.json"
BACKEND_DST="/usr/local/lib/backup-recovery"
POLKIT_DST="/etc/polkit-1/rules.d/49-backup-recovery.rules"
STATE_DIR="/var/lib/backup-recovery"

ENV_MOUNTPOINT="${BRC_MOUNTPOINT:-}"
ENV_BACKUP_SERVICE="${BRC_BACKUP_SERVICE:-}"
ENV_BACKUP_TIMER="${BRC_BACKUP_TIMER:-}"
ENV_MAINT_SERVICE="${BRC_MAINTENANCE_SERVICE:-}"
ENV_MAINT_TIMER="${BRC_MAINTENANCE_TIMER:-}"
ENV_RESTORE_TEST_PATH="${BRC_RESTORE_TEST_PATH:-}"

ROLLBACK_ROOT="$USER_HOME/.local/state/backup-recovery-center/install-backups"
WORK_ROOT="$USER_HOME/.cache/backup-recovery-center"

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

PUBLISHED=0

ok() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*"; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  if [[ "${PUBLISHED:-0}" -eq 1 ]] && declare -F rollback >/dev/null 2>&1; then
    PUBLISHED=0
    rollback 1 || true
  fi
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<EOF
Backup & Recovery Center public installer $PACKAGE_VERSION

Usage:
  BRC_BACKUP_UUID=<uuid> ./installer/install.sh --audit
  BRC_BACKUP_UUID=<uuid> ./installer/install.sh --install

For a repeat install/upgrade, BRC_BACKUP_UUID may be omitted when an existing
/etc/backup-recovery/config.json already contains it.

Optional:
  BRC_MOUNTPOINT=/mnt/backup
  BRC_KEY=U
  BRC_KEYBINDS=~/.config/hypr/custom/keybinds.conf
  BRC_BACKUP_SERVICE=restic-system-backup.service
  BRC_BACKUP_TIMER=restic-system-backup.timer
  BRC_MAINTENANCE_SERVICE=restic-maintenance.service
  BRC_MAINTENANCE_TIMER=restic-maintenance.timer
  BRC_RESTORE_TEST_PATH=/etc/hostname
  II_ROOT=~/.config/quickshell/ii

This alpha controls an existing Restic + Timeshift setup. It never creates or
prints a Restic password, never rewrites /etc/fstab, and never formats disks.
EOF
}

case "$MODE" in
  --help|-h) usage; exit 0 ;;
  --audit|--install) ;;
  *) usage >&2; exit 2 ;;
esac

for cmd in python3 systemctl grep install cp mkdir hyprctl qs findmnt lsblk mountpoint; do
  have "$cmd" || die "required command missing: $cmd"
done

[[ -f "$GLOBAL" ]] || die "Illogical Impulse GlobalStates.qml not found: $GLOBAL"
[[ -f "$FAMILY" ]] || die "Illogical Impulse panel family not found: $FAMILY"
[[ -d "$II_ROOT/modules/common" ]] || die "Illogical Impulse common QML modules not found"
[[ -f "$ROOT_DIR/installer/shell_edit.py" ]] || die "installer helper missing"

# Resolve config values. Existing config makes repeat installation idempotent.
EXISTING_JSON="{}"
if sudo test -f "$CONFIG_DST" 2>/dev/null; then
  EXISTING_JSON="$(sudo cat "$CONFIG_DST")"
fi

mapfile -t RESOLVED < <(python3 - \
  "${BRC_BACKUP_UUID:-}" \
  "$ENV_MOUNTPOINT" \
  "$ENV_BACKUP_SERVICE" \
  "$ENV_BACKUP_TIMER" \
  "$ENV_MAINT_SERVICE" \
  "$ENV_MAINT_TIMER" \
  "$ENV_RESTORE_TEST_PATH" \
  "$EXISTING_JSON" <<'PY'
import json, sys
uuid_env, mount_env, bs, bt, ms, mt, restore, raw = sys.argv[1:]
try:
    old=json.loads(raw)
except Exception:
    old={}
def choose(env, key, default):
    return str(env or old.get(key) or default).strip()
vals=[
    str(uuid_env or old.get("backup_uuid") or "").strip(),
    choose(mount_env,"mountpoint","/mnt/backup"),
    choose(bs,"backup_service","restic-system-backup.service"),
    choose(bt,"backup_timer","restic-system-backup.timer"),
    choose(ms,"maintenance_service","restic-maintenance.service"),
    choose(mt,"maintenance_timer","restic-maintenance.timer"),
    choose(restore,"restore_test_path","/etc/hostname"),
]
for v in vals:
    print(v)
PY
)

BACKUP_UUID="${RESOLVED[0]}"
MOUNTPOINT="${RESOLVED[1]}"
BACKUP_SERVICE="${RESOLVED[2]}"
BACKUP_TIMER="${RESOLVED[3]}"
MAINT_SERVICE="${RESOLVED[4]}"
MAINT_TIMER="${RESOLVED[5]}"
RESTORE_TEST_PATH="${RESOLVED[6]}"

[[ -n "$BACKUP_UUID" ]] || die "set BRC_BACKUP_UUID for the first installation"
[[ "$BACKUP_UUID" =~ ^[A-Fa-f0-9-]{8,}$ ]] || die "backup UUID does not look valid"
[[ "$MOUNTPOINT" == /* ]] || die "BRC_MOUNTPOINT must be an absolute path"
[[ "$RESTORE_TEST_PATH" == /* && "$RESTORE_TEST_PATH" != "/" ]] \
  || die "BRC_RESTORE_TEST_PATH must be an absolute file path"
[[ -f "$RESTORE_TEST_PATH" ]] || die "restore-test source does not exist: $RESTORE_TEST_PATH"

echo
echo "Backup & Recovery Center $PACKAGE_VERSION — ${MODE#--}"
echo "============================================================"
echo "  desktop user                  $USER_NAME"
echo "  shell root                    $II_ROOT"
echo "  backup mountpoint             $MOUNTPOINT"
echo "  shortcut                      Super+$KEY"
echo "  UI protocol                   $UI_PROTOCOL"
echo "  state schema                  $SCHEMA_VERSION"

# Protected prerequisite audit. Do not print secret content.
echo "Checking protected backup configuration (sudo may prompt)..."
sudo -v
sudo test -s /etc/restic/password || die "/etc/restic/password is missing or empty"
sudo test -f /etc/timeshift/timeshift.json || die "/etc/timeshift/timeshift.json is missing"
ok "Protected Restic + Timeshift configuration exists"

# All external units referenced by the public config must exist.
for unit in "$BACKUP_SERVICE" "$BACKUP_TIMER" "$MAINT_SERVICE" "$MAINT_TIMER"; do
  load="$(systemctl show "$unit" -p LoadState --value 2>/dev/null || true)"
  [[ "$load" == "loaded" ]] || die "required existing systemd unit is not loaded: $unit"
done
ok "Configured Restic backup/maintenance units exist"

# Timeshift RSYNC target must match the same removable backup filesystem.
sudo python3 - "$BACKUP_UUID" <<'PY'
import json, sys
p="/etc/timeshift/timeshift.json"
d=json.load(open(p))
if str(d.get("btrfs_mode","false")).lower()=="true":
    raise SystemExit("ERROR: public alpha currently supports Timeshift RSYNC mode only")
actual=str(d.get("backup_device_uuid") or "").strip()
expected=sys.argv[1]
if not actual:
    raise SystemExit("ERROR: Timeshift backup_device_uuid is empty")
if actual != expected:
    raise SystemExit(f"ERROR: Timeshift target UUID {actual} does not match configured backup UUID {expected}")
PY
ok "Timeshift target identity matches configured backup UUID"

# /etc/fstab is the mount contract; the public installer deliberately does not
# edit it. Parse fields exactly instead of interpolating paths into a regex.
python3 - "$BACKUP_UUID" "$MOUNTPOINT" /etc/fstab <<'PY_FSTAB'
from pathlib import Path
import re
import sys

expected_uuid, expected_mount, fstab_path = sys.argv[1:]

def unescape_fstab(value: str) -> str:
    # fstab uses octal escapes for whitespace/backslash in fields.
    def repl(match):
        return chr(int(match.group(1), 8))
    return re.sub(r"\\([0-7]{3})", repl, value)

found = False
for raw in Path(fstab_path).read_text(errors="replace").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    fields = line.split()
    if len(fields) < 2:
        continue
    source = unescape_fstab(fields[0])
    target = unescape_fstab(fields[1])
    if source == f"UUID={expected_uuid}" and target == expected_mount:
        found = True
        break

if not found:
    raise SystemExit(
        f"ERROR: missing /etc/fstab entry for UUID={expected_uuid} at {expected_mount}"
    )
PY_FSTAB
ok "Persistent UUID mount contract exists"

# First install requires physical presence to prove the operator supplied the
# correct UUID. Repeat installs may proceed offline after that identity exists
# in our installed config.
FRESH=1
sudo test -f "$INSTALL_META" && FRESH=0 || true
if [[ "$FRESH" -eq 1 ]]; then
  [[ -e "/dev/disk/by-uuid/$BACKUP_UUID" ]] \
    || die "first install requires the configured backup filesystem to be connected"
  ok "First-install filesystem identity is physically present"
else
  if [[ -e "/dev/disk/by-uuid/$BACKUP_UUID" ]]; then
    ok "Configured backup filesystem is connected"
  else
    warn "Configured backup filesystem is offline — valid for repeat install"
  fi
fi

# If anything is mounted at the target, fail closed on empty or wrong UUID.
if findmnt -rn "$MOUNTPOINT" >/dev/null 2>&1; then
  actual_uuid="$(findmnt -rn -o UUID "$MOUNTPOINT" 2>/dev/null || true)"
  [[ -n "$actual_uuid" ]] || die "$MOUNTPOINT is mounted but its UUID cannot be verified"
  [[ "$actual_uuid" == "$BACKUP_UUID" ]] \
    || die "wrong filesystem mounted at $MOUNTPOINT: expected $BACKUP_UUID, found $actual_uuid"
  ok "Mounted filesystem identity verified"
else
  ok "Backup filesystem is currently unmounted — valid state"
fi

# Live shortcut truth is authoritative.
python3 - "$KEY" <<'PY'
import json, subprocess, sys
key=sys.argv[1].upper()
items=json.loads(subprocess.check_output(["hyprctl","binds","-j"], text=True))
conflicts=[
    x for x in items
    if str(x.get("key","")).upper()==key
    and int(x.get("modmask",0) or 0)==64
    and "backupRecovery" not in str(x.get("arg",""))
]
if conflicts:
    raise SystemExit(f"ERROR: Super+{key} is already owned by another live binding")
PY
ok "Live Super+$KEY ownership is available"

# Source/static validation before publishing.
python3 -m py_compile \
  "$ROOT_DIR/src/backend/root_actions.py" \
  "$ROOT_DIR/src/backend/state_collector.py" \
  "$ROOT_DIR/src/quickshell/scripts/backup-recovery/control_center.py" \
  "$ROOT_DIR/installer/shell_edit.py"
bash -n "$ROOT_DIR/installer/install.sh" "$ROOT_DIR/installer/uninstall.sh"
python3 "$ROOT_DIR/tests/test_backend_safety.py"
python3 "$ROOT_DIR/tests/test_shell_editor.py"
python3 "$ROOT_DIR/tests/test_public_contract.py"
ok "Static and regression checks passed"

if [[ "$MODE" == "--audit" ]]; then
  echo
  ok "Audit passed"
  echo "Nothing was changed."
  exit 0
fi

WORK="$WORK_ROOT/$(date +%Y%m%d-%H%M%S)-$$"
STAGE="$WORK/stage"
ROLLBACK="$ROLLBACK_ROOT/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$STAGE" "$ROLLBACK/user" "$ROLLBACK/root"

# Stage source and config entirely before touching live files.
cp -a "$ROOT_DIR/src/quickshell/modules/ii/backupRecovery" "$STAGE/module"
cp -a "$ROOT_DIR/src/quickshell/scripts/backup-recovery" "$STAGE/helper"
cp -a "$ROOT_DIR/src/backend" "$STAGE/backend"
cp -a "$ROOT_DIR/systemd" "$STAGE/systemd"
cp -a "$GLOBAL" "$STAGE/GlobalStates.qml"
cp -a "$FAMILY" "$STAGE/IllogicalImpulseFamily.qml"
if [[ -f "$KEYBINDS" ]]; then
  cp -a "$KEYBINDS" "$STAGE/keybinds.conf"
else
  : > "$STAGE/keybinds.conf"
fi

python3 "$ROOT_DIR/installer/shell_edit.py" add \
  --global-states "$STAGE/GlobalStates.qml" \
  --family "$STAGE/IllogicalImpulseFamily.qml"

# Add keybind exactly once.
if ! grep -Fq 'ipc call backupRecovery toggle' "$STAGE/keybinds.conf"; then
  cat >> "$STAGE/keybinds.conf" <<EOF

# >>> backup-recovery-center >>>
bind = SUPER, $KEY, exec, qs -c ii ipc call backupRecovery toggle
# <<< backup-recovery-center <<<
EOF
fi

grep -Fq 'property bool backupRecoveryOpen:' "$STAGE/GlobalStates.qml" \
  || die "staged GlobalStates postcondition failed"
grep -Fq 'import qs.modules.ii.backupRecovery' "$STAGE/IllogicalImpulseFamily.qml" \
  || die "staged family import postcondition failed"
grep -Fq 'PanelLoader { component: BackupRecovery {} }' "$STAGE/IllogicalImpulseFamily.qml" \
  || die "staged PanelLoader postcondition failed"

# Generate config from sanitized template.
python3 - "$ROOT_DIR/config/config.example.json" "$STAGE/config.json" \
  "$USER_NAME" "$BACKUP_UUID" "$MOUNTPOINT" "$BACKUP_SERVICE" "$BACKUP_TIMER" \
  "$MAINT_SERVICE" "$MAINT_TIMER" "$RESTORE_TEST_PATH" <<'PY'
import json, sys
(src,dst,user,uuid,mountpoint,bs,bt,ms,mt,restore)=sys.argv[1:]
d=json.load(open(src))
d.update({
  "user": user,
  "backup_uuid": uuid,
  "mountpoint": mountpoint,
  "restic_repo": mountpoint.rstrip("/") + "/restic",
  "backup_service": bs,
  "backup_timer": bt,
  "maintenance_service": ms,
  "maintenance_timer": mt,
  "restore_test_path": restore,
})
json.dump(d, open(dst,"w"), indent=2)
open(dst,"a").write("\n")
PY

sed "s/__BRC_USER__/${USER_NAME//\//\\/}/g" \
  "$ROOT_DIR/polkit/49-backup-recovery.rules.in" > "$STAGE/49-backup-recovery.rules"

python3 - "$STAGE/config.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
assert d["user"]
assert d["backup_uuid"] and d["backup_uuid"] != "CHANGE_ME"
assert d["mountpoint"].startswith("/")
assert d["restore_test_path"].startswith("/")
PY
ok "Staging validation passed"

# Capture pre-install user state.
cp -a "$GLOBAL" "$ROLLBACK/user/GlobalStates.qml"
cp -a "$FAMILY" "$ROLLBACK/user/IllogicalImpulseFamily.qml"
if [[ -f "$KEYBINDS" ]]; then
  cp -a "$KEYBINDS" "$ROLLBACK/user/keybinds.conf"
  : > "$ROLLBACK/user/keybinds.was-present"
else
  : > "$ROLLBACK/user/keybinds.was-absent"
fi
if [[ -d "$MODULE_DST" ]]; then
  cp -a "$MODULE_DST" "$ROLLBACK/user/module"
  : > "$ROLLBACK/user/module.was-present"
else
  : > "$ROLLBACK/user/module.was-absent"
fi
if [[ -d "$HELPER_DST" ]]; then
  cp -a "$HELPER_DST" "$ROLLBACK/user/helper"
  : > "$ROLLBACK/user/helper.was-present"
else
  : > "$ROLLBACK/user/helper.was-absent"
fi

# Capture root-side pre-install state as a metadata-preserving tar archive.
ROOT_PATHS=()
for path in \
  usr/local/lib/backup-recovery \
  etc/backup-recovery \
  etc/polkit-1/rules.d/49-backup-recovery.rules \
  var/lib/backup-recovery
do
  sudo test -e "/$path" && ROOT_PATHS+=("$path") || true
done
for unit in "${SYSTEM_UNITS[@]}"; do
  path="etc/systemd/system/$unit"
  sudo test -e "/$path" && ROOT_PATHS+=("$path") || true
done

if systemctl is-enabled backup-recovery-state.timer >/dev/null 2>&1; then
  echo enabled > "$ROLLBACK/root/state-timer-enabled"
else
  echo disabled > "$ROLLBACK/root/state-timer-enabled"
fi

if ((${#ROOT_PATHS[@]})); then
  sudo tar -C / -cpf "$ROLLBACK/root/root-snapshot.tar" "${ROOT_PATHS[@]}"
else
  tar -C / -cpf "$ROLLBACK/root/root-snapshot.tar" --files-from /dev/null
fi
sudo chown "$USER_NAME:$USER_GROUP" "$ROLLBACK/root/root-snapshot.tar"
ok "Transactional rollback snapshot created"
echo "  $ROLLBACK"

rollback() {
  local status="${1:-1}"
  set +e
  echo
  echo "Rolling back Backup & Recovery Center installation..."

  # Stop our units before replacing files.
  sudo systemctl disable --now backup-recovery-state.timer >/dev/null 2>&1 || true
  for unit in "${SYSTEM_UNITS[@]}"; do
    sudo systemctl stop "$unit" >/dev/null 2>&1 || true
  done

  # Remove published public files.
  sudo rm -rf "$BACKEND_DST"
  sudo rm -f "$POLKIT_DST"
  for unit in "${SYSTEM_UNITS[@]}"; do sudo rm -f "/etc/systemd/system/$unit"; done

  # Remove every root-side target published by this project, then restore the
  # exact pre-install snapshot (including ownership/modes) if one existed.
  sudo rm -rf \
    /usr/local/lib/backup-recovery \
    /etc/backup-recovery \
    /var/lib/backup-recovery
  sudo rm -f /etc/polkit-1/rules.d/49-backup-recovery.rules
  for unit in "${SYSTEM_UNITS[@]}"; do
    sudo rm -f "/etc/systemd/system/$unit"
  done

  if [[ -s "$ROLLBACK/root/root-snapshot.tar" ]]; then
    sudo tar -C / -xpf "$ROLLBACK/root/root-snapshot.tar"
  fi


  # Restore user files exactly.
  cp -a "$ROLLBACK/user/GlobalStates.qml" "$GLOBAL"
  cp -a "$ROLLBACK/user/IllogicalImpulseFamily.qml" "$FAMILY"
  if [[ -f "$ROLLBACK/user/keybinds.was-present" ]]; then
    mkdir -p "$(dirname "$KEYBINDS")"
    cp -a "$ROLLBACK/user/keybinds.conf" "$KEYBINDS"
  else
    rm -f "$KEYBINDS"
  fi
  rm -rf "$MODULE_DST" "$HELPER_DST"
  [[ -f "$ROLLBACK/user/module.was-present" ]] && cp -a "$ROLLBACK/user/module" "$MODULE_DST"
  [[ -f "$ROLLBACK/user/helper.was-present" ]] && cp -a "$ROLLBACK/user/helper" "$HELPER_DST"

  sudo systemctl daemon-reload
  if [[ "$(cat "$ROLLBACK/root/state-timer-enabled" 2>/dev/null || echo disabled)" == "enabled" ]]; then
    sudo systemctl enable --now backup-recovery-state.timer >/dev/null 2>&1 || true
  fi
  hyprctl reload >/dev/null 2>&1 || true
  echo "✓ Rollback completed"
  echo "  $ROLLBACK"
  return "$status"
}

on_error() {
  local status=$?
  if [[ "$PUBLISHED" -eq 1 ]]; then
    PUBLISHED=0
    echo "ERROR: post-publish verification failed (status $status)." >&2
    rollback "$status" || true
  fi
  exit "$status"
}
trap on_error ERR INT TERM

# Capture log baseline before live changes.
mkdir -p "$WORK"
qs log -c ii --tail 700 --no-color > "$WORK/log-before.txt" 2>&1 || true

# Publish leaf/backend first, shell integration last.
PUBLISHED=1
mkdir -p "$(dirname "$MODULE_DST")" "$(dirname "$HELPER_DST")" "$(dirname "$KEYBINDS")"
rm -rf "$MODULE_DST" "$HELPER_DST"
cp -a "$STAGE/module" "$MODULE_DST"
cp -a "$STAGE/helper" "$HELPER_DST"
chmod +x "$HELPER_DST/control_center.py"

sudo install -d -m 0755 "$BACKEND_DST" /etc/backup-recovery
sudo install -d -m 0750 -o root -g "$USER_GROUP" "$STATE_DIR"
sudo chown "root:$USER_GROUP" "$STATE_DIR"
sudo chmod 0750 "$STATE_DIR"
sudo install -m 0755 "$STAGE/backend/root_actions.py" "$BACKEND_DST/root_actions.py"
sudo install -m 0755 "$STAGE/backend/state_collector.py" "$BACKEND_DST/state_collector.py"
sudo install -m 0640 "$STAGE/config.json" "$CONFIG_DST"

for f in "$STAGE/systemd"/*; do
  sudo install -m 0644 "$f" "/etc/systemd/system/$(basename "$f")"
done
sudo install -m 0644 "$STAGE/49-backup-recovery.rules" "$POLKIT_DST"

tmpmeta="$(mktemp)"
python3 - "$tmpmeta" "$PACKAGE_VERSION" "$UI_PROTOCOL" "$SCHEMA_VERSION" "$USER_NAME" <<'PY'
import json, sys, time
p,version,protocol,schema,user=sys.argv[1:]
json.dump({
  "package_version":version,
  "ui_protocol":protocol,
  "state_schema":int(schema),
  "user":user,
  "installed_at":int(time.time()),
},open(p,"w"),indent=2)
open(p,"a").write("\n")
PY
sudo install -m 0644 "$tmpmeta" "$INSTALL_META"
rm -f "$tmpmeta"

cp -a "$STAGE/GlobalStates.qml" "$GLOBAL"
cp -a "$STAGE/IllogicalImpulseFamily.qml" "$FAMILY"
cp -a "$STAGE/keybinds.conf" "$KEYBINDS"

sudo systemctl daemon-reload
sudo systemctl enable --now backup-recovery-state.timer
sudo systemctl start backup-recovery-state.service
hyprctl reload >/dev/null

# State contract proof.
sudo python3 - "$STATE_DIR/state.json" "$USER_NAME" "$UI_PROTOCOL" "$SCHEMA_VERSION" <<'PY'
import json, os, pwd, stat, sys
path,user,protocol,schema=sys.argv[1:]
d=json.load(open(path))
assert str(d.get("ui_contract")) == protocol
assert int(d.get("schema_version")) == int(schema)
st=os.stat(path)
pw=pwd.getpwnam(user)
assert stat.S_IMODE(st.st_mode)==0o640
assert st.st_uid==0
assert st.st_gid==pw.pw_gid
print("✓ state protocol/schema and permissions verified")
PY
sudo -u "$USER_NAME" test -r "$STATE_DIR/state.json"

# Wrapper runtime proof: stdout return values are intentionally not used.
WRAPPER_OK=0
for _ in {1..30}; do
  if qs -c ii ipc call backupRecoveryProtocol2 ping >/dev/null 2>&1; then
    WRAPPER_OK=1
    break
  fi
  sleep 0.25
done
[[ "$WRAPPER_OK" -eq 1 ]] || die "UI protocol runtime target did not become live"
qs -c ii ipc call backupRecovery ping >/dev/null 2>&1 \
  || die "stable Backup Recovery IPC target is unavailable"
ok "Quickshell protocol runtime proof verified"

# Instantiate the actual visual surface to catch runtime-only QML failures.
qs -c ii ipc call backupRecovery close >/dev/null 2>&1 || true
sleep 0.2
qs -c ii ipc call backupRecovery open >/dev/null 2>&1
sleep 1
hyprctl layers 2>/dev/null | grep -Fq 'namespace: quickshell:backupRecovery' \
  || die "Backup Recovery visual layer did not map"

qs log -c ii --tail 700 --no-color > "$WORK/log-after.txt" 2>&1 || true
python3 - "$WORK/log-before.txt" "$WORK/log-after.txt" <<'PY'
from collections import Counter
from pathlib import Path
import re, sys
before=Path(sys.argv[1]).read_text(errors="replace").splitlines()
after=Path(sys.argv[2]).read_text(errors="replace").splitlines()
counts=Counter(before); delta=[]
for line in after:
    if counts[line]: counts[line]-=1
    else: delta.append(line)
scope=re.compile(r"(?i)(backupRecovery|BackupRecovery|backup-recovery)")
badword=re.compile(r"(?i)(error|failed|not a type|referenceerror|typeerror|syntaxerror|unable to assign)")
bad=[line for line in delta if scope.search(line) and badword.search(line)]
if bad:
    print("ERROR: new Backup Recovery runtime diagnostics detected:", file=sys.stderr)
    print("\n".join(bad[-80:]), file=sys.stderr)
    raise SystemExit(1)
print("✓ no new Backup Recovery runtime errors detected")
PY
qs -c ii ipc call backupRecovery close >/dev/null 2>&1 || true
ok "Visual surface mapped and runtime diagnostics passed"

# Live shortcut must still belong to us after reload.
python3 - "$KEY" <<'PY'
import json, subprocess, sys
key=sys.argv[1].upper()
items=json.loads(subprocess.check_output(["hyprctl","binds","-j"],text=True))
assert any(
    str(x.get("key","")).upper()==key
    and int(x.get("modmask",0) or 0)==64
    and "backupRecovery" in str(x.get("arg",""))
    for x in items
)
print(f"✓ live Super+{key} binding verified")
PY

PUBLISHED=0
trap - ERR INT TERM

echo
ok "Backup & Recovery Center $PACKAGE_VERSION installed successfully"
echo "Rollback snapshot:"
echo "  $ROLLBACK"
echo
echo "No backup, restore point, SMART test, mount, unmount, or repository mutation"
echo "was started by the installer."
