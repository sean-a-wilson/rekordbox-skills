#!/usr/bin/env python3
"""Detect duplicate songs in a playlist and write a change manifest.

This script is read-only -- it never touches the database. It groups the
playlist's tracks by version-aware artist+title similarity (corroborated by
track duration / BPM), picks a *recommended* winner per group (lossless wins,
then higher bitrate, then larger file, then a stable tiebreak), classifies each
group three ways (exact / looks_same / different_versions), and seeds a per-group
decision the user later confirms (exact in bulk, the other two one at a time;
see decide.py).

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
    for review but must never be auto-collapsed -- the caller routes any group it
    taints to a reviewed table (looks_same / different_versions), not exact."""
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
        # group review-only (looks_same / different_versions), never auto-collapse.
        exacts = [e for (a, b), e in pair_exact.items()
                  if a in member_set and b in member_set]
        exact_eligible = all(exacts) if exacts else True
        result.append({
            "members": [tracks[m] for m in members],
            "confidence": confidence,
            "exact_eligible": exact_eligible,
        })
    return result


# How a non-exact group is split into "looks the same recording" vs "different
# versions". Two signals decide it:
#   1. HARD version tags. A "remaster"/"remastered" tag is the same recording in
#      better fidelity, so it is treated as SOFT (ignored here). Any other
#      distinguishing tag (Remix, Dub, a named/remixer mix, 12"/7", ...) is HARD.
#      If two copies carry DIFFERENT, non-empty hard-tag sets they are different
#      takes (e.g. "(Remix)" vs "(Labor Of Love Mix)") -- regardless of length.
#   2. Duration + BPM. Absent a hard-tag conflict, copies whose durations line up
#      closely AND whose BPMs agree are almost certainly the same recording (just
#      tagged/encoded differently, or a loose-rescued messy title); a real
#      alternate take almost always changes the length.
LOOKS_SAME_LEN_RATIO = 0.08   # max (max-min)/max duration spread to still look same
LOOKS_SAME_BPM_TOL = 1.5      # max BPM spread to still look same


def _hard_marker_sets(members: list[dict]) -> list[frozenset]:
    """Each member's distinguishing markers with SOFT (remaster) tags removed --
    a remaster is the same recording, not a different version."""
    return [frozenset(mk for mk in m["markers"] if "remaster" not in mk)
            for m in members]


def _looks_same_recording(members: list[dict]) -> bool:
    """True when the group's copies line up tightly enough on duration and BPM to
    most likely be the SAME recording despite differing tags. Used only to split
    the non-exact groups; never promotes anything to an auto-collapse."""
    lengths = [m["length"] for m in members if m["length"]]
    bpms = [m["bpm"] for m in members if m["bpm"]]

    bpm_ok = True
    if len(bpms) >= 2:
        bpm_ok = (max(bpms) - min(bpms)) <= LOOKS_SAME_BPM_TOL
    if not bpm_ok:
        return False

    if len(lengths) >= 2:
        hi = max(lengths)
        return hi > 0 and (hi - min(lengths)) / hi <= LOOKS_SAME_LEN_RATIO
    # No length evidence: rely on BPM essentially matching (already checked).
    return len(bpms) >= 2


def classify_group(members: list[dict], exact_eligible: bool = True) -> tuple[str, str]:
    """Classify a group as one of three kinds and produce a short human note:

      * 'exact'              -- same recording for sure: every member shares the
                                same distinguishing-marker set, durations all
                                agree within tolerance, and it wasn't held
                                together by a loose (fuzzy title) link.
      * 'looks_same'         -- not provably identical, but no conflicting version
                                tags and durations + BPM line up tightly, so it's
                                almost certainly the same take (remaster, neutral
                                tag drift, quality upgrade, or a loose-rescued
                                messy title). Reviewed, not auto-collapsed.
      * 'different_versions' -- genuinely different takes: conflicting version
                                tags, or notably different length/BPM. Reviewed
                                one at a time.
    """
    marker_sets = {frozenset(m["markers"]) for m in members}
    lengths = [m["length"] for m in members if m["length"]]

    length_spread_ok = True
    if len(lengths) >= 2:
        length_spread_ok = (max(lengths) - min(lengths)) <= length_tolerance(
            max(lengths), min(lengths))

    if len(marker_sets) == 1 and length_spread_ok and exact_eligible:
        return "exact", ""

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

    # Two or more distinct, non-empty HARD-tag sets => clearly different versions,
    # no matter how close the durations are.
    distinct_hard = {s for s in _hard_marker_sets(members) if s}
    if len(distinct_hard) >= 2:
        return "different_versions", "; ".join(notes)

    if _looks_same_recording(members):
        return "looks_same", "; ".join(notes)
    return "different_versions", "; ".join(notes)


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

        # Exact dupes pre-decide the keeper (quality rule), but the SCOPE is never
        # assumed -- it starts "unset" so the operator must explicitly choose
        # target_only vs everywhere for every removal (apply refuses while any
        # scope is unset). Version variants stay pending until ruled on too.
        decision = "collapse" if match_type == "exact" else "pending"
        scope = "unset"
        actions = []  # built by decide.py once a scope is explicitly chosen

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

    # Stable order grouped by the three tables the reviewer will see: exact dupes
    # first (batch-decided), then looks-same, then different versions; within each
    # by confidence then key. show_manifest renders one table per type but keeps
    # this single global numbering so decide.py --group N stays consistent.
    type_order = {"exact": 0, "looks_same": 1, "different_versions": 2}
    manifest_groups.sort(key=lambda mg: (
        type_order.get(mg["match_type"], 9), -mg["confidence"], mg["key"]))

    losing_tracks = sum(len(mg["members"]) - 1 for mg in manifest_groups)
    removes = sum(1 for mg in manifest_groups for a in mg["actions"] if a["type"] == "remove")
    adds = sum(1 for mg in manifest_groups for a in mg["actions"] if a["type"] == "add")
    touched = {a["playlist_id"] for mg in manifest_groups for a in mg["actions"]}
    exact_groups = sum(1 for mg in manifest_groups if mg["match_type"] == "exact")
    looks_same = sum(1 for mg in manifest_groups if mg["match_type"] == "looks_same")
    different_versions = sum(1 for mg in manifest_groups
                            if mg["match_type"] == "different_versions")

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
            "exact_groups": exact_groups,
            "looks_same_groups": looks_same,
            "different_version_groups": different_versions,
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
          f"({s['exact_groups']} exact, {s['looks_same_groups']} look the same, "
          f"{s['different_version_groups']} different versions)")
    print(f"  Distinct loser tracks: {s['losing_tracks']}")
    if exclude_terms:
        print(f"  Protected playlists skipped ({'/'.join(exclude_terms)}): "
              f"{s['protected_playlists_skipped']}")
    print(f"\n  NOTE: scope is UNSET for every group -- nothing is removed until you")
    print(f"  explicitly decide. Table 1 (exact) can be replaced in bulk")
    print(f"  (decide.py --all-exact --scope ...); tables 2 & 3 are reviewed one")
    print(f"  at a time (keeper + scope). Apply refuses until all are set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
