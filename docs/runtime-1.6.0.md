# Runtime contract 1.6.0

This branch synchronizes the public source with the locally validated Backup &
Recovery Center deployment produced by the four-phase reliability/security review.

## Included

- freshness-aware protection truth
- fail-closed mounted filesystem identity
- global backend-owned action state
- cross-page and close/reopen operation continuity
- root storage serialization and durable atomic state writes
- post-backup snapshot proof
- repository/snapshot provenance
- rotating Restic data verification
- dated credential, boot-media and second-copy evidence
- global operation component and modal accessibility fixes
- compact navigation
- instant cached first paint with expensive refresh in the background
- standard desktop lifecycle notifications
- deployment doctor, self-test, evidence and recovery-drill tools

## Packaging note

The runtime source in this branch is synchronized from the validated deployment.
The public installer/release packaging should be promoted only after its installer
contract is updated to install the new tools, hardening drop-ins and 1.6.0 runtime
endpoint. Keeping this on a review branch prevents the old alpha installer from
being presented as compatible before that packaging step is completed.
