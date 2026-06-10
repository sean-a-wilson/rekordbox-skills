#!/usr/bin/env python3
"""Find higher-quality versions you already own of a playlist's low-bitrate tracks.

For each track in the playlist that is lossy and <=320 kbps, this scans the
ENTIRE rekordbox library for a higher-quality file (lossless > bitrate > size) of
the same song, and classifies the pair three ways -- the same buckets the
playlist-dedupe skill uses -- so the report can show three tables:

  * exact              -- same recording for sure (identical version tags,
                          matching length, strong title): a confident upgrade.
  * looks_same         -- no conflicting tags and length+BPM line up tightly:
                          almost certainly the same take (remaster, tag drift).
  * different_versions -- conflicting version tags or notably different
                          length/BPM: a higher-quality DIFFERENT version.

Each candidate is reported once, in the most-confident tier where a real quality
upgrade exists (exact > looks_same > different_versions). The report is read-only
-- it never touches the database; applying a swap is a separate, opt-in step
(build_upgrade_manifest -> decide_upgrade -> apply_changes).

Read-only and deterministic: same library + same thresholds => same report.

Usage:
    python3 find_upgrades.py <playlist_id> [--out upgrades.json]
        [--title-threshold 0.87] [--artist-threshold 0.80]
        [--exclude-path "Backup"]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

# Make the shared module dir (.agents/shared) importable -- one canonical copy,
# no symlinks, runnable from anywhere. Must precede the rb_common import.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from rb_common import (
    DEFAULT_ARTIST_THRESHOLD,
    DEFAULT_TITLE_THRESHOLD,
    build_playlist_index,
    classify_group,
    get_db,
    pair_match,
    playlist_path,
    rank_key,
    track_facts,
)

TIER_ORDER = ["exact", "looks_same", "different_versions"]


def is_excluded(path: str, exclude_terms) -> bool:
    """A playlist is off-limits if any exclude term appears anywhere in its full
    folder path. Case-insensitive substring; protects both a playlist named
    '... Backup' and anything inside a 'Backups/' folder."""
    p = path.lower()
    return any(term.lower() in p for term in exclude_terms)


def memberships_for(db, tables, index, content_id, exclude_terms):
    """Every playlist a given content id belongs to, as full folder paths, with
    protected (Backup) playlists flagged so the report can footnote them."""
    rows = (db.get_playlist_songs()
            .filter(tables.DjmdSongPlaylist.ContentID == content_id,
                    tables.DjmdSongPlaylist.rb_local_deleted == 0)
            .all())
    out = []
    for r in rows:
        pid = str(r.PlaylistID)
        path = playlist_path(index, pid)
        out.append({
            "playlist_id": pid,
            "playlist_name": index[pid].Name if pid in index else "(unknown)",
            "playlist_path": path,
            "excluded": is_excluded(path, exclude_terms),
        })
    out.sort(key=lambda m: m["playlist_path"].lower())
    return out


def is_quality_upgrade(cand: dict, lib: dict) -> bool:
    """A *meaningful* quality jump, not just a bigger file. Either the library
    file is lossless and the candidate isn't (the user's 'lossless always wins'
    rule, regardless of kbps), or both share lossless-ness and the library file
    has a strictly higher bitrate. A same-format same-bitrate file that only wins
    rank_key's file-size/id tiebreak is NOT an upgrade -- it would be noise in a
    report meant to surface real bitrate/format improvements."""
    if lib["lossless"] and not cand["lossless"]:
        return True
    if lib["lossless"] == cand["lossless"] and lib["bitrate"] > cand["bitrate"]:
        return True
    return False


def find_best_upgrade(cand: dict, library: list[dict],
                      title_thr: float, artist_thr: float):
    """Return (best_track, confidence, also_available_count, match_type) for the
    best higher-quality file of the same song, or (None, 0.0, 0, None).

    Every quality-upgrade match is classified (exact / looks_same /
    different_versions) and the candidate is reported once, in the most-confident
    tier that has a match: a sure same-recording upgrade is preferred over a
    looks-same one, which is preferred over a higher-quality different version.
    Within the chosen tier, best = highest quality (rank_key, with a stable id
    tiebreak so the pick is reproducible); also_available counts the rest of that
    tier."""
    tiers: dict[str, list] = {t: [] for t in TIER_ORDER}
    for lib in library:
        if lib["content_id"] == cand["content_id"]:
            continue
        # Cheap pre-filter: only a genuine quality jump is worth the title work.
        if not is_quality_upgrade(cand, lib):
            continue
        matched, conf, exact_ok = pair_match(cand, lib, title_thr, artist_thr)
        if not matched:
            continue
        match_type, _ = classify_group([cand, lib], exact_ok)
        tiers[match_type].append((lib, conf))

    for mt in TIER_ORDER:
        if tiers[mt]:
            best, conf = max(tiers[mt], key=lambda mc: rank_key(mc[0]))
            return best, conf, len(tiers[mt]) - 1, mt
    return None, 0.0, 0, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find higher-quality versions of a playlist's lossy tracks.")
    ap.add_argument("playlist_id", help="The confirmed playlist ID from resolve_playlist.")
    ap.add_argument("--out", default="upgrades.json", help="Where to write the report.")
    ap.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD)
    ap.add_argument("--artist-threshold", type=float, default=DEFAULT_ARTIST_THRESHOLD)
    ap.add_argument("--max-bitrate", type=int, default=320,
                    help="A playlist track is a candidate when it is lossy and at or "
                         "below this bitrate. Default: 320.")
    ap.add_argument("--exclude-path", default="Backup",
                    help="Comma-separated terms; a playlist whose full folder path "
                         "contains one is flagged protected in the report. Default: 'Backup'.")
    args = ap.parse_args()

    exclude_terms = [t.strip() for t in args.exclude_path.split(",") if t.strip()]

    from pyrekordbox.db6 import tables  # local import; needs the db package

    db = get_db()
    index = build_playlist_index(db)
    pid = str(args.playlist_id)
    if pid not in index:
        raise SystemExit(f"No playlist with id {pid}. Run resolve_playlist.py first.")

    target = index[pid]
    target_path = playlist_path(index, pid)

    # The whole library, flattened once.
    library = [track_facts(c) for c in db.get_content()]

    # Candidates: this playlist's lossy tracks at/under the bitrate ceiling.
    # get_playlist_contents does not exclude tombstoned (rb_local_deleted=1)
    # memberships -- removals pending cloud-sync upload -- so intersect with the
    # playlist's live song rows to avoid scanning an already-removed track.
    live_ids = {str(r.ContentID) for r in db.get_playlist_songs()
                .filter(tables.DjmdSongPlaylist.PlaylistID == pid,
                        tables.DjmdSongPlaylist.rb_local_deleted == 0).all()}
    playlist_tracks = [track_facts(c) for c in db.get_playlist_contents(target)
                       if str(c.ID) in live_ids]
    candidates = [t for t in playlist_tracks
                  if not t["lossless"] and 0 < t["bitrate"] <= args.max_bitrate]

    upgrades = []
    no_upgrade = []
    for cand in candidates:
        best, conf, also, match_type = find_best_upgrade(
            cand, library, args.title_threshold, args.artist_threshold)
        if best is None:
            no_upgrade.append({
                "content_id": cand["content_id"],
                "artist": cand["artist"], "title": cand["title"],
                "bitrate": cand["bitrate"], "ext": cand["ext"],
            })
            continue
        cur_mem = memberships_for(db, tables, index, cand["content_id"], exclude_terms)
        up_mem = memberships_for(db, tables, index, best["content_id"], exclude_terms)
        # Note for tables 2 & 3 -- use the chosen pair's real match strength so a
        # strong title match isn't mislabeled "fuzzy".
        _, _, exact_ok = pair_match(cand, best, args.title_threshold, args.artist_threshold)
        _, note = classify_group([cand, best], exact_ok)
        upgrades.append({
            "match_type": match_type,
            "version_note": note,
            "confidence": conf,
            "also_available": also,
            "current": cand,
            "upgrade": best,
            # Where the lossy copy lives (the playlists that could be upgraded)...
            "playlists": [m for m in cur_mem if not m["excluded"]],
            "protected_playlists": [m["playlist_path"] for m in cur_mem if m["excluded"]],
            # ...and where the better file already lives.
            "upgrade_playlists": [m["playlist_path"] for m in up_mem if not m["excluded"]],
        })

    # Deterministic order, grouped by the three report tables: exact first, then
    # looks_same, then different_versions; within each by artist/title/id. Both
    # show_upgrades and build_upgrade_manifest iterate this order, so the group
    # numbers stay consistent between the report and the apply manifest.
    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}
    upgrades.sort(key=lambda u: (tier_rank.get(u["match_type"], 9),
                                 u["current"]["artist"].lower(),
                                 u["current"]["title"].lower(),
                                 u["current"]["content_id"]))
    no_upgrade.sort(key=lambda u: (u["artist"].lower(), u["title"].lower(), u["content_id"]))

    by_tier = {t: sum(1 for u in upgrades if u["match_type"] == t) for t in TIER_ORDER}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {"id": pid, "name": target.Name, "path": target_path},
        "thresholds": {"title": args.title_threshold, "artist": args.artist_threshold,
                       "max_bitrate": args.max_bitrate},
        "exclude_path_terms": exclude_terms,
        "summary": {
            "library_size": len(library),
            "playlist_track_count": len(playlist_tracks),
            "candidates_scanned": len(candidates),
            "upgrades_found": len(upgrades),
            "exact": by_tier["exact"],
            "looks_same": by_tier["looks_same"],
            "different_versions": by_tier["different_versions"],
            "no_upgrade": len(no_upgrade),
        },
        "upgrades": upgrades,
        "no_upgrade_tracks": no_upgrade,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    s = report["summary"]
    print(f"Wrote {args.out}")
    print(f"  Playlist: {target_path} ({s['playlist_track_count']} tracks)")
    print(f"  Library scanned: {s['library_size']} tracks")
    print(f"  Lossy candidates (<= {args.max_bitrate}k): {s['candidates_scanned']}")
    print(f"  Upgrades found: {s['upgrades_found']} "
          f"({s['exact']} exact, {s['looks_same']} look the same, "
          f"{s['different_versions']} different versions)")
    print(f"  No better file: {s['no_upgrade']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
