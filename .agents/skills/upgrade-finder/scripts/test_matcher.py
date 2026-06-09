#!/usr/bin/env python3
"""Dependency-free checks for the upgrade matcher (no pytest, no database).

Synthetic tracks fed through the real find_upgrades / rb_common functions,
asserting which library files count as an upgrade for a lossy playlist track and
which of the three tables they land in. Run after touching the matcher:

    python3 test_matcher.py

The cases pin down the contract:
  * a higher-quality file of the SAME recording -> an 'exact' upgrade;
  * a remaster / tag-drift copy at matching length -> a 'looks_same' upgrade;
  * a higher-quality DIFFERENT version -> a 'different_versions' upgrade
    (surfaced for review, not silently dropped);
  * an equal/lower-quality file is never an upgrade;
  * a same-title file by a different artist is not an upgrade.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rb_common import normalize, split_title  # noqa: E402
from find_upgrades import find_best_upgrade, is_quality_upgrade  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)


def mk(cid: str, artist: str, title: str, length: int, bpm: float,
       bitrate: int, ext: str, remixer: str = "") -> dict:
    """Build the subset of a track fact the upgrade matcher reads."""
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
        "file_size": bitrate * length,  # rough proxy; rank uses lossless+bitrate first
    }


def best(cand, library, title_thr=0.87, artist_thr=0.80):
    return find_best_upgrade(cand, library, title_thr, artist_thr)


def test_lossless_beats_mp3() -> None:
    cand = mk("1", "G.Q.", "Disco Nights (Rock Freak)", 351, 122.8, 320, "mp3")
    wav = mk("2", "G.Q.", "Disco Nights (Rock Freak)", 351, 122.8, 1411, "wav")
    b, conf, also, mt = best(cand, [cand, wav])
    check(b is not None and b["content_id"] == "2", "wav should upgrade the 320 mp3")
    check(mt == "exact", f"same recording should be an exact upgrade, got {mt}")
    check(also == 0, f"unexpected extra matches: {also}")


def test_higher_bitrate_mp3() -> None:
    cand = mk("1", "Chic", "Le Freak", 320, 120.0, 192, "mp3")
    better = mk("2", "Chic", "Le Freak", 320, 120.0, 320, "mp3")
    b, _, _, mt = best(cand, [cand, better])
    check(b is not None and b["content_id"] == "2", "320 mp3 should upgrade the 192 mp3")
    check(mt == "exact", f"expected exact, got {mt}")


def test_remaster_is_looks_same() -> None:
    # A remaster tag at matching length/BPM is the same take in better fidelity.
    cand = mk("1", "Patrice Rushen", "Forget Me Nots", 284, 113.8, 256, "m4a")
    flac = mk("2", "Patrice Rushen", "Forget Me Nots (Remastered)", 284, 113.8, 1000, "flac")
    b, _, _, mt = best(cand, [cand, flac])
    check(b is not None and b["content_id"] == "2", "remaster flac should be found")
    check(mt == "looks_same", f"remaster should be looks_same, got {mt}")


def test_different_version_is_surfaced() -> None:
    # A higher-quality DIFFERENT version is now SURFACED (table 3), not dropped.
    cand = mk("1", "D Train", "You're The One For Me", 360, 117.0, 320, "mp3")
    flac_remix = mk("2", "D Train", "You're The One For Me (Extended Mix)",
                    520, 117.0, 1000, "flac")
    b, _, _, mt = best(cand, [cand, flac_remix])
    check(b is not None and b["content_id"] == "2",
          "a higher-quality different version should be surfaced")
    check(mt == "different_versions",
          f"extended-mix flac should be different_versions, got {mt}")


def test_exact_preferred_over_different_version() -> None:
    # When both an exact upgrade and a different-version upgrade exist, the exact
    # one wins the row (most-confident tier).
    cand = mk("1", "First Choice", "Let No Man Put Asunder", 264, 121.0, 256, "m4a")
    exact_wav = mk("2", "First Choice", "Let No Man Put Asunder", 264, 121.0, 1411, "wav")
    other_ver = mk("3", "First Choice", "Let No Man Put Asunder (Shep Pettibone Mix)",
                   481, 121.0, 1411, "wav")
    b, _, _, mt = best(cand, [cand, exact_wav, other_ver])
    check(b is not None and b["content_id"] == "2", "should pick the exact same-recording wav")
    check(mt == "exact", f"exact tier should win, got {mt}")


def test_equal_quality_is_not_upgrade() -> None:
    cand = mk("1", "Chic", "Good Times", 480, 112.0, 320, "mp3")
    same = mk("2", "Chic", "Good Times", 480, 112.0, 320, "mp3")
    b, _, _, _ = best(cand, [cand, same])
    check(b is None, "an equal-quality copy is not an upgrade")


def test_same_bitrate_bigger_file_is_not_upgrade() -> None:
    cand = mk("1", "Sylvester", "You Make Me Feel (Mighty Real)", 394, 124.0, 256, "m4a")
    bigger = mk("2", "Sylvester", "You Make Me Feel (Mighty Real)", 394, 124.0, 256, "m4a")
    bigger["file_size"] = cand["file_size"] * 3
    check(not is_quality_upgrade(cand, bigger),
          "same format+bitrate must not count as a quality upgrade")
    b, _, _, _ = best(cand, [cand, bigger])
    check(b is None, "same 256k m4a should not be reported as an upgrade")


def test_different_artist_is_not_upgrade() -> None:
    cand = mk("1", "Sister Sledge", "Lost In Music", 350, 116.0, 256, "mp3")
    other = mk("2", "Some Cover Band", "Lost In Music", 350, 116.0, 1411, "wav")
    b, _, _, _ = best(cand, [cand, other])
    check(b is None, "same title by a different artist must not match")


def test_picks_best_and_counts_extras() -> None:
    cand = mk("1", "First Choice", "Let No Man Put Asunder", 400, 119.0, 192, "mp3")
    mp3_320 = mk("2", "First Choice", "Let No Man Put Asunder", 400, 119.0, 320, "mp3")
    wav = mk("3", "First Choice", "Let No Man Put Asunder", 400, 119.0, 1411, "wav")
    b, _, also, mt = best(cand, [cand, mp3_320, wav])
    check(b is not None and b["content_id"] == "3", "should pick the lossless over the 320")
    check(mt == "exact", f"expected exact, got {mt}")
    check(also == 1, f"should report 1 other available upgrade, got {also}")


def main() -> int:
    test_lossless_beats_mp3()
    test_higher_bitrate_mp3()
    test_remaster_is_looks_same()
    test_different_version_is_surfaced()
    test_exact_preferred_over_different_version()
    test_equal_quality_is_not_upgrade()
    test_same_bitrate_bigger_file_is_not_upgrade()
    test_different_artist_is_not_upgrade()
    test_picks_best_and_counts_extras()

    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("OK: all upgrade-matcher checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
