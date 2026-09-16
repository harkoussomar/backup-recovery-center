# Operations

Normal removable-HDD workflow:

```text
plug disk
→ disk identity recognized
→ mount
→ mounted UUID verified
→ backup / snapshot / check
→ wait for action completion
→ safe eject
→ connected + not mounted
→ unplug
```

The application distinguishes:

```text
unplugged
connected + unmounted
mounted + verified
mounted + wrong/unverifiable filesystem
```

The last state is a critical safety state: repository, Timeshift and recovery
reads/writes are blocked.

SMART checks can run while the filesystem is unmounted because they target the
physical disk. A SMART self-test can also continue after its starter systemd
unit exits, so safe eject checks the drive-level self-test status.

Closing the Quickshell modal does not stop a systemd backup job. The disk must
remain mounted until that job is complete.
