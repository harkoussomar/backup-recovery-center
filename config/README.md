# Configuration

The installed runtime configuration lives at:

```text
/etc/backup-recovery/config.json
```

The public alpha controls an **existing** Restic + Timeshift setup. It does not
create passwords, format storage, or rewrite `/etc/fstab`.

Required/important fields:

- `user`: desktop user running Quickshell; generated from the user who runs the installer.
- `backup_uuid`: filesystem UUID of the removable backup partition.
- `mountpoint`: stable `/etc/fstab` mountpoint.
- `restic_repo`: repository directory on that filesystem.
- `restic_password_file`: root-readable Restic password file.
- `restore_test_path`: an absolute file already included in Restic snapshots; it
  is restored to a temporary directory and byte-compared with the live file.
- `backup_service` / `backup_timer`: existing Restic backup systemd units.
- `maintenance_service` / `maintenance_timer`: existing retention/prune units.

The password value itself must never be committed to this repository.

The installer verifies that Timeshift RSYNC targets the same filesystem UUID as
`backup_uuid`. A mounted filesystem with an empty or mismatched UUID is treated
as unsafe and blocked.
