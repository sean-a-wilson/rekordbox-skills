#!/usr/bin/env python3
"""Resolve a playlist name to concrete matches, each with its full folder path
and track count, so the user can confirm exactly which playlist to operate on
before anything is read or changed.

Playlist names are ambiguous in rekordbox -- a substring like "ML - Disco"
matches many playlists in different folders. This script never writes anything;
it just lists candidates and emits JSON the orchestrator can show.

Usage:
    python3 resolve_playlist.py "ML - Disco"
    python3 resolve_playlist.py "ML - Disco" --exact

Output: JSON to stdout with a `matches` array sorted by path. Each match has
id, name, path, and track_count.
"""
from __future__ import annotations

import argparse
import json
import sys

from rb_common import build_playlist_index, get_db, playlist_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve a rekordbox playlist by name.")
    ap.add_argument("query", help="Playlist name or substring to search for.")
    ap.add_argument("--exact", action="store_true",
                    help="Match the name exactly (case-insensitive) instead of substring.")
    args = ap.parse_args()

    db = get_db()
    index = build_playlist_index(db)

    # Count tracks per playlist in one pass over the membership table. Skip
    # tombstoned rows (rb_local_deleted=1) -- removals pending cloud sync upload
    # are not live members and must not inflate the count.
    counts: dict[str, int] = {}
    for sp in db.get_playlist_songs():
        if getattr(sp, "rb_local_deleted", 0):
            continue
        pid = str(sp.PlaylistID)
        counts[pid] = counts.get(pid, 0) + 1

    q = args.query.strip().lower()
    matches = []
    for pid, node in index.items():
        name = node.Name or ""
        hit = (name.lower() == q) if args.exact else (q in name.lower())
        if not hit:
            continue
        matches.append({
            "id": pid,
            "name": name,
            "path": playlist_path(index, pid),
            "track_count": counts.get(pid, 0),
        })

    # Deterministic order: by path so the list reads top-down through the tree.
    matches.sort(key=lambda m: m["path"].lower())

    json.dump({"query": args.query, "match_count": len(matches), "matches": matches},
              sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
