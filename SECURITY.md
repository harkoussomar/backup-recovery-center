# Security Policy

## Threat model

Backup & Recovery Center displays recovery state and starts a small set of
privileged operations. It must never become a general root-command launcher or
silently operate on an unverified filesystem.

## Security invariants

1. The QML/UI and controller run unprivileged.
2. The installer itself must be run as the desktop user, not as root.
3. Polkit authorizes only `start` for an explicit Backup & Recovery Center
   systemd-unit allow-list.
4. `root_actions.py` accepts only fixed action names.
5. Mounted storage is trusted only when the mounted filesystem UUID is present
   and exactly equals the configured backup UUID.
6. Repository, Timeshift and recovery-material reads are blocked on a wrong or
   unverifiable mounted filesystem.
7. Mount action verifies the expected filesystem after mounting and unmounts it
   again if identity verification fails.
8. Safe eject refuses active backup/check work and an in-progress drive SMART
   self-test.
9. `/etc/restic/password` may be referenced by path but is never copied,
   displayed, committed, or placed on recovery media by this project.
10. Unknown/unavailable state is never converted to `0`, `false`, or healthy.
11. Installation is transactional and must roll back on post-publication
    runtime-verification failure.
12. Default uninstall preserves configuration/state; `--purge` is explicit.

## Runtime file permissions

- `/var/lib/backup-recovery/state.json`: `0640`, root + desktop primary group.
- Evidence/activity/check markers: root-only (`0600`) unless a future feature
  explicitly requires broader read access.
- `/etc/backup-recovery/config.json`: root-controlled; it contains no password.

## Reporting

Use the security-reporting mechanism of the hosting forge for vulnerabilities.
Never include passwords, private keys, Restic repository data, recovery
archives, or private system logs in a public issue.
