#!/usr/bin/env python3
"""Apply an approved dedupe manifest to the rekordbox database.

This is the ONLY script that writes. It refuses to run unless:
  1. rekordbox is closed (writing the live DB underneath a running app
     corrupts it), and
  2. a full, timestamped copy of master.db has been saved under the
     'rekordbox-db-backups/' folder beside it (these are kept forever).

Write order is chosen so a track can never be lost: adds are committed first,
then losers are removed. If adds fail, the run aborts before any removal, so the
worst case leaves duplicates rather than a gap. After writing, it verifies the
database matches the manifest and warns (pointing at the DB backup) on any
mismatch.

By default it performs a DRY RUN: it re-reads the manifest and prints exactly
what it would do, touching nothing. Pass --apply to actually write.

Decisions are per group (see decide.py): a `collapse` group removes its loser(s)
per its `scope`, a `keep_all` group is skipped, and a `pending` group (an
undecided version variant) makes this script refuse to run until it is resolved.

Usage:
    python3 apply_changes.py manifest.json              # dry run
    python3 apply_changes.py manifest.json --apply      # write for real
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from rb_common import build_playlist_index, get_db


def rekordbox_running() -> bool:
    """True if a rekordbox process appears to be running. We try pyrekordbox's
    own helper first, then fall back to a psutil name scan."""
    try:
        from pyrekordbox.utils import get_rekordbox_pid
        pid = get_rekordbox_pid()
        if pid:
            return True
    except Exception:
        pass
    try:
        import os
        import psutil
        # Exact basename match, mirroring pyrekordbox's own get_process_id. A
        # substring test would false-positive on unrelated processes whose name
        # merely contains "rekordbox" -- e.g. an editor running in a workspace
        # called "rekordbox-skills" -- and wrongly block apply.
        for p in psutil.process_iter(["name"]):
            proc_name = os.path.splitext((p.info.get("name") or ""))[0].strip().lower()
            if proc_name == "rekordbox":
                return True
    except Exception:
        pass
    return False


def find_db_path(db) -> Path | None:
    """Best-effort locate master.db for backup purposes."""
    for attr in ("db_path", "_db_path", "path"):
        v = getattr(db, attr, None)
        if v:
            return Path(str(v))
    # SQLAlchemy engine holds the real db file path.
    try:
        p = db.engine.url.database
        if p:
            return Path(str(p))
    except Exception:
        pass
    # The db directory plus the conventional filename.
    for attr in ("_db_dir", "db_dir", "directory"):
        v = getattr(db, attr, None)
        if v:
            cand = Path(str(v)) / "master.db"
            if cand.exists():
                return cand
    # Fall back to pyrekordbox config.
    try:
        from pyrekordbox.config import get_config
        p = get_config("rekordbox6", "db_path")
        if p:
            return Path(str(p))
    except Exception:
        pass
    return None


# Root folder that holds all snapshots, and the suffix added to each snapshot
# playlist's name. Snapshots mirror the source folder tree under this root, e.g.
#   Claude Backups / ML / ML - Classics / ML - Disco - Claude Backup - <stamp>
BACKUP_ROOT = "Claude Backups"
BACKUP_SUFFIX = "Claude Backup"

# Folder (inside the rekordbox library root, beside master.db) where a full,
# timestamped copy of the database is written before every apply. These full-DB
# backups are the guaranteed restore point and are kept FOREVER -- the skill
# never prunes them.
DB_BACKUP_DIRNAME = "rekordbox-db-backups"


def collect_actions(manifest: dict):
    """Flatten manifest groups into a global action list, adds before removes so
    every winner exists before any loser is removed.

    Refuses while any group is still `pending` (an undecided version variant) --
    this enforces "no default for variants": the user must rule on each one
    (decide.py) before anything is written. `keep_all` groups are skipped."""
    groups = manifest.get("groups", [])
    pending = [g for g in groups if g.get("decision") == "pending"]
    if pending:
        lines = []
        for g in groups:
            if g.get("decision") != "pending":
                continue
            who = g.get("members", [{}])[0]
            note = g.get("version_note") or g.get("match_type", "")
            lines.append(f"  - {who.get('artist','?')} - {who.get('title','?')}  ({note})")
        raise SystemExit(
            f"{len(pending)} group(s) are still PENDING and must be decided before "
            f"applying. Resolve each with decide_upgrade.py (--apply [--scope ...] "
            f"or --skip):\n"
            + "\n".join(lines)
        )

    adds, removes = [], []
    for g in groups:
        if g.get("decision") == "keep_all":
            continue  # user chose to keep every copy -- no changes for this group
        for a in g.get("actions", []):
            (adds if a["type"] == "add" else removes).append(a)
    return adds, removes


def affected_playlists(adds, removes):
    """Every playlist that will actually be modified, as ordered (id, path, name)
    tuples. These are exactly the playlists worth snapshotting -- protected
    (Backup) playlists never appear in actions, so they're naturally excluded."""
    seen: dict[str, tuple] = {}
    for a in list(adds) + list(removes):
        seen.setdefault(a["playlist_id"], (a["playlist_path"], a["playlist_name"]))
    return sorted(((pid, p, n) for pid, (p, n) in seen.items()),
                  key=lambda t: t[1].lower())


