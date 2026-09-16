#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

GLOBAL_BEGIN = "    // >>> backup-recovery-center-state >>>"
GLOBAL_PROP = "    property bool backupRecoveryOpen: false"
GLOBAL_END = "    // <<< backup-recovery-center-state <<<"

IMPORT_BEGIN = "// >>> backup-recovery-center-import >>>"
IMPORT_LINE = "import qs.modules.ii.backupRecovery"
IMPORT_END = "// <<< backup-recovery-center-import <<<"

PANEL_BEGIN = "    // >>> backup-recovery-center-panel >>>"
PANEL_LINE = "    PanelLoader { component: BackupRecovery {} }"
PANEL_END = "    // <<< backup-recovery-center-panel <<<"


class EditError(RuntimeError):
    pass


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".brc-tmp")
    tmp.write_text(text)
    tmp.replace(path)


def add_global(path: Path) -> None:
    text = path.read_text()
    if "property bool backupRecoveryOpen:" in text:
        return
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if re.match(r"^\s*Singleton\s*\{\s*$", line)), None)
    if idx is None:
        raise EditError(f"{path}: expected `Singleton {{` insertion point was not found")
    block = [GLOBAL_BEGIN, GLOBAL_PROP, GLOBAL_END]
    lines[idx + 1:idx + 1] = block
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if out.count("property bool backupRecoveryOpen:") != 1:
        raise EditError(f"{path}: global-state postcondition failed")
    _write(path, out)


def add_family(path: Path) -> None:
    text = path.read_text()
    lines = text.splitlines()

    if IMPORT_LINE not in text:
        import_idxs = [i for i, line in enumerate(lines) if re.match(r"^\s*import\s+\S+", line)]
        if not import_idxs:
            raise EditError(f"{path}: no QML import section found")
        insert_at = max(import_idxs) + 1
        lines[insert_at:insert_at] = [IMPORT_BEGIN, IMPORT_LINE, IMPORT_END]

    joined = "\n".join(lines)
    if PANEL_LINE not in joined:
        # The last standalone closing brace belongs to the top-level component.
        close_idxs = [i for i, line in enumerate(lines) if re.match(r"^\s*\}\s*$", line)]
        if not close_idxs:
            raise EditError(f"{path}: top-level closing brace not found")
        insert_at = close_idxs[-1]
        lines[insert_at:insert_at] = [PANEL_BEGIN, PANEL_LINE, PANEL_END]

    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if out.count(IMPORT_LINE) != 1:
        raise EditError(f"{path}: import postcondition failed")
    if out.count(PANEL_LINE) != 1:
        raise EditError(f"{path}: PanelLoader postcondition failed")
    _write(path, out)


def remove_global(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text()
    lines = text.splitlines()
    out = []
    skip = False
    for line in lines:
        if line.strip() == GLOBAL_BEGIN.strip():
            skip = True
            continue
        if line.strip() == GLOBAL_END.strip():
            skip = False
            continue
        if skip:
            continue
        # Compatibility with pre-marker private/public prototypes.
        if re.search(r"\bproperty\s+bool\s+backupRecoveryOpen\s*:", line):
            continue
        out.append(line)
    _write(path, "\n".join(out) + ("\n" if text.endswith("\n") else ""))


def remove_family(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text()
    lines = text.splitlines()
    out = []
    skip_kind = None
    for line in lines:
        stripped = line.strip()
        if stripped == IMPORT_BEGIN.strip():
            skip_kind = "import"
            continue
        if stripped == IMPORT_END.strip() and skip_kind == "import":
            skip_kind = None
            continue
        if stripped == PANEL_BEGIN.strip():
            skip_kind = "panel"
            continue
        if stripped == PANEL_END.strip() and skip_kind == "panel":
            skip_kind = None
            continue
        if skip_kind:
            continue
        # Compatibility cleanup for prior unmarked integrations.
        if stripped == IMPORT_LINE:
            continue
        if stripped == PANEL_LINE.strip():
            continue
        out.append(line)
    _write(path, "\n".join(out) + ("\n" if text.endswith("\n") else ""))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("--global-states", required=True, type=Path)
    p.add_argument("--family", required=True, type=Path)
    args = p.parse_args()

    if args.action == "add":
        add_global(args.global_states)
        add_family(args.family)
    else:
        remove_global(args.global_states)
        remove_family(args.family)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
