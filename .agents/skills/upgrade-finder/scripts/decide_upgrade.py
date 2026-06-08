#!/usr/bin/env python3
"""Decide one upgrade group: approve the swap (with a scope) or skip it.

During review the user is shown one upgrade at a time -- a lossy playlist track
and the higher-quality file of the same recording they already own -- and makes
a single binary choice, exactly like playlist-dedupe: replace the lossy file in
THIS playlist only, or EVERYWHERE it appears (protected/Backup playlists are
never touched). Or skip the upgrade and leave the track as-is.

The winner is always the better file (the group's `winner_content_id`), so unlike
the dedupe `decide.py` the user never has to name a copy to keep -- they only
choose scope or skip. This script edits the manifest JSON only; it does NOT touch
the rekordbox database. Apply still happens later, via apply_changes.py.

Usage:
    # replace the lossy file with the better one, this playlist only (default)
    python3 decide_upgrade.py manifest.json --group 3 --apply

    # replace it everywhere it appears (non-protected playlists)
    python3 decide_upgrade.py manifest.json --group 3 --apply --scope everywhere

    # skip this upgrade -- make no change
    python3 decide_upgrade.py manifest.json --group 3 --skip

Group numbers are 1-based and match show_upgrades.py's '#'.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rb_common import build_group_actions


def main() -> int:
    ap = argparse.ArgumentParser(description="Decide one upgrade group.")
    ap.add_argument("manifest", help="Path to manifest.json from build_upgrade_manifest.")
    ap.add_argument("--group", type=int, required=True, help="1-based group number.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true",
                   help="Swap in the better file (remove the lossy copy).")
    g.add_argument("--skip", action="store_true",
                   help="Leave this track unchanged -- make no swap.")
    ap.add_argument("--scope", choices=["target-only", "everywhere"], default="target-only",
                    help="With --apply: replace the lossy file only in this playlist "
                         "(default) or everywhere it appears (non-protected).")
    args = ap.parse_args()

    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    groups = manifest.get("groups", [])
    if not (1 <= args.group <= len(groups)):
        raise SystemExit(f"--group {args.group} out of range (1..{len(groups)}).")

    grp = groups[args.group - 1]
    members = grp["members"]
    target_pid = str(manifest["playlist"]["id"])

    if args.skip:
        grp["decision"] = "keep_all"
        grp["scope"] = grp.get("scope", "target_only")
        grp["actions"] = []
        _report(args.group, grp, members)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0

    keep = str(grp["winner_content_id"])  # the better file -- fixed, not user-chosen
    winner = next(m for m in members if m["content_id"] == keep)
    losers = [m for m in members if m["content_id"] != keep]
    winner_pids = {m["playlist_id"] for m in winner["memberships"]}
    scope = "everywhere" if args.scope == "everywhere" else "target_only"

    grp["decision"] = "collapse"
    grp["scope"] = scope
    grp["actions"] = build_group_actions(winner, losers, winner_pids, target_pid, scope)

    _report(args.group, grp, members)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def _label(m: dict) -> str:
    return f"{m['artist']} - {m['title']} [{m['ext']} {m['bitrate']}k] (id {m['content_id']})"


def _report(n: int, grp: dict, members: list[dict]) -> None:
    if grp["decision"] == "collapse":
        keep = grp["winner_content_id"]
        print(f"Group {n}: APPLY upgrade, scope = {grp['scope']}")
        for m in members:
            mark = "KEEP " if m["content_id"] == keep else "drop "
            print(f"  {mark}{_label(m)}")
        removes = sum(1 for a in grp["actions"] if a["type"] == "remove")
        adds = sum(1 for a in grp["actions"] if a["type"] == "add")
        print(f"  -> {removes} removal(s), {adds} add(s)")
    else:
        print(f"Group {n}: SKIP (leave unchanged -- no swap)")
        for m in members:
            print(f"  keep {_label(m)}")


if __name__ == "__main__":
    raise SystemExit(main())