def ancestor_names(index, pid):
    """Folder names from the top of the tree down to the source playlist's
    immediate parent. Walking the real DjmdPlaylist tree (rather than splitting
    the path string) is robust even if a playlist name contains ' / '."""
    names = []
    node = index.get(str(pid))
    if node is None:
        return names
    parent = str(node.ParentID)
    while parent and parent != "root" and parent in index:
        names.insert(0, index[parent].Name or "")
        parent = str(index[parent].ParentID)
    return names


def build_child_index(db):
    """Map (parent_id, name) -> playlist node, so we can find an existing folder
    to reuse instead of creating duplicates."""
    idx = {}
    for p in db.get_playlist():
        idx[(str(p.ParentID), p.Name or "")] = p
    return idx


def ensure_folder(db, child_idx, parent_node, name):
    """Return the child folder `name` under `parent_node`, creating it if absent.
    Keeps child_idx up to date so repeated calls in one run reuse folders."""
    parent_id = str(parent_node.ID)
    node = child_idx.get((parent_id, name))
    if node is not None:
        return node
    node = db.create_playlist_folder(name, parent=parent_node)
    child_idx[(str(node.ParentID), node.Name or "")] = node
    return node


def ensure_backup_root(db, child_idx):
    """Get or create the root-level 'Claude Backups' folder."""
    node = child_idx.get(("root", BACKUP_ROOT))
    if node is not None:
        return node
    node = db.create_playlist_folder(BACKUP_ROOT)
    child_idx[("root", BACKUP_ROOT)] = node
    return node


def snapshot_playlists(db, tables, index, affected, human_stamp):
    """Before anything is modified, copy each affected playlist into the backup
    tree so the user has an in-app restore point. The snapshot lives under
    'Claude Backups' in a folder structure mirroring its source location, and is
    named '<playlist> - Claude Backup - <stamp>'."""
    child_idx = build_child_index(db)
    root = ensure_backup_root(db, child_idx)

    created = []
    for pid, path, name in affected:
        # Recreate the source's folder chain under the backup root.
        parent = root
        for folder_name in ancestor_names(index, pid):
            parent = ensure_folder(db, child_idx, parent, folder_name)

        title = f"{name} - {BACKUP_SUFFIX} - {human_stamp}"
        new_pl = db.create_playlist(title, parent=parent)

        rows = (db.get_playlist_songs()
                .filter(tables.DjmdSongPlaylist.PlaylistID == pid).all())
        rows.sort(key=lambda r: int(r.TrackNo or 0))
        for seq, r in enumerate(rows, 1):
            db.add_to_playlist(new_pl, str(r.ContentID), track_no=seq)

        mirror = " / ".join([BACKUP_ROOT, *ancestor_names(index, pid), title])
        created.append({"source": path, "backup_path": mirror, "tracks": len(rows)})
    return created


def content_in_playlist(db, tables, playlist_id, content_id) -> bool:
    """True if `content_id` is already a member of `playlist_id`. Lets adds be
    idempotent, so a re-run after a partial failure never creates a duplicate
    membership. Tombstoned rows (rb_local_deleted=1) are removals pending cloud
    upload -- they are NOT live memberships, so they are excluded."""
    return (db.get_playlist_songs()
            .filter(tables.DjmdSongPlaylist.PlaylistID == str(playlist_id),
                    tables.DjmdSongPlaylist.ContentID == str(content_id),
                    tables.DjmdSongPlaylist.rb_local_deleted == 0)
            .count() > 0)


