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

Usage (--scope is REQUIRED with --keep -- there is no default; the operator must
always explicitly choose, after asking the user):
    # keep a specific copy; remove the loser everywhere it appears (non-backup)
    python3 decide.py manifest.json --group 3 --keep <content_id> --scope everywhere

    # keep a specific copy; clean only the playlist being deduped
    python3 decide.py manifest.json --group 3 --keep <content_id> --scope target-only

    # keep both copies (no change for this group)
    python3 decide.py manifest.json --group 3 --keep-both

Group numbers are 1-based and match show_manifest.py's '#'.

Bulk decide for Table 1 (the EXACT dupes -- same recording for sure). These have
a pre-decided keeper (the quality winner), so the operator only chooses one scope
for the whole table, or keeps them all. This is the only batch operation; the
'looks the same' and 'different versions' tables are always reviewed per group.
    # replace every exact dupe (keep each group's quality winner) -- pick a scope
    python3 decide.py manifest.json --all-exact --scope everywhere
    python3 decide.py manifest.json --all-exact --scope target-only
    # keep all copies of every exact dupe (make no change for Table 1)
    python3 decide.py manifest.json --all-exact --keep-both
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rb_common import build_group_actions


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a duplicate group's decision.")
    ap.add_argument("manifest", help="Path to manifest.json.")
    ap.add_argument("--group", type=int, help="1-based group number (omit with --all-exact).")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--keep", metavar="CONTENT_ID",
                   help="content_id of the copy to keep (collapse the rest).")
    g.add_argument("--keep-both", action="store_true",
                   help="Keep every copy in this group -- make no changes for it.")
    ap.add_argument("--all-exact", action="store_true",
                    help="Apply to EVERY exact-dupe group at once (Table 1), keeping "
                         "each group's recommended quality winner. Use with --scope "
                         "to replace all, or --keep-both to keep all. Ignores --group.")
    ap.add_argument("--scope", choices=["target-only", "everywhere"], default=None,
                    help="REQUIRED with --keep (no default -- you must explicitly "
                         "choose): 'everywhere' removes the loser from every "
                         "non-backup playlist it appears in; 'target-only' removes "
                         "it only from the playlist being deduped.")
    args = ap.parse_args()

    if args.all_exact:
        if args.keep:
            raise SystemExit("--all-exact uses each group's recommended winner; "
                             "don't pass --keep. Use --scope (replace all) or "
                             "--keep-both (keep all).")
        if not args.keep_both and args.scope is None:
            raise SystemExit("--all-exact needs a scope: '--scope everywhere' / "
                             "'--scope target-only' to replace all exact dupes, or "
                             "'--keep-both' to keep them all.")
        return _decide_all_exact(args)

    if args.group is None:
        raise SystemExit("--group is required (or use --all-exact for Table 1).")
    if not args.keep and not args.keep_both:
        raise SystemExit("Choose --keep <content_id> (with --scope) or --keep-both.")

    if args.keep and args.scope is None:
        raise SystemExit(
            "--scope is required with --keep. There is no default: choose "
            "'--scope everywhere' (remove the loser from every non-backup playlist "
            "it appears in) or '--scope target-only' (remove it only from this "
            "playlist). Check the overview's 'Lives in' column to see where it lives.")

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


def _decide_all_exact(args) -> int:
    """Bulk-decide every exact-dupe group (Table 1). Keeps each group's recommended
    quality winner; either collapses them all at the chosen scope or keeps them
    all. Leaves the 'looks the same' and 'different versions' tables untouched."""
    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    groups = manifest.get("groups", [])
    target_pid = str(manifest["playlist"]["id"])

    exacts = [g for g in groups if g.get("match_type") == "exact"]
    if not exacts:
        print("No exact-dupe groups to decide.")
        return 0

    if args.keep_both:
        for grp in exacts:
            grp["decision"] = "keep_all"
            grp["scope"] = "target_only"
            grp["actions"] = []
        print(f"Kept all copies for {len(exacts)} exact group(s) -- no changes.")
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0

    scope = "everywhere" if args.scope == "everywhere" else "target_only"
    removals = adds = 0
    for grp in exacts:
        members = grp["members"]
        keep = grp.get("recommended_winner_content_id") or members[0]["content_id"]
        winner = next(m for m in members if m["content_id"] == keep)
        losers = [m for m in members if m["content_id"] != keep]
        winner_pids = {m["playlist_id"] for m in winner["memberships"]}
        grp["winner_content_id"] = keep
        grp["decision"] = "collapse"
        grp["scope"] = scope
        grp["actions"] = build_group_actions(winner, losers, winner_pids, target_pid, scope)
        removals += sum(1 for a in grp["actions"] if a["type"] == "remove")
        adds += sum(1 for a in grp["actions"] if a["type"] == "add")

    print(f"Collapsed {len(exacts)} exact group(s), scope = {scope}: "
          f"{removals} removal(s), {adds} add(s).")
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
