# Changelog

## 0.1.0-alpha.6

Clean persistent-mount validation.

- Replaced regex interpolation of the configured mountpoint in the `/etc/fstab`
  check with exact field parsing.
- Supports standard fstab octal escapes while avoiding `grep` regex escaping
  warnings.
- Bubblewrap audits now capture both stdout and stderr and fail on unexpected
  `grep` warnings, tracebacks, unbound variables, or installer `ERROR:` output.
- Added a static regression preventing the old regex-based fstab check from
  returning.
- No live Restic, Timeshift, backup, restore, or storage behavior changed.

## 0.1.0-alpha.5


Sandbox prerequisite completeness fix.

- Added a private `/etc/hostname` fixture so the installer's default
  restore-test source exists inside the isolated Bubblewrap environment.
- Added a second private restore fixture to exercise
  `BRC_RESTORE_TEST_PATH`.
- The alpha.4 error was the sandbox correctly missing a prerequisite, not a
  defect in the live Backup & Recovery system.
- No backup, Timeshift, Restic, mount, or recovery behavior changed.

## 0.1.0-alpha.4


Zero-network sandbox harness fix.

- Fixed the Bubblewrap smoke test so writable private `/tmp`, `/etc`, `/var`,
  and `/dev` trees are created before fixture bind mounts.
- The alpha.3 failure `Can't create file /tmp/brc-home: Read-only file system`
  was a test-harness mount-layout bug, not a Backup & Recovery installer or
  live backup-system failure.
- Added `/proc` and the minimal passwd/group/runtime linker fixtures needed by
  the isolated audit.
- Public verification now redirects Python bytecode caches outside the source
  tree, so running the test suite no longer leaves `__pycache__` directories.

## 0.1.0-alpha.3


Installer-resolution and zero-network test hardening.

### Installer

- Fixed an `alpha.2` blocker where the config resolver referenced undefined
  `DEFAULT_*` shell variables under `set -u`.
- Environment overrides now correctly pass through the declared `ENV_*`
  variables into the resolver.
- No storage, backup, or recovery data model changes from `alpha.2`.

### Tests

- Added a static regression that rejects undefined `DEFAULT_*` resolver tokens.
- Added a Bubblewrap installer-audit smoke test using a fake home, fake
  Illogical Impulse shell, fake systemd/Hyprland/Quickshell commands, fake
  `/etc/fstab`, and a fake backup UUID.
- The Bubblewrap smoke test also verifies a mountpoint environment override.
- `verify-public-tree.sh` runs the sandbox test automatically when `bwrap`
  exists.

## 0.1.0-alpha.2


Safety and portability hardening after review of `alpha.1` and validation on
the original live deployment.

### Storage identity

- Empty mounted UUID is now **unverified**, never accepted as a match.
- Wrong or unverifiable filesystems at the backup mountpoint are quarantined.
- Restic, Timeshift, and recovery-material reads require verified mount identity.
- Installer verifies Timeshift RSYNC targets the same configured filesystem UUID.

### Safe operations

- Safe eject checks active backup/check units.
- Safe eject also detects a drive-level SMART self-test that outlives the
  starter systemd unit.
- Restore-test source path is configurable.

### Installer

- No dependency on Arch Remote integration.
- Refuses whole-installer root execution.
- Current desktop user/home are the single installation target.
- Repeat installation is idempotent and may reuse installed configuration.
- Shell edits are deterministic and postcondition-checked.
- Root/user files are snapshotted before live mutation.
- Runtime failure triggers automatic rollback.
- Runtime proof opens the real QML surface and checks new Quickshell errors.
- Required external Restic/maintenance units are audited before publication.
- Public package version is separated from UI/state protocol.

### Uninstall

- Default uninstall preserves `/etc/backup-recovery/config.json` and
  `/var/lib/backup-recovery/`.
- `--purge` explicitly removes Backup & Recovery Center config/state.
- Backup repositories, Timeshift snapshots and removable recovery data are
  never deleted.

### Tests

Added regression coverage for empty/wrong UUID, connected-unmounted state,
offline state, SMART self-test detection, shell installation without Arch
Remote, repeat shell integration and safe shell removal.

## 0.1.0-alpha.1

Initial sanitized public-source prototype. Superseded by `alpha.2`; do not use
`alpha.1` as a publication/install candidate.
