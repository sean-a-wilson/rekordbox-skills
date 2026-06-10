#!/usr/bin/env python3
"""Entry shim -> the one canonical implementation at .agents/shared/resolve_playlist.py.

Adds the shared dir to the front of sys.path so `resolve_playlist` (and the
`rb_common` it imports) resolve to the single shared copy, then delegates. Keeping
this thin file means the documented `python3 scripts/resolve_playlist.py "name"`
invocation is unchanged. Platform-agnostic: no symlinks.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from resolve_playlist import main

if __name__ == "__main__":
    raise SystemExit(main())
