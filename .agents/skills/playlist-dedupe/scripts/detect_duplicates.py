#!/usr/bin/env python3
"""Detect duplicate songs in a playlist and write a change manifest.

This script is read-only -- it never touches the database. It groups the
playlist's tracks by version-aware artist+title similarity (corroborated by
track duration / BPM), picks a *recommended* winner per group (lossless wins,
then higher bitrate, then larger file, then a stable tiebreak), classifies each
group as an exact duplicate or a version variant, and seeds a per-group
decision the user later confirms one group at a time (see decide.py).

Read-only and deterministic: same library + same thresholds => same manifest.

Usage:
    python3 detect_duplicates.py <playlist_id> [--out manifest.json]
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
    build_group_actions,
    build_playlist_index,
    bpms_close,
    bpms_very_close,
    effective_artist,
    get_db,
    length_tolerance,
    lengths_close,
    lengths_very_close,
    markers_label,
    normalize,
    playlist_path,
    rank_key,
    similarity,
    track_facts,
)


def _pair_matches(a: dict, b: dict, title_thr: float, artist_thr: float):
    """Decide whether two tracks belong in the same duplicate group, using the
    cleaned base title plus duration/BPM as corroborating evidence. Returns
    (matched: bool, confidence: float, exact_ok: bool).

    exact_ok is False when the pair was only held together by the loose Tier C
    rescue (weak title, near-identical duration+BPM). Such a pair may be grouped
    for review but must never be auto-collapsed -- the caller downgrades any
    group it taints to a version_variant so the user rules on it."""
    t_sim = similarity(a["base_title"], b["base_title"])
    dur_close = lengths_close(a["length"], b["length"])
    bpm_close = bpms_close(a["bpm"], b["bpm"])

    # Tier A: title alone clears the bar.
    # Tier B: slightly-low title, rescued by close duration AND BPM.
    strong = t_sim >= title_thr or (
        t_sim >= title_thr - 0.10 and dur_close and bpm_close
    )
    # Tier C (loose): a weak title rescued only by near-identical duration AND
    # BPM. Surfaces messy-titled same recordings for review without collapsing.
    loose = t_sim >= title_thr - 0.20 and (
        lengths_very_close(a["length"], b["length"])
        and bpms_very_close(a["bpm"], b["bpm"])
    )
    if not (strong or loose):
        return False, 0.0, False

    a_sim = similarity(effective_artist(a), effective_artist(b))
    if a_sim < artist_thr:
        return False, 0.0, False

    conf = round(t_sim * 0.6 + a_sim * 0.4, 3)
    return True, conf, strong


def cluster_duplicates(tracks, title_thr, artist_thr):
    """Greedy union-find clustering. Two tracks join when base titles are
    similar enough (or rescued by duration+BPM) AND artists are similar enough.
    Returns a list of groups; each is a list of track dicts plus the min
    pairwise confidence that held it together."""
    n = len(tracks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    pair_conf: dict[tuple[int, int], float] = {}
    pair_exact: dict[tuple[int, int], bool] = {}
    for i in range(n):
        for j in range(i + 1, n):
            matched, conf, exact_ok = _pair_matches(
                tracks[i], tracks[j], title_thr, artist_thr)
            if not matched:
                continue
            union(i, j)
            pair_conf[(i, j)] = conf
            pair_exact[(i, j)] = exact_ok

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    result = []
    for members in groups.values():
        if len(members) < 2:
            continue  # not a duplicate -- single track
        member_set = set(members)
        confs = [c for (a, b), c in pair_conf.items()
                 if a in member_set and b in member_set]
        confidence = min(confs) if confs else 0.0
        # A group is exact-eligible only if every matching pair within it cleared
        # the strong title bar; a single loose (Tier C) link makes the whole
        # group review-only (a version_variant), never an auto-collapse.
        exacts = [e for (a, b), e in pair_exact.items()
                  if a in member_set and b in member_set]
        exact_eligible = all(exacts) if exacts else True
        result.append({
            "members": [tracks[m] for m in members],
            "confidence": confidence,
            "exact_eligible": exact_eligible,
        })
    return result


def classify_group(members: list[dict], exact_eligible: bool = True) -> tuple[str, str]:
    """Decide whether a group is an 'exact' duplicate or a 'version_variant',
    and produce a short human note explaining why. A group is exact only when
    every member shares the same distinguishing-marker set AND their durations
    all agree within tolerance AND it wasn't held together by a loose (fuzzy
    title) link; otherwise it is a variant the user must rule on."""
    marker_sets = {frozenset(m["markers"]) for m in members}
    lengths = [m["length"] for m in members if m["length"]]

    length_spread_ok = True
    if len(lengths) >= 2:
        length_spread_ok = (max(lengths) - min(lengths)) <= length_tolerance(
            max(lengths), min(lengths))

    notes = []
    if len(marker_sets) > 1:
        labels = sorted({markers_label(s) for s in marker_sets})
        notes.append(" vs ".join(labels))
    if not length_spread_ok and len(lengths) >= 2:
        def mmss(s):
            return f"{s // 60}:{s % 60:02d}"
        notes.append(f"{mmss(max(lengths))} vs {mmss(min(lengths))}")
    if not exact_eligible:
        notes.append("fuzzy title match -- review")

    if len(marker_sets) == 1 and length_spread_ok and exact_eligible:
        return "exact", ""
    return "version_variant", "; ".join(notes)


def is_excluded(path: str, exclude_terms) -> bool:
    """A playlist is off-limits if any exclude term appears anywhere in its full
    folder path. Case-insensitive substring; protects both a playlist named
    '... Backup' and anything inside a 'Backups/' folder."""
    p = path.lower()
    return any(term.lower() in p for term in exclude_terms)


def memberships_for(db, tables, index, content_id, exclude_terms):
    """Every playlist a given track belongs to, with the membership row id and
    track position. Each entry is flagged `excluded` so protected playlists are
    never acted on. Computed for EVERY member (winner included) so decide.py can
    repick the keeper without touching the database."""
    rows = (db.get_playlist_songs()
            .filter(tables.DjmdSongPlaylist.ContentID == content_id,
                    tables.DjmdSongPlaylist.rb_local_deleted == 0)
            .all())
    out = []
    for r in rows:
        pid = str(r.PlaylistID)
        path = playlist_path(index, pid)
        out.append({
            "song_id": str(r.ID),
            "playlist_id": pid,
            "playlist_name": index[pid].Name if pid in index else "(unknown)",
            "playlist_path": path,
            "track_no": int(r.TrackNo or 0),
            "excluded": is_excluded(path, exclude_terms),
        })
    out.sort(key=lambda m: (m["playlist_path"].lower(), m["track_no"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect duplicates in a playlist.")
    ap.add_argument("playlist_id", help="The confirmed playlist ID from resolve_playlist.")
    ap.add_argument("--out", default="manifest.json", help="Where to write the manifest.")
    ap.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD)
    ap.add_argument("--artist-threshold", type=float, default=DEFAULT_ARTIST_THRESHOLD)
    ap.add_argument("--exclude-path", default="Backup",
                    help="Comma-separated terms; any playlist whose full folder path "
                         "contains one is never modified. Default: 'Backup'. "
                         "Pass '' to disable.")
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
    if is_excluded(target_path, exclude_terms):
        raise SystemExit(
            f"Target playlist '{target_path}' matches an exclude term "
            f"({exclude_terms}) and is protected. Nothing to do. "
            f"Use --exclude-path '' to override."
        )
    # get_playlist_contents does not exclude tombstoned (rb_local_deleted=1)
    # memberships -- removals pending cloud-sync upload -- so intersect with the
    # playlist's live song rows; otherwise an already-removed track would be
    # re-detected as a duplicate.
    live_ids = {str(r.ContentID) for r in db.get_playlist_songs()
                .filter(tables.DjmdSongPlaylist.PlaylistID == pid,
                        tables.DjmdSongPlaylist.rb_local_deleted == 0).all()}
    contents = [c for c in db.get_playlist_contents(target) if str(c.ID) in live_ids]
    tracks = [track_facts(c) for c in contents]

    groups = cluster_duplicates(tracks, args.title_threshold, args.artist_threshold)

    manifest_groups = []
    for g in groups:
        members = sorted(g["members"], key=rank_key, reverse=True)
        winner = members[0]

        match_type, version_note = classify_group(members, g.get("exact_eligible", True))

        # Memberships for EVERY member so decide.py can repick the keeper offline.
        for m in members:
            m["memberships"] = memberships_for(
                db, tables, index, m["content_id"], exclude_terms)

        winner_pids = {mm["playlist_id"] for mm in winner["memberships"]}
        losers = [m for m in members if m["content_id"] != winner["content_id"]]

        # Default decision: exact dupes collapse (this-playlist-only); version
        # variants are pending until the user rules on them.
        decision = "collapse" if match_type == "exact" else "pending"
        scope = "target_only"
        actions = build_group_actions(winner, losers, winner_pids, pid, scope)

        manifest_groups.append({
            "key": normalize(winner["artist"]) + " :: " + winner["base_title"],
            "confidence": g["confidence"],
            "match_type": match_type,
            "version_note": version_note,
            "members": members,
            "recommended_winner_content_id": winner["content_id"],
            "winner_content_id": winner["content_id"],
            "decision": decision,
            "scope": scope,
            "actions": actions,
        })

    # Stable order: pending variants first (they need attention), then by
    # confidence (riskiest fuzzy matches next), then key.
    manifest_groups.sort(key=lambda mg: (
        0 if mg["decision"] == "pending" else 1, mg["confidence"], mg["key"]))

    losing_tracks = sum(len(mg["members"]) - 1 for mg in manifest_groups)
    removes = sum(1 for mg in manifest_groups for a in mg["actions"] if a["type"] == "remove")
    adds = sum(1 for mg in manifest_groups for a in mg["actions"] if a["type"] == "add")
    touched = {a["playlist_id"] for mg in manifest_groups for a in mg["actions"]}
    variants = sum(1 for mg in manifest_groups if mg["match_type"] == "version_variant")

    protected = sorted({
        m["playlist_path"]
        for mg in manifest_groups for mem in mg["members"] for m in mem["memberships"]
        if m.get("excluded")
    })

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {"id": pid, "name": target.Name, "path": playlist_path(index, pid)},
        "thresholds": {"title": args.title_threshold, "artist": args.artist_threshold},
        "exclude_path_terms": exclude_terms,
        "protected_playlists_skipped": protected,
        "summary": {
            "duplicate_groups": len(manifest_groups),
            "version_variant_groups": variants,
            "pending_groups": sum(1 for mg in manifest_groups if mg["decision"] == "pending"),
            "losing_tracks": losing_tracks,
            "membership_removals": removes,
            "winner_adds": adds,
            "playlists_touched": len(touched),
            "protected_playlists_skipped": len(protected),
            "playlist_track_count": len(tracks),
        },
        "groups": manifest_groups,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    s = manifest["summary"]
    print(f"Wrote {args.out}")
    print(f"  Playlist: {manifest['playlist']['path']} ({s['playlist_track_count']} tracks)")
    print(f"  Duplicate groups: {s['duplicate_groups']} "
          f"({s['version_variant_groups']} version variants, {s['pending_groups']} pending)")
    print(f"  Distinct loser tracks: {s['losing_tracks']}")
    print(f"  Default membership removals: {s['membership_removals']}")
    print(f"  Default winner adds (cross-playlist): {s['winner_adds']}")
    print(f"  Playlists touched (by current decisions): {s['playlists_touched']}")
    if exclude_terms:
        print(f"  Protected playlists skipped ({'/'.join(exclude_terms)}): "
              f"{s['protected_playlists_skipped']}")
    if s["pending_groups"]:
        print(f"\n  NOTE: {s['pending_groups']} version-variant group(s) are PENDING "
              f"and must be decided (decide.py) before apply will run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
