# Contributing

Contributions are welcome, especially around portability, state correctness,
accessibility, and safe installation.

Before opening a pull request:

```bash
./scripts/verify-public-tree.sh
```

Please preserve these rules:

- no arbitrary privileged command execution;
- no personal usernames, UUIDs, hostnames, keys, tokens, or backup data;
- `unknown` must remain distinct from `0`, `false`, and `healthy`;
- removable/offline storage is a valid state;
- actions affecting storage must verify the configured filesystem identity;
- new root actions require an explicit systemd unit and Polkit review.

For UI changes, keep compatibility with Illogical Impulse semantic appearance
tokens rather than hard-coded colors/radii where possible.
