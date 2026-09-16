# Backup & Recovery Center

A Quickshell control center for an Arch Linux recovery stack built around
**Restic**, **Timeshift**, **SMART**, systemd, and narrowly scoped privileged
actions.

> Public status: **v0.1.0-alpha.6**. This is an early portability/hardening
> build. Test it in a disposable Arch/Hyprland environment before relying on it
> for disaster recovery.

## Validation status

- Source sanitization and regression suite: **passing**.
- Bubblewrap zero-network installer audit: **passing**.
- Live personal deployment/backend actions: **validated separately**.
- Fresh clean-VM installation test: **pending** before the first public release.

## Why this project exists

Backup tools often expose raw commands but make it hard to answer basic
questions:

- Is my backup disk connected, mounted, and actually the expected filesystem?
- Is the repository accessible, or am I only seeing cached evidence?
- Is `0 snapshots` real, or is the repository simply unavailable?
- Has a file actually been restored and byte-compared?
- Is it safe to unmount the backup disk?

Backup & Recovery Center turns those into explicit states and fixed actions.

## Design invariants

- **Unknown is not zero.**
- **Offline is not broken.** A removable backup HDD may be disconnected by design.
- **A readable mount is not trusted until its filesystem UUID matches.**
- **Live state and last-known verified evidence are separate.**
- **Backup success is not restore proof.**
- **The GUI never receives an arbitrary root shell.**
- **Safe eject refuses active backup/check work and an in-progress SMART self-test.**

## Current target

This alpha currently targets:

- Arch Linux
- Hyprland
- Quickshell
- end-4/dots-hyprland / Illogical Impulse shell architecture
- Restic
- Timeshift **RSYNC** mode
- smartmontools
- systemd + Polkit

It is **not** a general Linux backup installer yet. It controls an existing
Restic + Timeshift setup.

## Repository layout

```text
src/quickshell/   QML UI and unprivileged controller
src/backend/      root state collector and fixed root actions
systemd/          fixed privileged action/state units
polkit/           install-time per-user allow-list template
config/           sanitized configuration example
installer/        transactional installer + safe uninstaller
tests/            backend, shell-integration and release-contract tests
docs/             architecture, state model, compatibility and operations
scripts/          public-tree sanitization/regression audit
```

## Prerequisites

Before installation you should already have:

1. A Restic repository on a removable filesystem.
2. `/etc/restic/password` readable only by root.
3. A working Restic backup service/timer and maintenance service/timer.
4. Timeshift configured in RSYNC mode to the **same filesystem UUID**.
5. A stable `/etc/fstab` entry such as:

```text
UUID=<your-uuid> /mnt/backup ext4 defaults,noatime,nofail,x-systemd.device-timeout=10s 0 2
```

The installer deliberately does **not** create passwords, format disks, or
rewrite `/etc/fstab`.

## Audit before install

```bash
./scripts/verify-public-tree.sh
BRC_BACKUP_UUID='<your-filesystem-uuid>' ./installer/install.sh --audit
```

When Bubblewrap is installed, `verify-public-tree.sh` also runs the installer
audit inside an isolated fake Arch/Illogical-Impulse filesystem. This catches
early installer/runtime-resolution failures without touching the live system or
using network data.

First installation requires that filesystem to be physically connected so the
operator-supplied UUID can be proven. Repeat installation may run while the disk
is offline because the installed config already carries the identity.

## Install

```bash
BRC_BACKUP_UUID='<your-filesystem-uuid>' ./installer/install.sh --install
```

Optional environment overrides:

```text
BRC_MOUNTPOINT
BRC_KEY
BRC_KEYBINDS
BRC_BACKUP_SERVICE
BRC_BACKUP_TIMER
BRC_MAINTENANCE_SERVICE
BRC_MAINTENANCE_TIMER
BRC_RESTORE_TEST_PATH
II_ROOT
```

Run the installer as the desktop user, **not with `sudo`**. It elevates only
the fixed root-side installation steps.

The installer is transactional: it snapshots user/root integration, stages and
validates changes, publishes them, verifies the state schema/protocol, proves
the IPC target, opens the real QML layer, checks new runtime diagnostics, and
automatically rolls back if post-publish verification fails.

## Uninstall

Preserve Backup & Recovery Center config/state:

```bash
./installer/uninstall.sh
```

Also remove its config/state:

```bash
./installer/uninstall.sh --purge
```

Neither mode deletes the Restic repository, Timeshift snapshots, `/etc/restic`,
`/etc/timeshift`, or removable-disk recovery data.

## Public package vs runtime protocol

These are intentionally separate:

```text
public package version: 0.1.0-alpha.6
UI/state protocol:      2
state schema:           2
```

A package release may change without forcing a state-protocol change.

## Safety and secrets

Never commit:

- `/etc/restic/password`
- private keys, API tokens, or `.env` files
- Restic repository contents
- Timeshift snapshots
- machine recovery archives
- real filesystem UUIDs
- personal hostnames/home paths
- private logs/evidence

Read [SECURITY.md](SECURITY.md) before publishing a fork.

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
