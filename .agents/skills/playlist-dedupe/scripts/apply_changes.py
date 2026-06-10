#!/usr/bin/env python3
"""Apply an approved dedupe manifest -> delegates to .agents/shared/apply_core.py.

Thin entry shim: it puts the shared dir on sys.path, supplies this skill's
user-facing refusal messages, and calls the shared apply engine. All the safety
machinery (rekordbox-closed guard, permanent DB backup, in-app snapshots,
attribute bypass, adds-before-removes ordering, never-assume-scope) lives in
apply_core.py so it stays byte-identical across skills. Platform-agnostic: no
symlinks. By default it performs a DRY RUN; pass --apply to write.

Usage:
    python3 apply_changes.py manifest.json              # dry run
    python3 apply_changes.py manifest.json --apply      # write for real
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from apply_core import main

# Skill-specific refusal messages (each a `{n}`-format template); the shared
# engine appends the offending track list. These name this skill's decide script.
MESSAGES = {
    "pending": (
        "{n} group(s) are still PENDING and must be decided before applying. "
        "Resolve each with decide.py (keep one copy, or --keep-both):"
    ),
    "unset_scope": (
        "{n} collapse group(s) have NO scope chosen. Scope is never assumed -- "
        "choose per group with decide.py (--scope everywhere or --scope "
        "target-only) before applying:"
    ),
}

if __name__ == "__main__":
    raise SystemExit(main(MESSAGES))
