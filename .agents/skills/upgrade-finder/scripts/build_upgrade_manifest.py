#!/usr/bin/env python3
"""Turn a read-only upgrade report into a dedupe-style APPLY manifest.

`find_upgrades.py` reports, per lossy playlist track, a higher-quality file of
the same recording the user already owns. This script converts that report into
the same manifest schema `playlist-dedupe` uses, so the proven
`decide` -> `apply_changes` pipeline can perform the swap (remove the lossy file,
drop the better file in at its position).

Each upgrade becomes a one-winner / one-loser group:
    winner = the better library file (the upgrade)
    loser  = the lossy playlist track (the current copy)
Every group starts `pending`: the user must decide each one (decide_upgrade.py)
before apply_changes.py will write anything -- the same "no default for a write"
safety the dedupe skill enforces.

This script READS the database (to gather each file's full playlist memberships,
including the membership-row id and position that removal/insertion need) but
never writes to it.

Usage:
    python3 build_upgrade_manifest.py <report.json> --out manifest.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from rb_common import (
    build_playlist_index,
    get_db,
    markers_label,
    playlist_memberships,
    track_facts,
)


def _member(content_by_id, tables, db, index, content_id, exclude_terms):
    """Build one manifest member (track_facts + full memberships) for a content
    id, or raise if the file has vanished from the library since the report."""
    cid = str(content_id)
    content = content_by_id.get(cid)
    if content is None:
        raise SystemExit(
            f"Content id {cid} from the report is no longer in the library. "
            f"Re-run find_upgrades.py to regenerate the report."
        )
    m = track_facts(content)
    m["memberships"] = playlist_memberships(db, tables, index, cid, exclude_terms)
    return m


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert an upgrade report into an apply manifest.")
    ap.add_argument("report", help="Path to the JSON report from find_upgrades.py.")
    ap.add_argument("--out", default="manifest.json", help="Where to write the manifest.")
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    upgrades = report.get("upgrades", [])
    exclude_terms = report.get("exclude_path_terms", ["Backup"])

    from pyrekordbox.db6 import tables  # local import; needs the db package

    db = get_db()
    index = build_playlist_index(db)
    # One pass over the library, keyed by id, so each member lookup is cheap and
    # we never depend on a single-row getter's exact return shape.
    content_by_id = {str(c.ID): c for c in db.get_content()}

    groups = []
    for u in upgrades:
        winner = _member(content_by_id, tables, db, index,
                         u["upgrade"]["content_id"], exclude_terms)
        loser = _member(content_by_id, tables, db, index,
                        u["current"]["content_id"], exclude_terms)
        jump = (f"{loser['bitrate']}k {loser['ext'] or '?'} -> "
                f"{winner['bitrate']}k {winner['ext'] or '?'}")
        groups.append({
            "match_type": "upgrade",
            "version_note": jump,
            "marker_label": markers_label(frozenset(loser["markers"])),
            "confidence": u.get("confidence"),
            "winner_content_id": winner["content_id"],
            "decision": "pending",
            "scope": "target_only",
            "actions": [],
            "members": [winner, loser],
        })

    manifest = {
        "kind": "upgrade",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": str(Path(args.report).resolve()),
        "playlist": report.get("playlist", {}),
        "exclude_path_terms": exclude_terms,
        "groups": groups,
    }

    Path(args.out).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    pl = manifest["playlist"]
    print(f"Wrote {args.out}")
    print(f"  Playlist: {pl.get('path')} ({pl.get('id')})")
    print(f"  Upgrade groups (all pending): {len(groups)}")
    if groups:
        print("  Decide each with decide_upgrade.py, then apply_changes.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
