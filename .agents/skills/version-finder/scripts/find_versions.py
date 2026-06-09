#!/usr/bin/env python3
"""Find every version, edit, and remix of a song you already own (read-only).

Takes an "Artist - Song Title" query and scans the ENTIRE rekordbox library for
all files of the same song -- the original, the extended mix, the dub, each
remix -- groups them by version, and records which playlists each file lives in.
It writes a read-only JSON report; it NEVER touches the database and takes no
action, so rekordbox can stay open.

Two ways a library track joins the result:
  * Title match -- a strong token-sorted similarity between the query title and
    the track's *clean base title* (with any embedded "Artist - " prefix stripped
    so a blank-Artist row like "Sister Sledge - Lost In Music" still matches), with
    the artist used only as a soft boost, never a hard filter.
  * Comments token -- a user-authored bracketed token in DjmdContent.Commnt such
    as "[edit of: Chic - Everybody Dance]" / "[sample: ...]" lets a renamed edit
    with zero shared title text join the right song. Tokens are READ ONLY.

One row per distinct file: two files of the same version but different format or
bitrate are two rows, so you can compare every copy you own. Only byte-identical
true dupes (same version + format + bitrate + length) collapse into one row with
a x N note.

Read-only and deterministic: same library + same thresholds => same report.

Usage:
    python3 find_versions.py "Artist - Song Title" [--out versions.json]
        [--title-threshold 0.87] [--artist-threshold 0.80] [--exclude-path "Backup"]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from rb_common import (
    DEFAULT_ARTIST_THRESHOLD,
    DEFAULT_TITLE_THRESHOLD,
    build_playlist_index,
    effective_artist,
    get_db,
    is_excluded,
    markers_label,
    normalize,
    playlist_path,
    similarity,
    track_facts,
)

# User-authored relationship token in the Comments field. Tolerant of other text
# around it (MIK writes energy/key into Comments), so it matches anywhere in the
# string and is case-insensitive. Examples it accepts:
#   [edit of: Chic - Everybody Dance]   [sample: The Whispers - Headlights]
#   [remix of: ...]   [flip of: ...]    [bootleg of: ...]
TOKEN_RE = re.compile(
    r"\[\s*(edit|sample|remix|flip|bootleg)\s*(?:of)?\s*:\s*(?P<target>[^\]]+?)\s*\]",
    re.IGNORECASE,
)


def parse_query(q: str) -> tuple[str, str]:
    """Split a free-text query on the first ' - ' into (artist, title). With no
    ' - ', the whole string is the title and artist is empty."""
    if " - " in q:
        artist, title = q.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", q.strip()


def facts_with_comment(content) -> dict:
    """track_facts plus the raw Comments field. Read directly off the row so the
    shared rb_common.track_facts stays byte-identical across skills."""
    t = track_facts(content)
    t["comment"] = (getattr(content, "Commnt", None) or "")
    return t


def clean_base(track: dict) -> str:
    """The base title to score against, with an embedded leading 'artist - '
    stripped. rb_common.split_title only peels version tags off the RIGHT, so a
    blank-Artist row keeps the artist glued to the LEFT of the base
    ('sister sledge lost in music'). When the Artist relationship is blank and the
    title carried a ' - ', strip the same leading segment effective_artist
    extracts so we score title-against-title."""
    base = track.get("base_title", "")
    if (track.get("artist") or "").strip():
        return base
    ea = normalize(effective_artist(track))
    if ea and base.startswith(ea + " "):
        return base[len(ea) + 1:].strip()
    return base


def title_match(track: dict, artist_q: str, title_q: str,
                title_thr: float, artist_thr: float):
    """Soft-artist title match. Returns a confidence (0..1) when the track is the
    query song, else None.

    Title similarity is the gate; a strong title alone always passes (so a remix
    with a blank Artist field still surfaces). The artist is only a *rescue* that
    lets a slightly-weaker title through when the artist also agrees -- it is
    never a hard requirement."""
    cb = clean_base(track)
    t_sim = similarity(title_q, cb)
    if artist_q:
        # Second signal: the query's combined "artist - title" vs the raw base,
        # which still carries the embedded artist for blank-Artist rows.
        t_sim = max(t_sim, similarity(f"{artist_q} - {title_q}", track.get("base_title", "")))
    a_sim = similarity(artist_q, effective_artist(track)) if artist_q else 1.0
    strong = t_sim >= title_thr or (t_sim >= title_thr - 0.10 and a_sim >= artist_thr)
    if not strong:
        return None
    return round(t_sim * 0.7 + a_sim * 0.3, 3)


def parse_tokens(comment: str) -> list[dict]:
    """Every relationship token in a Comments field, as
    {rel, target_raw, target_artist, target_title}."""
    out = []
    for m in TOKEN_RE.finditer(comment or ""):
        target_raw = m.group("target").strip()
        t_artist, t_title = parse_query(target_raw)
        out.append({
            "rel": m.group(1).lower(),
            "target_raw": target_raw,
            "target_artist": t_artist,
            "target_title": t_title,
        })
    return out


def token_hits_query(tokens: list[dict], artist_q: str, title_q: str,
                     title_thr: float) -> bool:
    """True when any token's target names the query song (so a renamed edit with
    no shared title text still joins)."""
    for tok in tokens:
        if similarity(title_q, tok["target_title"]) >= title_thr:
            return True
        if artist_q and similarity(
                f"{artist_q} - {title_q}",
                f"{tok['target_artist']} - {tok['target_title']}") >= title_thr:
            return True
    return False


def version_label(markers) -> str:
    """Version label for a marker set. The neutral (empty) bucket is 'Original'
    rather than rb_common's 'plain', since this report is about naming versions."""
    ms = frozenset(markers)
    return "Original" if not ms else markers_label(ms)


