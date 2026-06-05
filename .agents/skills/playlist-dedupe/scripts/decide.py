#!/usr/bin/env python3
"""Record one duplicate group's decision and recompute its actions.

This is the per-group review helper. During review the user is shown one group
at a time (see show_manifest.py --group N) and makes two choices: which copy to
keep (or keep both), and whether the loser is replaced only in the playlist
being deduped or everywhere it appears. This script writes that decision back
into the manifest and rebuilds just that group's add/remove actions, using the
same shared builder detect_duplicates.py uses -- so a confirmed/overridden
decision produces identical operations.

It is read-only with respect to the rekordbox database: it only edits the
manifest JSON (every member already carries its playlist memberships, so no DB
access is needed). Apply still happens later, via apply_changes.py.

Usage:
    # keep a specific copy; clean only this playlist (default scope)
    python3 decide.py manifest.json --group 3 --keep <content_id>

    # keep a specific copy; also replace the loser everywhere it appears
    python3 decide.py manifest.json --group 3 --keep <content_id> --scope everywhere

    # keep both copies (no change for this group)
    python3 decide.py manifest.json --group 3 --keep-both

Group numbers are 1-based and match show_manifest.py's '#'.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rb_common import build_group_actions


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a duplicate group's decision.")
    ap.add_argument("manifest", help="Path to manifest.json.")
    ap.add_argument("--group", type=int, required=True, help="1-based group number.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--keep", metavar="CONTENT_ID",
                   help="content_id of the copy to keep (collapse the rest).")
    g.add_argument("--keep-both", action="store_true",
                   help="Keep every copy in this group -- make no changes for it.")
    ap.add_argument("--scope", choices=["target-only", "everywhere"], default="target-only",
                    help="With --keep: remove the loser only from this playlist "
                         "(default) or everywhere it appears.")
    args = ap.parse_args()

    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    groups = manifest.get("groups", [])
    if not (1 <= args.group <= len(groups)):
        raise SystemExit(f"--group {args.group} out of range (1..{len(groups)}).")

    grp = groups[args.group - 1]
    members = grp["members"]
    target_pid = str(manifest["playlist"]["id"])

    if args.keep_both:
        grp["decision"] = "keep_all"
        grp["scope"] = grp.get("scope", "target_only")
        grp["actions"] = []
        _report(args.group, grp, members)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0

    keep = str(args.keep)
    ids = {m["content_id"] for m in members}
    if keep not in ids:
        raise SystemExit(
            f"content_id {keep} is not in group {args.group}. Members: {sorted(ids)}")

    scope = "everywhere" if args.scope == "everywhere" else "target_only"
    winner = next(m for m in members if m["content_id"] == keep)
    losers = [m for m in members if m["content_id"] != keep]
    winner_pids = {m["playlist_id"] for m in winner["memberships"]}

    grp["winner_content_id"] = keep
    grp["decision"] = "collapse"
    grp["scope"] = scope
    grp["actions"] = build_group_actions(winner, losers, winner_pids, target_pid, scope)

    _report(args.group, grp, members)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def _label(m: dict) -> str:
    return f"{m['artist']} - {m['title']} [{m['ext']} {m['bitrate']}k] (id {m['content_id']})"


def _report(n: int, grp: dict, members: list[dict]) -> None:
    print(f"Group {n}: decision = {grp['decision']}", end="")
    if grp["decision"] == "collapse":
        print(f", scope = {grp['scope']}")
        keep = grp["winner_content_id"]
        for m in members:
            mark = "KEEP " if m["content_id"] == keep else "drop "
            print(f"  {mark}{_label(m)}")
        removes = sum(1 for a in grp["actions"] if a["type"] == "remove")
        adds = sum(1 for a in grp["actions"] if a["type"] == "add")
        print(f"  -> {removes} removal(s), {adds} add(s)")
    else:
        print(" (keeping all copies -- no change)")
        for m in members:
            print(f"  keep {_label(m)}")


if __name__ == "__main__":
    raise SystemExit(main())
