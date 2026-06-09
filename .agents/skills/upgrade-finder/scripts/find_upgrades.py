#!/usr/bin/env python3
"""Find higher-quality versions you already own of a playlist's low-bitrate tracks.

For each track in the playlist that is lossy and <=320 kbps, this scans the
ENTIRE rekordbox library for a file of the **same recording** that ranks higher
on quality (lossless > bitrate > size). It writes a read-only JSON report; it
never touches the database and takes no action -- applying an upgrade is out of
scope for this skill.

"Same recording" is matched strictly so we never suggest a different version as
an upgrade: a strong title match (the dedupe matcher's exact tier, not the loose
fuzzy rescue), an artist match, an **identical version-marker set** (so an
"(Extended Mix)" is never offered as an upgrade for the plain mix), and -- when
both lengths are known -- a close duration. The quality comparison reuses the
same `rank_key` the dedupe skill uses to pick a winner.

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

from rb_common import (
    DEFAULT_ARTIST_THRESHOLD,
    DEFAULT_TITLE_THRESHOLD,
    build_playlist_index,
    bpms_close,
    effective_artist,
    get_db,
    lengths_close,
    playlist_path,
    rank_key,
    similarity,
    track_facts,
)


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


def is_same_recording(cand: dict, lib: dict, title_thr: float, artist_thr: float):
    """Strict 'same recording' test for an upgrade candidate -> the better file.

    Returns a confidence float (0..1) when the pair is the same recording, else
    None. Stricter than the dedupe grouping: the version-marker sets must be
    identical (no alternate versions), the title must clear the *strong* bar
    (the loose duration/BPM rescue is deliberately excluded), the artists must
    match, and any known durations must agree. This guards a report that takes
    no action -- a false positive here is a wrong upgrade suggestion."""
    if cand["content_id"] == lib["content_id"]:
        return None
    # Same version identity: an "(Extended Mix)" is not an upgrade for the plain
    # mix, and vice versa.
    if frozenset(cand["markers"]) != frozenset(lib["markers"]):
        return None

    t_sim = similarity(cand["base_title"], lib["base_title"])
    dur_close = lengths_close(cand["length"], lib["length"])
    bpm_close = bpms_close(cand["bpm"], lib["bpm"])
    # Strong tier only (mirrors detect_duplicates._pair_matches Tier A/B); the
    # loose Tier C fuzzy rescue is intentionally NOT accepted here.
    strong = t_sim >= title_thr or (
        t_sim >= title_thr - 0.10 and dur_close and bpm_close)
    if not strong:
        return None

    a_sim = similarity(effective_artist(cand), effective_artist(lib))
    if a_sim < artist_thr:
        return None

    # When both durations are known they must agree; unknown length is treated
    # as 'no evidence' rather than a disqualifier (same as the dedupe model).
    if cand["length"] and lib["length"] and not dur_close:
        return None

    return round(t_sim * 0.6 + a_sim * 0.4, 3)


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
    """Return (best_track, confidence, also_available_count) for the highest-
    quality same-recording file that is a real upgrade on `cand`, or
    (None, 0.0, 0)."""
    matches = []
    for lib in library:
        # Cheap pre-filter: skip anything that isn't a genuine quality jump, so
        # the expensive similarity work only runs on real upgrade candidates.
        if not is_quality_upgrade(cand, lib):
            continue
        conf = is_same_recording(cand, lib, title_thr, artist_thr)
        if conf is None:
            continue
        matches.append((lib, conf))
    if not matches:
        return None, 0.0, 0
    # Best = highest quality (rank_key already includes a stable id tiebreak so
    # the choice is reproducible). Confidence reported is the chosen match's.
    best, conf = max(matches, key=lambda mc: rank_key(mc[0]))
    return best, conf, len(matches) - 1


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
        best, conf, also = find_best_upgrade(
            cand, library, args.title_threshold, args.artist_threshold)
        if best is None:
            no_upgrade.append({
                "content_id": cand["content_id"],
                "artist": cand["artist"], "title": cand["title"],
                "bitrate": cand["bitrate"], "ext": cand["ext"],
            })
            continue
        memberships = memberships_for(db, tables, index, cand["content_id"], exclude_terms)
        upgrades.append({
            "confidence": conf,
            "also_available": also,
            "current": cand,
            "upgrade": best,
            "playlists": [m for m in memberships if not m["excluded"]],
            "protected_playlists": [m["playlist_path"] for m in memberships if m["excluded"]],
        })

    # Deterministic order: by artist then title (case-insensitive), then id.
    upgrades.sort(key=lambda u: (u["current"]["artist"].lower(),
                                 u["current"]["title"].lower(),
                                 u["current"]["content_id"]))
    no_upgrade.sort(key=lambda u: (u["artist"].lower(), u["title"].lower(), u["content_id"]))

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
    print(f"  Upgrades found: {s['upgrades_found']}")
    print(f"  No better file: {s['no_upgrade']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
