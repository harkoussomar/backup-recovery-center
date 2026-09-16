# Backup & Recovery Center

[![Verify](https://github.com/harkoussomar/backup-recovery-center/actions/workflows/verify.yml/badge.svg)](https://github.com/harkoussomar/backup-recovery-center/actions/workflows/verify.yml)

A Quickshell control center for an Arch Linux recovery stack built around
**Restic**, **Timeshift**, **SMART**, systemd, and narrowly scoped privileged
actions.

Backup & Recovery Center turns backup and recovery state into something that is
easy to inspect, verify, and act on without hiding important uncertainty.

> **Status:** `v0.1.0-alpha.6`
>
> This is an early portability and hardening build. The source, backend safety
> tests, installer contract, and Bubblewrap sandbox audit pass, but a fresh
> clean-Arch VM installation test is still pending before the first tagged
> public prerelease.

## Overview

![Backup & Recovery Center overview](screenshots/overview.png)

The dashboard separates:

- live state from cached evidence;
- connected from mounted storage;
- backup success from actual restore verification;
- historical disk errors from current SMART risk;
- recovery readiness from recommendations that are still pending.

## Screenshots

### Backups

Encrypted Restic snapshots, backup scheduling, repository verification, and
storage state.

![Backup & Recovery Center backups](screenshots/backups.png)

### Restore points

Timeshift RSYNC rollback points and retention policy without exposing a
one-click destructive system restore.

![Backup & Recovery Center restore points](screenshots/restore.png)

### Disk health

Current SMART state, temperature, lifetime power-on hours, sector counters,
historical ATA errors, and self-test status.

![Backup & Recovery Center disk health](screenshots/disk-health.png)

### Recovery readiness

A concrete disaster-recovery checklist covering Restic, restore testing,
Timeshift, recovery documentation, system manifests, and bootable media.

![Backup & Recovery Center recovery readiness](screenshots/recovery.png)

## Validation status

| Area | Status |
|  --- | --- |
| Public-tree sanitization | ✅ Passing |
| Python compilation | ✅ Passing |
| Shell syntax | ✅ Passing |
| Backend safety regressions | ✅ Passing |
| Quickshell shell-editor integration | ✅ Passing |
| Public contract tests | ✅ Passing |
| Bubblewrap zero-network installer audit | ✅ Passing |
| GitHub Actions CI | ✅ Passing |
| Live personal deployment/backend actions | ✅ Validated separately |
| Fresh clean-Arch VM installation | ⏳ Pending |

The Bubblewrap test uses an isolated synthetic Arch/Illogical-Impulse
environment. It does not modify the host backup configuration.

## Why this project exists

Backup tools often expose raw commands but make it surprisingly difficult to
answer basic operational questions:

- Is my backup disk connected?
- Is it mounted?
- Is the filesystem actually the disk I expect?
- Is the Restic repository accessible?
- Is `0 snapshots` real, or is the repository simply offline?
- When was the repository last checked?
- Has a real file actually been restored and byte-compared?
- Are SMART errors current or only historical?
- Is it safe to unmount the disk?
- If the machine fails today, what recovery pieces are still missing?

Backup & Recovery Center turns those questions into explicit states and fixed
actions.

## Design invariants

The project deliberately follows several safety rules.

- **Unknown is not zero.**
- **Offline is not broken.**
- **Connected is not the same as mounted.**
- **A readable mount is not trusted until its filesystem UUID matches.**
- **Live state and last-known verified evidence are separate.**
- **Backup success is not restore proof.**
- **Historical SMART errors are not presented as current sector failure.**
- **The GUI never receives an arbitrary root shell.**
- **Privileged operations are fixed and allow-listed.**
- **Safe eject refuses active backup/check work and an in-progress SMART
  self-test.**
- **System restore remains guided rather than becoming a one-click destructive
  operation.**

## Current target

This alpha currently targets:

- Arch Linux
- Hyprland
- Quickshell
- end-4/dots-hyprland / Illogical Impulse shell architecture
- Restic
- Timeshift **RSYNC** mode
- smartmontools
- systemd
- Polkit

It is **not** a general Linux backup installer.

The current public alpha controls an **existing Restic + Timeshift setup** and
adds state collection, verification, fixed privileged actions, recovery
readiness, and a Quickshell interface around it.

## What it manages

Backup & Recovery Center currently understands these recovery layers:

```text
┌───────────────────────────────────────────────┐
│              Backup & Recovery                │
├───────────────────────────────────────────────┤
│ Restic        encrypted/versioned file backup │
│ Timeshift     system rollback points          │
│ SMART         physical backup-disk health     │
│ systemd       scheduled + privileged actions  │
│ Polkit        narrow privilege boundary       │
│ manifests     installed/system state evidence │
│ recovery docs disaster-recovery instructions  │
└───────────────────────────────────────────────┘

```

The GUI does not replace those tools. It coordinates and explains their state.

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
