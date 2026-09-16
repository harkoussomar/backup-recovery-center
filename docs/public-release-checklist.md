# Public Release Checklist

## Source safety

- [ ] `./scripts/verify-public-tree.sh` passes.
- [ ] No personal username/home path.
- [ ] No concrete filesystem UUID.
- [ ] No password/private key/token.
- [ ] No Restic repository, Timeshift snapshot or recovery archive.
- [ ] Package version and runtime protocol are documented separately.

## Backend safety

- [ ] Empty mounted UUID is rejected.
- [ ] Wrong mounted UUID is rejected.
- [ ] Wrong/unverified mount cannot be read as Restic/Timeshift/recovery storage.
- [ ] Connected-unmounted is valid but filesystem-inaccessible.
- [ ] Offline is neutral/unverified, not zero.
- [ ] Safe eject refuses active backup/check work.
- [ ] Safe eject refuses drive-level SMART self-test in progress.
- [ ] Real restore test succeeds for the configured test path.

## Installer

- [ ] Installer refuses root invocation.
- [ ] No Arch Remote dependency.
- [ ] First install verifies physical UUID presence.
- [ ] Repeat install is idempotent.
- [ ] Existing custom config is preserved/reused when overrides are omitted.
- [ ] Shell edits are postcondition-checked.
- [ ] Failure after first live mutation rolls back automatically.
- [ ] Rollback restores ownership/modes and prior timer enablement.
- [ ] Actual QML layer maps.
- [ ] New Quickshell runtime errors fail installation.
- [ ] Live shortcut belongs to Backup & Recovery Center after reload.

## Uninstall

- [ ] Default uninstall preserves config/state.
- [ ] `--purge` removes only Backup & Recovery Center config/state.
- [ ] Neither mode touches Restic/Timeshift/recovery data.

## Clean-environment test

- [ ] Pin exact upstream Illogical Impulse commit.
- [ ] Fresh Arch/Hyprland install.
- [ ] Install.
- [ ] Re-run install.
- [ ] Open GUI.
- [ ] Connected-unmounted state.
- [ ] Correct mounted state.
- [ ] Backup.
- [ ] Repository check.
- [ ] Restore test.
- [ ] Safe eject.
- [ ] Deliberately broken QML → automatic rollback.
- [ ] Wrong filesystem fixture → blocked.
- [ ] Uninstall.
- [ ] Reinstall.
- [ ] `--purge` test with disposable state.
