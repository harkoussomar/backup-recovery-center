# Runtime State Model

The model deliberately separates physical presence, filesystem access, and
backup evidence.

```text
disk disconnected
disk connected + unmounted
disk connected + mounted
disk connected + mounted + repository accessible
```

Repository state separately tracks:

```text
live
cached / last-known verified
unavailable / unknown
```

Therefore:

- `count = 0` means a successful inventory found zero items.
- `count = null` means the inventory is not currently known.
- disconnecting the HDD does not erase previously verified evidence.

This invariant is critical for recovery software: inability to query a device
must never be presented as loss of the backup.