def relationship_label(tokens: list[dict]) -> str:
    """Human relationship for the row, e.g. 'edit of Chic - Everybody Dance'.
    Uses the first token on the track."""
    if not tokens:
        return ""
    tok = tokens[0]
    return f"{tok['rel']} of {tok['target_raw']}"


def memberships_for(db, tables, index, content_id, exclude_terms):
    """Every playlist a given content id belongs to, as full folder paths, with
    protected (Backup) playlists flagged so the report can footnote them."""
    rows = (db.get_playlist_songs()
            .filter(tables.DjmdSongPlaylist.ContentID == str(content_id))
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


def collapse_key(track: dict) -> tuple:
    """Two files collapse into one row only when they are the same version AND the
    same format, bitrate, and length -- i.e. byte-identical true dupes. Any
    difference in format or bitrate keeps them on separate rows so the user can
    compare every copy."""
    return (tuple(sorted(track["markers"])), track["ext"], track["bitrate"], track["length"])


def lossless_rank(track: dict) -> tuple:
    """Within a version: lossless first, then higher bitrate."""
    return (1 if track["lossless"] else 0, track["bitrate"])


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find every version/edit/remix of a song you own (read-only).")
    ap.add_argument("query", help='"Artist - Song Title" (or just a title).')
    ap.add_argument("--out", default="/tmp/rb-versions.json", help="Where to write the report.")
    ap.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD)
    ap.add_argument("--artist-threshold", type=float, default=DEFAULT_ARTIST_THRESHOLD)
    ap.add_argument("--exclude-path", default="Backup",
                    help="Comma-separated terms; a playlist whose full folder path "
                         "contains one is flagged protected in the report. Default: 'Backup'.")
    args = ap.parse_args()

    exclude_terms = [t.strip() for t in args.exclude_path.split(",") if t.strip()]
    artist_q, title_q = parse_query(args.query)

    from pyrekordbox.db6 import tables  # local import; needs the db package

    db = get_db()
    index = build_playlist_index(db)

    library = [facts_with_comment(c) for c in db.get_content()]

    # Match: title (soft-artist) OR a Comments token that names the query song.
    matched = []
    for t in library:
        tokens = parse_tokens(t["comment"])
        conf = title_match(t, artist_q, title_q, args.title_threshold, args.artist_threshold)
        via_token = token_hits_query(tokens, artist_q, title_q, args.title_threshold)
        if conf is None and not via_token:
            continue
        t["_confidence"] = conf if conf is not None else 1.0
        t["_relationship"] = relationship_label(tokens)
        t["_via_token"] = conf is None and via_token
        matched.append(t)

    # Group one row per distinct file; collapse only byte-identical true dupes.
    groups: dict[tuple, dict] = {}
    for t in matched:
        key = collapse_key(t)
        g = groups.get(key)
        if g is None:
            memberships = memberships_for(db, tables, index, t["content_id"], exclude_terms)
            groups[key] = {
                "label": version_label(t["markers"]),
                "title": t["title"],
                "relationship": t["_relationship"],
                "via_token": t["_via_token"],
                "markers": t["markers"],
                "format": t["ext"] or "?",
                "bitrate": t["bitrate"],
                "bpm": t["bpm"],
                "key": t["key"],
                "length": t["length"],
                "lossless": t["lossless"],
                "count": 1,
                "confidence": t["_confidence"],
                "content_ids": [t["content_id"]],
                "_members": {m["playlist_id"]: m for m in memberships},
            }
        else:
            g["count"] += 1
            g["content_ids"].append(t["content_id"])
            # Union playlist memberships across the collapsed copies.
            for m in memberships_for(db, tables, index, t["content_id"], exclude_terms):
                g["_members"].setdefault(m["playlist_id"], m)
            if t["_relationship"] and not g["relationship"]:
                g["relationship"] = t["_relationship"]

    versions = []
    for g in groups.values():
        members = sorted(g.pop("_members").values(), key=lambda m: m["playlist_path"].lower())
        g["playlists"] = [m["playlist_path"] for m in members if not m["excluded"]]
        g["protected_playlists"] = [m["playlist_path"] for m in members if m["excluded"]]
        versions.append(g)

    # "Original" first, then alphabetically by version label; within a label,
    # lossless first then higher bitrate.
    versions.sort(key=lambda v: (
        0 if v["label"] == "Original" else 1,
        v["label"].lower(),
        0 if v["lossless"] else 1,
        -v["bitrate"],
    ))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": {"raw": args.query, "artist": artist_q, "title": title_q},
        "thresholds": {"title": args.title_threshold, "artist": args.artist_threshold},
        "exclude_path_terms": exclude_terms,
        "summary": {
            "library_size": len(library),
            "matched_files": len(matched),
            "version_rows": len(versions),
        },
        "versions": versions,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    s = report["summary"]
    print(f"Wrote {args.out}")
    print(f"  Query: artist={artist_q!r} title={title_q!r}")
    print(f"  Library scanned: {s['library_size']} tracks")
    print(f"  Files matched: {s['matched_files']}")
    print(f"  Version rows: {s['version_rows']}")
    if s["version_rows"] == 0:
        print("  No versions of this song found in your library.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
