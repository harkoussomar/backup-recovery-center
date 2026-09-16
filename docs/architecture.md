# Architecture

```mermaid
flowchart LR
    UI[Quickshell UI<br/>unprivileged]
    Helper[control_center.py<br/>unprivileged]
    Systemd[systemd action units]
    Polkit[Polkit allow-list]
    Root[root_actions.py]
    Collector[state_collector.py]
    State[/var/lib/backup-recovery/state.json]
    Evidence[/var/lib/backup-recovery/evidence.json]
    HDD[Removable backup filesystem]
    Restic[Restic repository]
    Timeshift[Timeshift snapshots]
    Smart[SMART device health]

    UI --> Helper
    Helper -->|read| State
    Helper -->|start fixed unit| Systemd
    Polkit --> Systemd
    Systemd --> Root
    Systemd --> Collector
    Root --> HDD
    Root --> Restic
    Root --> Timeshift
    Root --> Smart
    Collector --> HDD
    Collector --> Restic
    Collector --> Timeshift
    Collector --> Smart
    Collector --> State
    Collector --> Evidence
```

The user interface never receives a general root shell. Privileged work is
represented by fixed systemd units and fixed backend actions.
