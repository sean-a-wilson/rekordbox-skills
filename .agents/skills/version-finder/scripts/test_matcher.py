#!/usr/bin/env python3
"""Dependency-free checks for the version matcher (no pytest, no database).

Synthetic tracks fed through the real find_versions / rb_common functions,
pinning down the version-finder contract:
  * every distinct version of one song matches (Original, Extended, Dub, Remix);
  * a different song does NOT match (no row is invented);
  * an embedded / blank Artist title still matches on its clean base;
  * a remix with an empty Artist field is not dropped by the soft artist gate;
  * a Comments token ([edit of: ...]) pulls in a renamed edit with no shared
    title text, even amid other comment text (MIK energy/key);
  * two files of the same version but different format/bitrate stay separate rows,
    while byte-identical true dupes collapse.

Run after touching the matcher:

    python3 test_matcher.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rb_common import normalize, split_title  # noqa: E402
from find_versions import (  # noqa: E402
    clean_base,
    collapse_key,
    parse_query,
    parse_tokens,
    relationship_label,
    title_match,
    token_hits_query,
    version_label,
)

_failures: list[str] = []
TITLE_THR = 0.87
ARTIST_THR = 0.80


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)


def mk(cid: str, artist: str, title: str, length: int = 0, bpm: float = 0.0,
       bitrate: int = 320, ext: str = "mp3", remixer: str = "", comment: str = "") -> dict:
    base, markers = split_title(title)
    if remixer:
        markers = frozenset(markers | {normalize(remixer, strip_feat=False)})
    lossless = ext in {"wav", "aiff", "aif", "flac", "alac"}
    return {
        "content_id": cid,
        "artist": artist,
        "title": title,
        "base_title": base,
        "markers": sorted(markers),
        "length": length,
        "bpm": bpm,
        "bitrate": bitrate,
        "ext": ext,
        "lossless": lossless,
        "file_size": bitrate * (length or 1),
        "comment": comment,
    }


def matches(track, artist_q, title_q) -> bool:
    return title_match(track, artist_q, title_q, TITLE_THR, ARTIST_THR) is not None


# ---------------------------------------------------------------------------

def test_every_version_matches() -> None:
    aq, tq = parse_query("First Choice - Let No Man Put Asunder")
    original = mk("1", "First Choice", "Let No Man Put Asunder")
    extended = mk("2", "First Choice", "Let No Man Put Asunder (Extended Mix)")
    dub = mk("3", "First Choice", "Let No Man Put Asunder (Dub)")
    remix = mk("4", "First Choice", "Let No Man Put Asunder (Frankie Knuckles Remix)")
    for t in (original, extended, dub, remix):
        check(matches(t, aq, tq), f"{t['title']!r} should match the query song")
    labels = {version_label(t["markers"]) for t in (original, extended, dub, remix)}
    check(len(labels) == 4, f"each version should get a distinct label, got {labels}")
    check(version_label(original["markers"]) == "Original",
          "the neutral bucket must be labeled 'Original'")


def test_different_song_does_not_match() -> None:
    aq, tq = parse_query("Chic - Le Freak")
    other = mk("1", "Sister Sledge", "Lost In Music")
    check(not matches(other, aq, tq),
          "an unrelated song must not match (no invented row)")


def test_blank_artist_embedded_in_title_matches() -> None:
    aq, tq = parse_query("Sister Sledge - Lost In Music")
    # Artist relationship blank; artist lives in the title's left segment.
    t = mk("1", "", "Sister Sledge - Lost In Music")
    check(clean_base(t) == "lost in music",
          f"clean_base should strip the embedded artist, got {clean_base(t)!r}")
    check(matches(t, aq, tq), "blank-Artist embedded title should still match")


def test_remix_with_empty_artist_not_dropped() -> None:
    aq, tq = parse_query("Sister Sledge - Lost In Music")
    t = mk("1", "", "Sister Sledge - Lost In Music (Dimitri From Paris Remix)")
    check(matches(t, aq, tq),
          "a remix with an empty Artist field must not be dropped by the artist gate")
    check(version_label(t["markers"]) != "Original",
          "the remix should carry a distinguishing version label")


def test_comments_token_pulls_in_renamed_edit() -> None:
    aq, tq = parse_query("Chic - Everybody Dance")
    edit = mk("1", "Mark Knight, Wh0", "Clap Your Hands",
              comment="Energy 7  8A  [edit of: Chic - Everybody Dance]")
    # Title alone is a non-match (zero shared tokens with the query)...
    check(not matches(edit, aq, tq),
          "the renamed edit's title should not match the query on its own")
    # ...but its Comments token names the query song, so it joins.
    toks = parse_tokens(edit["comment"])
    check(token_hits_query(toks, aq, tq, TITLE_THR),
          "the [edit of:] token should pull the renamed edit into the result")
    check(relationship_label(toks) == "edit of Chic - Everybody Dance",
          f"relationship label wrong: {relationship_label(toks)!r}")


def test_token_ignored_when_unrelated() -> None:
    aq, tq = parse_query("Chic - Everybody Dance")
    toks = parse_tokens("[sample: The Whispers - Headlights]")
    check(not token_hits_query(toks, aq, tq, TITLE_THR),
          "a token naming a different song must not pull the track in")


def test_format_bitrate_keep_files_separate() -> None:
    wav = mk("1", "First Choice", "Let No Man Put Asunder (Extended Mix)",
             length=400, bitrate=1411, ext="wav")
    mp3 = mk("2", "First Choice", "Let No Man Put Asunder (Extended Mix)",
             length=400, bitrate=320, ext="mp3")
    check(collapse_key(wav) != collapse_key(mp3),
          "same version in different format/bitrate must stay separate rows")


def test_true_dupes_collapse() -> None:
    a = mk("1", "First Choice", "Let No Man Put Asunder",
           length=400, bitrate=1411, ext="wav")
    b = mk("2", "First Choice", "Let No Man Put Asunder",
           length=400, bitrate=1411, ext="wav")
    check(collapse_key(a) == collapse_key(b),
          "byte-identical copies (same version+format+bitrate+length) should collapse")


def main() -> int:
    test_every_version_matches()
    test_different_song_does_not_match()
    test_blank_artist_embedded_in_title_matches()
    test_remix_with_empty_artist_not_dropped()
    test_comments_token_pulls_in_renamed_edit()
    test_token_ignored_when_unrelated()
    test_format_bitrate_keep_files_separate()
    test_true_dupes_collapse()

    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("OK: all version-matcher checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
