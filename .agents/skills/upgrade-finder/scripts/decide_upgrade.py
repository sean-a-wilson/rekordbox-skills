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

Usage (--scope is REQUIRED with --apply -- there is no default; the operator must
always explicitly choose, after asking the user):
    # replace the lossy file with the better one everywhere it appears (non-protected)
    python3 decide_upgrade.py manifest.json --group 3 --apply --scope everywhere

    # replace it only in the playlist being scanned
    python3 decide_upgrade.py manifest.json --group 3 --apply --scope target-only

    # skip this upgrade -- make no change
    python3 decide_upgrade.py manifest.json --group 3 --skip

Group numbers are 1-based and match show_upgrades.py's '#'.

Bulk-decide Table 1 (the EXACT upgrades -- same recording for sure). The better
file is fixed, so the operator only picks one scope for the whole table, or skips
them all. This is the only batch operation; the 'looks the same' and 'different
versions' tables are always reviewed per group.
    # swap in every exact upgrade -- pick a scope
    python3 decide_upgrade.py manifest.json --all-exact --apply --scope everywhere
    python3 decide_upgrade.py manifest.json --all-exact --apply --scope target-only
    # skip every exact upgrade (make no change for Table 1)
    python3 decide_upgrade.py manifest.json --all-exact --skip
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rb_common import build_group_actions


def main() -> int:
    ap = argparse.ArgumentParser(description="Decide one upgrade group.")
    ap.add_argument("manifest", help="Path to manifest.json from build_upgrade_manifest.")
    ap.add_argument("--group", type=int, help="1-based group number (omit with --all-exact).")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--apply", action="store_true",
                   help="Swap in the better file (remove the lossy copy).")
    g.add_argument("--skip", action="store_true",
                   help="Leave this track unchanged -- make no swap.")
    ap.add_argument("--all-exact", action="store_true",
                    help="Apply to EVERY exact upgrade at once (Table 1). Use with "
                         "--apply --scope to swap all, or --skip to skip all. "
                         "Ignores --group.")
    ap.add_argument("--scope", choices=["target-only", "everywhere"], default=None,
                    help="REQUIRED with --apply (no default -- you must explicitly "
                         "choose): 'everywhere' swaps the lossy file across every "
                         "non-protected playlist it appears in; 'target-only' swaps "
                         "it only in the playlist being scanned.")
    args = ap.parse_args()

    if args.all_exact:
        if args.apply and args.scope is None:
            raise SystemExit("--all-exact --apply needs a scope: '--scope everywhere' "
                             "or '--scope target-only'. Or use --all-exact --skip.")
        if not args.apply and not args.skip:
            raise SystemExit("--all-exact needs --apply (with --scope) or --skip.")
        return _decide_all_exact(args)

    if args.group is None:
        raise SystemExit("--group is required (or use --all-exact for Table 1).")
    if not args.apply and not args.skip:
        raise SystemExit("Choose --apply (with --scope) or --skip.")

    if args.apply and args.scope is None:
        raise SystemExit(
            "--scope is required with --apply. There is no default: choose "
            "'--scope everywhere' (swap the file in every non-protected playlist it "
            "appears in) or '--scope target-only' (only this playlist). The report's "
            "'Playlists that could be upgraded' column shows where the file lives.")

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
    scope = "everywhere" if args.scope == "everywhere" else "target_only"  # args.scope guaranteed set above

    grp["decision"] = "collapse"
    grp["scope"] = scope
    grp["actions"] = build_group_actions(winner, losers, winner_pids, target_pid, scope)

    _report(args.group, grp, members)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def _decide_all_exact(args) -> int:
    """Bulk-decide every exact upgrade (Table 1): swap them all in at the chosen
    scope, or skip them all. Leaves the looks-same and different-version tables
    untouched (those are always reviewed per group)."""
    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    groups = manifest.get("groups", [])
    target_pid = str(manifest["playlist"]["id"])
    exacts = [g for g in groups if g.get("match_type") == "exact"]
    if not exacts:
        print("No exact upgrades to decide.")
        return 0

    if args.skip:
        for grp in exacts:
            grp["decision"] = "keep_all"
            grp["scope"] = "target_only"
            grp["actions"] = []
        print(f"Skipped all {len(exacts)} exact upgrade(s) -- no changes.")
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0

    scope = "everywhere" if args.scope == "everywhere" else "target_only"
    removals = adds = 0
    for grp in exacts:
        members = grp["members"]
        keep = str(grp["winner_content_id"])
        winner = next(m for m in members if m["content_id"] == keep)
        losers = [m for m in members if m["content_id"] != keep]
        winner_pids = {m["playlist_id"] for m in winner["memberships"]}
        grp["decision"] = "collapse"
        grp["scope"] = scope
        grp["actions"] = build_group_actions(winner, losers, winner_pids, target_pid, scope)
        removals += sum(1 for a in grp["actions"] if a["type"] == "remove")
        adds += sum(1 for a in grp["actions"] if a["type"] == "add")

    print(f"Applied {len(exacts)} exact upgrade(s), scope = {scope}: "
          f"{removals} removal(s), {adds} add(s).")
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
