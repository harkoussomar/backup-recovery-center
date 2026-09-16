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

<table>
  <tr>
    <td width="50%">
      <strong>Backups</strong><br>
      <sub>Restic snapshots, scheduling, repository verification, and storage state.</sub><br><br>
      <img src="screenshots/backups.png" alt="Backup & Recovery Center backups" width="100%">
    </td>
    <td width="50%">
      <strong>Restore points</strong><br>
      <sub>Timeshift RSYNC rollback points and retention policy.</sub><br><br>
      <img src="screenshots/restore.png" alt="Backup & Recovery Center restore points" width="100%">
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>Disk health</strong><br>
      <sub>SMART condition, sector counters, history, and self-test state.</sub><br><br>
      <img src="screenshots/disk-health.png" alt="Backup & Recovery Center disk health" width="100%">
    </td>
    <td width="50%">
      <strong>Recovery readiness</strong><br>
      <sub>Disaster-recovery readiness, manifests, documentation, and resilience.</sub><br><br>
      <img src="screenshots/recovery.png" alt="Backup & Recovery Center recovery readiness" width="100%">
    </td>
  </tr>
</table>

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

## Security model

The desktop UI remains unprivileged. Root-required operations are exposed only
through fixed systemd units and a narrow Polkit allow-list; the GUI cannot
construct arbitrary privileged commands.

Storage-sensitive operations verify the configured filesystem identity before
reading or mutating backup state.

## State model

The UI deliberately distinguishes states that are often incorrectly collapsed
together:

```text
disk disconnected
connected but unmounted
correct filesystem mounted
wrong filesystem mounted
filesystem identity unknown
repository accessible / unavailable
live evidence / cached evidence
verified success / unknown result / actual failure
```

A disconnected removable backup disk is therefore a valid state. Cached
snapshot information may remain visible as **last-known evidence**, but it is
not presented as live repository state.

See [docs/state-model.md](docs/state-model.md).

## Restore verification

A successful backup is not enough evidence that recovery works.

Backup & Recovery Center restores a configured source file from Restic into
temporary storage and compares it byte-for-byte with the source before recording
verified evidence.

The default source is `/etc/hostname` and can be overridden with
`BRC_RESTORE_TEST_PATH`.

## SMART and disk health

Disk health separates current condition from historical evidence.

The UI tracks current SMART status, temperature, power-on hours,
reallocated/pending/uncorrectable sectors, CRC errors, historical ATA errors,
and the latest SMART self-test state.

Historical errors remain visible without automatically being treated as current
media failure.

## Recovery readiness

Core recovery readiness currently tracks:

- encrypted Restic repository;
- actual restore verification;
- Timeshift restore points;
- recovery documentation;
- Arch system manifests;
- bootable recovery media.

Recommended resilience improvements, such as a second independent backup copy,
are shown separately from the core readiness score.

## Testing

Run the full safe verification suite with:

```bash
./scripts/verify-public-tree.sh
```

The suite covers source sanitization, Python compilation, shell syntax,
filesystem-identity regressions, offline/connected/unmounted states, SMART
self-test detection, shell integration, public package contracts, and the
Bubblewrap installer-audit smoke test when Bubblewrap is available.

GitHub Actions runs the portable portion of the same verification suite on every
push and pull request.

## Documentation

- [Architecture](docs/architecture.md)
- [State model](docs/state-model.md)
- [Operations](docs/operations.md)
- [Compatibility](docs/compatibility.md)
- [Public release checklist](docs/public-release-checklist.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

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