def add_with_attr_bypass(db, playlist_id, content_id, track_no=None):
    """Add a track to a playlist, tolerating playlists whose Attribute is not 0.

    pyrekordbox's add_to_playlist rejects any playlist with Attribute != 0
    ("Playlist must be a normal playlist"), but real rekordbox libraries set
    other Attribute flags (e.g. -128) on perfectly ordinary playlists. We refuse
    smart playlists (which carry a SmartList -- adding to them is meaningless),
    but for a flagged-yet-normal playlist we temporarily clear Attribute, add at
    the requested position using pyrekordbox's own logic, then restore the
    original value, so the net change to Attribute is zero."""
    pl = db.get_playlist(ID=str(playlist_id))
    if getattr(pl, "SmartList", None):
        raise ValueError(f"Refusing to add to smart playlist {playlist_id}")
    orig_attr = pl.Attribute
    if orig_attr != 0:
        pl.Attribute = 0
        db.flush()
    try:
        return db.add_to_playlist(str(playlist_id), str(content_id), track_no=track_no)
    finally:
        if orig_attr != 0:
            pl.Attribute = orig_attr
            db.flush()


# A rekordbox-deleted playlist song is not removed from the table -- it is
# TOMBSTONED: the row stays with rb_local_deleted=1 and rb_data_status=262 (a
# live row is rb_data_status=256, rb_local_deleted=0). The bumped row USN that
# commit() assigns is what Cloud Library Sync uploads, so the deletion reaches
# the cloud and is not re-downloaded on the next sync. A hard delete leaves no
# such tombstone, so the cloud's still-present copy wins and the removal reverts
# (observed with Dropbox sync). rb_local_synced=0 marks the row pending upload.
TOMBSTONE_DATA_STATUS = 262


def tombstone_remove(db, tables, song_id, now) -> int:
    """Soft-delete one DjmdSongPlaylist row the way rekordbox does, and return its
    TrackNo so the caller can renumber the rows left behind. The attribute writes
    are tracked by the registry, so commit() assigns the row a fresh local USN."""
    song = db.query(tables.DjmdSongPlaylist).filter_by(ID=str(song_id)).one()
    track_no = int(song.TrackNo or 0)
    song.rb_local_deleted = 1
    song.rb_data_status = TOMBSTONE_DATA_STATUS
    song.rb_local_synced = 0
    song.updated_at = now
    return track_no


def renumber_after_removals(db, tables, playlist_id, removed_track_nos, now) -> None:
    """Close the gaps left by tombstoned rows: every remaining LIVE row shifts down
    by the count of removed positions below it, mirroring rekordbox's own removal.
    Only changed rows are touched, so we don't churn USNs on untouched tracks."""
    removed = sorted(removed_track_nos)
    live = (db.get_playlist_songs()
            .filter(tables.DjmdSongPlaylist.PlaylistID == str(playlist_id),
                    tables.DjmdSongPlaylist.rb_local_deleted == 0)
            .all())
    for row in live:
        old = int(row.TrackNo or 0)
        shift = sum(1 for t in removed if t < old)
        if shift:
            row.TrackNo = old - shift
            row.updated_at = now


def verify_applied(db, tables, adds, removes) -> list[str]:
    """After writing, confirm the DB matches intent: every removed loser is gone
    from its original playlist and every winner is present where it was added.
    (Membership is checked by playlist id, so the backup snapshots -- which have
    their own ids -- never confuse the result.) Tombstoned rows count as gone.
    Returns a list of human-readable discrepancies; empty means all verified."""
    def members(cid):
        rows = (db.get_playlist_songs()
                .filter(tables.DjmdSongPlaylist.ContentID == str(cid),
                        tables.DjmdSongPlaylist.rb_local_deleted == 0).all())
        return {str(r.PlaylistID) for r in rows}

    problems = []
    for r in removes:
        if str(r["playlist_id"]) in members(r["content_id"]):
            problems.append(f"STILL PRESENT: content {r['content_id']} in {r['playlist_path']}")
    for a in adds:
        if str(a["playlist_id"]) not in members(a["content_id"]):
            problems.append(f"MISSING ADD: content {a['content_id']} -> {a['playlist_path']}")
    return problems


