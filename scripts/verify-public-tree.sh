#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bad=0

CACHE_TMP="$(mktemp -d)"
trap 'rm -rf "$CACHE_TMP"' EXIT
export PYTHONPYCACHEPREFIX="$CACHE_TMP/pycache"

fail_if() {
  local pattern="$1" label="$2"
  if grep -RInE "$pattern" "$ROOT" \
      --exclude-dir='.git' \
      --exclude='verify-public-tree.sh' \
      --exclude='CHECKSUMS.sha256' \
      --exclude='LICENSE' 2>/dev/null; then
    echo "ERROR: public tree contains $label"
    bad=1
  else
    echo "✓ no $label"
  fi
}

fail_if '/home/[A-Za-z0-9._-]+' 'hard-coded personal home path'
fail_if '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}' 'concrete UUID value'
fail_if 'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY' 'private key material'
fail_if 'Authorization:[[:space:]]*Bearer' 'Bearer token'
fail_if 'tail[0-9A-Za-z.-]*\.ts\.net' 'Tailnet hostname'

python3 -m py_compile \
  "$ROOT"/src/backend/*.py \
  "$ROOT"/src/quickshell/scripts/backup-recovery/control_center.py \
  "$ROOT"/installer/shell_edit.py
echo "✓ Python compile"

bash -n "$ROOT/installer/install.sh" "$ROOT/installer/uninstall.sh" "$ROOT/tests/test_installer_sandbox.sh" "$0"
echo "✓ shell syntax"

python3 "$ROOT/tests/test_backend_safety.py"
python3 "$ROOT/tests/test_shell_editor.py"
python3 "$ROOT/tests/test_public_contract.py"
bash "$ROOT/tests/test_installer_sandbox.sh"

if ((bad)); then exit 1; fi
echo "✓ public tree sanitization and regression audit passed"
