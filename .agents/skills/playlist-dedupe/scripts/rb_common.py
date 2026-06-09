"""Shared helpers for the playlist-dedupe skill.

Everything that needs to be deterministic lives here: how we connect to the
database, how we normalize artist/title strings, how we decide which of two
files is "better", and how we resolve a playlist's folder path. The three
entry-point scripts (resolve_playlist, detect_duplicates, apply_changes) all
import from this module so they share one source of truth.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

# Lossless extensions. The user's rule is "lossless always wins" over any lossy
# file regardless of the kbps number, so we key off the container extension,
# which is the reliable signal (FileType integer codes are ambiguous).
LOSSLESS_EXTS = {"wav", "aiff", "aif", "flac", "alac"}

# Default fuzzy thresholds. Title is weighted more heavily than artist because
# artist tags are noisier (feat., remixer credits, label aliases). These are
# overridable from the CLI so they can be tuned after reviewing a manifest.
# The title threshold applies to the *cleaned base title* (see split_title),
# which strips neutral version tags -- so a slightly lower default than before
# is appropriate now that we compare like-with-like.
DEFAULT_TITLE_THRESHOLD = 0.87
DEFAULT_ARTIST_THRESHOLD = 0.80

# How close two durations must be to count as the same recording: the larger of
# 3 seconds or 2% of the longer track. Used both to corroborate a match and to
# decide whether a group is an "exact" dupe or needs review (looks_same /
# different_versions).
def length_tolerance(a: int, b: int) -> int:
    return max(3, round(0.02 * max(a, b)))


def lengths_close(a: int, b: int) -> bool:
    """True if both lengths are known and within tolerance. Unknown (0) lengths
    are treated as 'no evidence' -> not a positive match on their own."""
    if not a or not b:
        return False
    return abs(a - b) <= length_tolerance(a, b)


def bpms_close(a: float, b: float) -> bool:
    """True if both BPMs are known and within 0.5. Unknown (0) -> no evidence."""
    if not a or not b:
        return False
    return abs(a - b) <= 0.5


def lengths_very_close(a: int, b: int, tol: int = 2) -> bool:
    """Tighter than lengths_close: both known and within `tol` seconds. Used by
    the loose title rescue, where near-identical duration is strong corroboration
    that two messy-titled tracks are the same recording."""
    if not a or not b:
        return False
    return abs(a - b) <= tol


def bpms_very_close(a: float, b: float, tol: float = 1.0) -> bool:
    """Both BPMs known and within `tol`. Companion to lengths_very_close for the
    loose title rescue."""
    if not a or not b:
        return False
    return abs(a - b) <= tol


def get_db():
    """Open the rekordbox database. Raises a clear error if pyrekordbox is
    missing so the caller can tell the user how to fix it."""
    try:
        from pyrekordbox import Rekordbox6Database
    except ImportError as e:
        raise SystemExit(
            "pyrekordbox is not installed. Run:\n"
            "  pip3 install pyrekordbox --break-system-packages"
        ) from e
    return Rekordbox6Database()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
# Real metadata is dirty: casing differs, punctuation drifts, "feat." appears in
# both the artist and title fields, parenthetical mix tags come and go. We map
# each string to a canonical form so two spellings of the same thing compare as
# close. This function is intentionally fixed and boring -- the whole point is
# that the same input always normalizes the same way.

_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with)\b.*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str | None, strip_feat: bool = True) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    if strip_feat:
        s = _FEAT_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def token_sorted(text: str) -> str:
    """Sort tokens so word order doesn't matter ('smith jane' == 'jane smith').
    Helps when artist/title word order varies between sources."""
    return " ".join(sorted(normalize(text).split()))


def similarity(a: str, b: str) -> float:
    """Deterministic 0..1 string similarity using stdlib difflib (no external
    fuzzy dependency needed). Token-sorted so order doesn't matter."""
    sa, sb = token_sorted(a), token_sorted(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return SequenceMatcher(None, sa, sb).ratio()


# ---------------------------------------------------------------------------
# Version-aware title parsing
# ---------------------------------------------------------------------------
# A title like "Disco Nights (Rock Freak) (Original Mix)" carries three kinds of
# information: the core song name ("Disco Nights"), a parenthetical that is part
# of the song's identity ("Rock Freak"), and a *version* tag ("Original Mix").
# To match duplicates well we must:
#   - DROP neutral version tags ("Original Mix", "Album Version", ...) so a plain
#     title and its "(Original Mix)" twin reduce to the same base and match;
#   - KEEP distinguishing version tags ("Extended Mix", "Dub", "Radio Edit", ...)
#     as a separate signal so genuine alternate versions are flagged, not merged;
#   - KEEP an unknown parenthetical ("Rock Freak") as part of the base, because
#     it identifies the song rather than a version of it.

# Neutral tags are matched by EXACT (normalized) equality and simply dropped.
NEUTRAL_MARKERS = {
    "original mix", "original", "album version", "album",
    "main mix", "main", "single version", "single", "lp version",
    "version", "stereo", "mono", "full length", "full version",
    "original version", "main version", "mono version", "stereo version",
    "mix",
}

# Words that, on their own, don't name a distinct version. A parenthetical that
# is just these words plus a trailing "version"/"mix" (e.g. "Original Version",
# "Stereo Mix") is neutral and gets dropped, so it matches the plain title.
NEUTRAL_WORDS = {
    "original", "album", "main", "single", "lp", "full", "length",
    "stereo", "mono",
}

# A version segment ends in one of these generic words. On its own (with only
# neutral words before it) -> neutral; with a custom name before it (e.g.
# "Jim Burgess Mix", "Labor Of Love Mix") -> distinguishing (a named version).
_VERSION_WORDS = {"mix", "version"}

# A Camelot key tag like "8A" / "12B" is a DJ working annotation, not a version.
# Matched on the normalized segment (lowercased, punctuation stripped).
_CAMELOT_RE = re.compile(r"\d{1,2}[ab]")

# A tag containing any of these substrings is treated as distinguishing -- it
# names a specific alternate version, so two tracks that differ here are variants
# rather than exact duplicates. ("mix" is deliberately NOT here: it is too broad
# and "... Mix" neutral tags are already caught by NEUTRAL_MARKERS above.)
DISTINGUISHING_KEYWORDS = {
    "remix", "edit", "extended", "dub", "instrumental", "radio", "club",
    "vocal", "acapella", "a cappella", "vip", "bootleg", "rework", "live",
    "reprise", "interlude", "remaster", "remastered", "rerub", "re rub",
    "inch", "version dub",
}

_BRACKET_RE = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")


def _is_neutral_version_phrase(seg_norm: str) -> bool:
    """True for segments like 'original version' / 'stereo mix' -- only neutral
    words followed by a generic 'version'/'mix'. These don't name a distinct
    version, so they're dropped and the tag matches the plain title."""
    tokens = seg_norm.split()
    if len(tokens) < 2 or tokens[-1] not in _VERSION_WORDS:
        return False
    return all(t in NEUTRAL_WORDS for t in tokens[:-1])


def _classify_marker(seg_norm: str, seg_raw: str) -> str:
    """Classify a single parenthetical/suffix segment.
    Returns 'neutral' (drop it), 'distinguishing' (it's a version signal), or
    'title_part' (keep it in the base -- it identifies the song)."""
    if not seg_norm:
        return "neutral"
    # Camelot key tags ("8A", "12B") are DJ annotations, not versions -> drop.
    if _CAMELOT_RE.fullmatch(seg_norm):
        return "neutral"
    if seg_norm in NEUTRAL_MARKERS:
        return "neutral"
    if _is_neutral_version_phrase(seg_norm):
        return "neutral"
    raw_l = (seg_raw or "").lower()
    if '7"' in raw_l or '12"' in raw_l or "7''" in raw_l or "12''" in raw_l:
        return "distinguishing"
    if any(kw in seg_norm for kw in DISTINGUISHING_KEYWORDS):
        return "distinguishing"
    # A named version: a custom name followed by a generic version word, e.g.
    # "Labor Of Love Mix", "Jim Burgess Mix", "Female Version". These identify a
    # specific alternate take, so they're distinguishing (the plain neutral
    # phrases above were already filtered out).
    if seg_norm.split()[-1] in _VERSION_WORDS:
        return "distinguishing"
    return "title_part"


def split_title(title: str | None) -> tuple[str, frozenset]:
    """Split a raw title into (base, distinguishing_markers).

    base -- the normalized core used for fuzzy matching, with neutral version
    tags removed but identity-bearing parentheticals kept.
    markers -- a frozenset of normalized distinguishing version tags. Two tracks
    with equal marker sets are candidate exact dupes; differing sets mean they
    are different versions.

    Deterministic: the same title always yields the same (base, markers)."""
    if not title:
        return "", frozenset()

    markers: set[str] = set()
    base_parts: list[str] = []

    # 1. Bracketed segments: (), [], {}. Classify each; keep the surrounding text.
    leading: list[str] = []
    last = 0
    raw = title.strip()
    for m in _BRACKET_RE.finditer(raw):
        leading.append(raw[last:m.start()])
        seg_raw = m.group(1)
        seg_norm = normalize(seg_raw, strip_feat=False)
        cls = _classify_marker(seg_norm, seg_raw)
        if cls == "distinguishing":
            markers.add(seg_norm)
        elif cls == "title_part" and seg_norm and seg_norm not in base_parts:
            # Dedupe repeated identity tags, e.g. "(Love Break) (Love Break)",
            # so a doubled tag doesn't inflate the base vs the single-tag twin.
            base_parts.append(seg_norm)
        # neutral -> dropped
        last = m.end()
    leading.append(raw[last:])
    main = " ".join(leading)

    # 2. Trailing " - <tag>" suffixes (e.g. "Theme - Extended", "X - 12\" Mix",
    # "... - Female Version - 8A"). Strip them right-to-left: drop neutral tags
    # and Camelot key annotations, pull out distinguishing version tags, and
    # stop at the first identity-bearing segment so the real title is preserved.
    while " - " in main:
        head, _, tail = main.rpartition(" - ")
        tail_norm = normalize(tail, strip_feat=False)
        cls = _classify_marker(tail_norm, tail)
        if cls == "distinguishing":
            markers.add(tail_norm)
            main = head
        elif cls == "neutral" and tail_norm:
            main = head
        else:
            break  # title_part -> leave it (and anything before it) attached

    base_main = normalize(main, strip_feat=False)
    base = _WS_RE.sub(" ", " ".join([base_main, *base_parts])).strip()
    return base, frozenset(markers)


def markers_label(markers: frozenset) -> str:
    """Human label for a marker set: '(Extended Mix)' or 'plain' when empty."""
    if not markers:
        return "plain"
    return " ".join(f"({m})" for m in sorted(markers))


# ---------------------------------------------------------------------------
# Track facts
# ---------------------------------------------------------------------------

def ext_of(folder_path: str | None) -> str:
    if not folder_path or "." not in folder_path:
        return ""
    return folder_path.rsplit(".", 1)[-1].lower()


def is_lossless(folder_path: str | None) -> bool:
    return ext_of(folder_path) in LOSSLESS_EXTS


def artist_name(content) -> str:
    try:
        return content.Artist.Name if content.Artist else ""
    except Exception:
        return ""


def remixer_name(content) -> str:
    """Remixer credit, when present. A populated remixer is itself a strong
    'this is a specific version' signal."""
    for attr in ("Remixer",):
        try:
            v = getattr(content, attr, None)
            if v is None:
                continue
            return v.Name if hasattr(v, "Name") else str(v)
        except Exception:
            continue
    return ""


def key_name(content) -> str:
    try:
        k = getattr(content, "Key", None)
        if k is None:
            return ""
        return k.ScaleName if hasattr(k, "ScaleName") else str(k)
    except Exception:
        return ""


def _int_attr(content, name: str) -> int:
    try:
        return int(getattr(content, name, 0) or 0)
    except Exception:
        return 0


def track_facts(content) -> dict:
    """Flatten a DjmdContent row into the plain dict we pass around and write to
    the manifest. Keeping this in one place means every script describes a track
    identically."""
    fp = content.FolderPath or ""
    title = content.Title or ""
    base, markers = split_title(title)
    remixer = remixer_name(content)
    # A remixer credit is a distinguishing version signal even if the title
    # doesn't spell it out -- fold it into the marker set.
    if remixer:
        markers = frozenset(markers | {normalize(remixer, strip_feat=False)})
    # rekordbox stores BPM as an integer x100 (12280 -> 122.8).
    bpm = round(_int_attr(content, "BPM") / 100.0, 2)
    return {
        "content_id": str(content.ID),
        "artist": artist_name(content),
        "title": title,
        "base_title": base,
        "markers": sorted(markers),
        "remixer": remixer,
        "bitrate": int(content.BitRate or 0),
        "ext": ext_of(fp),
        "lossless": is_lossless(fp),
        "file_size": int(content.FileSize or 0),
        "length": _int_attr(content, "Length"),   # seconds
        "bpm": bpm,
        "key": key_name(content),
        "path": fp,
    }


def effective_artist(track: dict) -> str:
    """Artist to match on. When the artist tag is blank but the title looks like
    'Artist - Title', fall back to the leading segment so blank-artist rows don't
    silently fail to cluster."""
    a = track.get("artist") or ""
    if a:
        return a
    t = track.get("title") or ""
    if " - " in t:
        return t.split(" - ", 1)[0].strip()
    return ""


def rank_key(track: dict) -> tuple:
    """Sort key for picking the winner within a duplicate group. Higher is
    better. The trailing content_id makes the order *total* -- if two files tie
    on quality, the winner is still decided the same way on every run, so the
    manifest is reproducible. We negate content_id so 'higher tuple = winner'
    consistently prefers the lower id as the final, arbitrary tiebreak."""
    return (
        1 if track["lossless"] else 0,
        track["bitrate"],
        track["file_size"],
        -int(track["content_id"]),
    )


# ---------------------------------------------------------------------------
# Playlist path resolution
# ---------------------------------------------------------------------------

def build_playlist_index(db):
    """Return {id: playlist_row} for every playlist/folder node."""
    return {str(p.ID): p for p in db.get_playlist()}


def playlist_path(index: dict, playlist_id: str) -> str:
    """Full folder path like 'ML / ML - Classics / ML - Disco'. Walks ParentID
    up to the root. Cached per call via a local dict isn't needed -- libraries
    are small."""
    parts = []
    pid = str(playlist_id)
    seen = set()
    while pid and pid != "root" and pid not in seen:
        seen.add(pid)
        node = index.get(pid)
        if node is None:
            break
        parts.insert(0, node.Name)
        pid = str(node.ParentID)
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# Action builder (shared by detect_duplicates.py and decide.py)
# ---------------------------------------------------------------------------
# Turning a decided group into concrete add/remove operations lives here so the
# initial detection and any later re-decision (decide.py) produce identical
# actions for identical inputs. `scope` controls reach:
#   "target_only" -- only remove the loser from the playlist being deduped; make
#                     no cross-playlist edits (the winner already lives there, so
#                     no add is needed). This is the default.
#   "everywhere"  -- remove the loser from every non-protected playlist it is in,
#                     and add the winner wherever it is missing.

def build_group_actions(winner: dict, losers: list[dict], winner_pids: set,
                        target_playlist_id: str, scope: str = "target_only") -> list[dict]:
    """Return the sorted add/remove actions for one decided group. Each member
    (winner and losers) must carry a `memberships` list (see memberships_for).
    `winner_pids` is the set of playlist ids the winner already belongs to."""
    target_pid = str(target_playlist_id)
    actions: list[dict] = []
    add_targets: dict[str, dict] = {}

    for loser in losers:
        for m in loser.get("memberships", []):
            if m.get("excluded"):
                continue  # protected playlist (e.g. a Backup) -- never touch it
            if scope == "target_only" and m["playlist_id"] != target_pid:
                continue  # this-playlist-only: leave other playlists alone
            actions.append({
                "type": "remove",
                "playlist_id": m["playlist_id"],
                "playlist_name": m["playlist_name"],
                "playlist_path": m["playlist_path"],
                "song_id": m["song_id"],
                "content_id": loser["content_id"],
            })
            pid = m["playlist_id"]
            if pid not in winner_pids:
                # Drop the winner in at the loser's old position; earliest wins.
                if pid not in add_targets or m["track_no"] < add_targets[pid]["track_no"]:
                    add_targets[pid] = {
                        "playlist_id": pid,
                        "playlist_name": m["playlist_name"],
                        "playlist_path": m["playlist_path"],
                        "track_no": m["track_no"],
                    }

    for pid, t in add_targets.items():
        actions.append({
            "type": "add",
            "playlist_id": pid,
            "playlist_name": t["playlist_name"],
            "playlist_path": t["playlist_path"],
            "content_id": winner["content_id"],
            "track_no": t["track_no"],
        })

    # Stable order: by playlist path, adds before removes within a playlist so
    # the winner exists before we (separately) drop losers.
    actions.sort(key=lambda a: (a["playlist_path"].lower(),
                                0 if a["type"] == "add" else 1,
                                a.get("content_id", "")))
    return actions