def cloud_sync_service(db) -> str:
    """Name of the configured rekordbox Cloud Library Sync service (e.g.
    'Dropbox'), or '' if none. Read-only, best-effort -- used only to print a
    heads-up that tombstoned removals upload on the next sync."""
    try:
        from sqlalchemy import text
        with db.engine.connect() as con:
            row = con.execute(text(
                "SELECT Reserved2 FROM djmdCloudProperty "
                "WHERE Reserved1='SyncService' "
                "AND (rb_local_deleted IS NULL OR rb_local_deleted=0) LIMIT 1"
            )).fetchone()
        return (row[0] or "") if row else ""
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a dedupe manifest.")
    ap.add_argument("manifest", help="Path to manifest.json from detect_duplicates.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes. Without this flag, dry run only.")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    adds, removes = collect_actions(manifest)

    pl = manifest.get("playlist", {})
    print(f"Manifest for: {pl.get('path')} ({pl.get('id')})")
    print(f"  Groups: {len(manifest.get('groups', []))}")
    print(f"  Winner adds: {len(adds)}   Loser removals: {len(removes)}")
    print()

    affected = affected_playlists(adds, removes)
    human_stamp = datetime.now().strftime("%Y-%m-%d | %H:%M")

    if not args.apply:
        print("DRY RUN (no changes written). Planned operations:")
        print(f"\n  First, snapshot {len(affected)} playlist(s), mirroring their folders under '{BACKUP_ROOT}/':")
        for _pid, path, name in affected:
            ancestors = path.split(" / ")[:-1]  # preview only; apply walks the real tree
            mirror = " / ".join([BACKUP_ROOT, *ancestors, f"{name} - {BACKUP_SUFFIX} - {human_stamp}"])
            print(f"    BACKUP  {path}")
            print(f"            -> {mirror}")
        print("\n  Then modify:")
        for a in adds:
            print(f"    ADD     content {a['content_id']} -> {a['playlist_path']} @ {a.get('track_no')}")
        for r in removes:
            print(f"    REMOVE  song {r['song_id']} (content {r['content_id']}) from {r['playlist_path']}")
        print(f"\n  A full, permanent copy of master.db will be saved under '{DB_BACKUP_DIRNAME}/' first.")
        print("Re-run with --apply to write these changes.")
        return 0

    # ---- Safety gates (only reached with --apply) ----
    if rekordbox_running():
        raise SystemExit(
            "rekordbox appears to be RUNNING. Close it completely before applying "
            "changes -- writing the database underneath a live app can corrupt it."
        )

    # pyrekordbox emits a flood of benign "not found in masterPlaylists6.xml"
    # warnings while editing; quiet them so a real error is never buried.
    logging.getLogger("pyrekordbox").setLevel(logging.ERROR)

    db = get_db()
    db_path = find_db_path(db)
    if not (db_path and db_path.exists()):
        raise SystemExit(
            "Could not locate master.db to back up. Refusing to write without a "
            "backup. Locate the file and make a copy manually first."
        )
    # Full, timestamped copy of the database into a dedicated folder beside
    # master.db, kept FOREVER. This is the guaranteed restore point: if anything
    # ever goes wrong, copying this file back over master.db fully reverts the run.
    backup_dir = db_path.parent / DB_BACKUP_DIRNAME
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup)
    print(f"Backed up database -> {backup}")
    print(f"(Full DB backups are kept permanently in '{backup_dir}'.)")

    from pyrekordbox.db6 import tables  # noqa: E402  (needs the db package)

    # ---- Snapshot every affected playlist under the Claude Backups tree ----
    # Done first so the user has an in-app restore point before a single track is
    # moved or removed. Snapshots mirror the source folder structure for easy
    # management. (The full-DB file backup above is the primary restore point;
    # these are the convenient in-app one.)
    try:
        index = build_playlist_index(db)
        snapshots = snapshot_playlists(db, tables, index, affected, human_stamp)
        db.commit()
        print(f"Snapshotted {len(snapshots)} playlist(s) under '{BACKUP_ROOT}/':")
        for s in snapshots:
            print(f"  + {s['backup_path']}  ({s['tracks']} tracks)")
    except Exception as e:
        db.rollback()
        raise SystemExit(f"Failed creating backup playlists -- rolled back, nothing written: {e}")

    # ---- Apply, in an order that CANNOT lose a track ----
    # We do every ADD first and commit them; only once the winners are safely in
    # place do we tombstone the losers (a separate commit). If any add fails we
    # roll back and abort BEFORE removing anything -- so the worst case leaves
    # duplicates in place, never a gap. Adds are idempotent (skip a winner already
    # present), so re-running after a partial failure is safe.
    applied = {"adds": 0, "removes": 0, "skipped_adds": 0}
    try:
        for a in adds:
            if content_in_playlist(db, tables, a["playlist_id"], a["content_id"]):
                applied["skipped_adds"] += 1
                continue
            add_with_attr_bypass(db, a["playlist_id"], a["content_id"], a.get("track_no"))
            applied["adds"] += 1
        db.commit()
    except Exception as e:
        db.rollback()
        raise SystemExit(
            "Add phase failed -- rolled back before removing anything, so NO track "
            "was lost (duplicates simply remain). The only changes made were the "
            f"backup snapshots already created. Restore point: {backup}\nError: {e}"
        )

    # Removals are TOMBSTONES, not hard deletes, so Cloud Library Sync uploads
    # them instead of re-adding the track on the next sync (see tombstone_remove).
    # We tombstone every loser, close the TrackNo gaps among the survivors, then
    # commit once -- a single all-or-nothing removal pass. The winners are already
    # committed above, so even a failed commit here only leaves duplicates behind,
    # never a gap.
    remove_errors = []
    now = datetime.now()
    removed_by_pl: dict[str, list[int]] = {}
    for r in removes:
        try:
            track_no = tombstone_remove(db, tables, r["song_id"], now)
            removed_by_pl.setdefault(str(r["playlist_id"]), []).append(track_no)
            applied["removes"] += 1
        except Exception as e:
            remove_errors.append(f"remove {r['song_id']} from {r['playlist_path']}: {e}")
    for pid, removed_track_nos in removed_by_pl.items():
        renumber_after_removals(db, tables, pid, removed_track_nos, now)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise SystemExit(
            "Removal commit failed -- rolled back, so no loser was removed (the "
            "winners added above remain in place; worst case is leftover "
            f"duplicates, never a gap). Restore point: {backup}\nError: {e}"
        )

    # ---- Verify the database matches the manifest's intent ----
    problems = verify_applied(db, tables, adds, removes)

    # Write an audit log next to the manifest.
    log_path = Path(args.manifest).with_name("applied_log.json")
    log_path.write_text(json.dumps({
        "applied_at": datetime.now().isoformat(),
        "manifest": str(Path(args.manifest).resolve()),
        "db_file_backup": str(backup),
        "snapshot_root": BACKUP_ROOT,
        "snapshot_playlists": snapshots,
        "adds": applied["adds"],
        "skipped_adds_already_present": applied["skipped_adds"],
        "removes": applied["removes"],
        "remove_errors": remove_errors,
        "verification_problems": problems,
    }, indent=2), encoding="utf-8")

    skipped_note = (f", skipped {applied['skipped_adds']} already-present add(s)"
                    if applied["skipped_adds"] else "")
    print(f"\nDone. Added {applied['adds']}, removed {applied['removes']}{skipped_note}.")
    print(f"Created {len(snapshots)} in-app backup playlist(s) under '{BACKUP_ROOT}/'.")
    print(f"Full DB backup: {backup}")
    print(f"Audit log: {log_path}")

    sync = cloud_sync_service(db)
    if sync:
        print(f"\nNote: Cloud Library Sync ({sync}) is enabled. Removals are written as")
        print("tombstones and upload on the next sync -- reopen rekordbox and let a sync")
        print("finish before judging the result, or the cloud copy may look unchanged.")

    if remove_errors:
        print("\nWARNING: some removals failed. Winners were already added, so no")
        print("track was lost -- but a few losers may remain:")
        for e in remove_errors:
            print("  ", e)
    if problems:
        print("\nWARNING: post-apply verification found discrepancies. Your data is")
        print(f"safe -- restore from the full DB backup if needed: {backup}")
        for p in problems:
            print("  ", p)
        return 1

    print("\nVerified: every removal and addition is reflected in the database.")
    return 0
    print("Open rekordbox to verify the results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
