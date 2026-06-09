#!/usr/bin/env python3
"""Dependency-free checks for the version-aware matcher.

No pytest, no database -- just synthetic titles/tracks fed through the real
rb_common / detect_duplicates functions, asserting the verdicts. Run it after
touching the matcher logic:

    python3 test_matcher.py

Exits 0 when every check passes, 1 (with a list of failures) otherwise. The
cases mirror real misses found in the user's library, split into the three group
kinds the reviewer sees:
  * true duplicates that must collapse (exact),
  * copies that look like the same recording -- matching length + BPM, no
    conflicting version tags (looks_same; reviewed, never auto-collapsed),
  * genuinely different versions -- conflicting tags or divergent length
    (different_versions; reviewed one at a time).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rb_common import normalize, similarity, split_title  # noqa: E402
from detect_duplicates import _pair_matches, classify_group, cluster_duplicates  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)


def mk(artist: str, title: str, length: int, bpm: float, remixer: str = "") -> dict:
    """Build the subset of a track fact the matcher reads, deriving base/markers
    from split_title exactly as track_facts does (remixer folded into markers)."""
    base, markers = split_title(title)
    if remixer:
        markers = frozenset(markers | {normalize(remixer, strip_feat=False)})
    return {
        "artist": artist,
        "title": title,
        "base_title": base,
        "markers": sorted(markers),
        "length": length,
        "bpm": bpm,
    }


def verdict(a: dict, b: dict, title_thr: float = 0.87, artist_thr: float = 0.80):
    """Run the real clustering on a 2-track list and report (matched, type)."""
    matched, _, exact_ok = _pair_matches(a, b, title_thr, artist_thr)
    groups = cluster_duplicates([a, b], title_thr, artist_thr)
    if not groups:
        return matched, None
    g = groups[0]
    match_type, _ = classify_group(g["members"], g.get("exact_eligible", True))
    return matched, match_type


# ---------------------------------------------------------------------------
# split_title blind spots
# ---------------------------------------------------------------------------

def test_split_title() -> None:
    # 1. "(Original Version)" is neutral -> reduces to the plain title.
    base_ov, m_ov = split_title("Ain't That Enough For You (Original Version)")
    base_plain, m_plain = split_title("Ain't That Enough for You")
    check(base_ov == base_plain,
          f"original-version base {base_ov!r} != plain {base_plain!r}")
    check(not m_ov and not m_plain, f"unexpected markers {m_ov} / {m_plain}")

    # 2. Doubled identity tag collapses to one.
    base_dbl, _ = split_title("Ooh I Love It (Love Break) (Love Break)")
    base_one, _ = split_title("Ooh I Love It (Love Break)")
    check(base_dbl == base_one,
          f"doubled base {base_dbl!r} != single {base_one!r}")
    check("love break" in base_dbl, f"identity tag lost: {base_dbl!r}")

    # 3. Non-bracket Camelot + version dash suffix: key dropped, version kept.
    base_fv, m_fv = split_title("Is It All Over My Face? - Female Version - 8A")
    check(base_fv == "is it all over my face", f"suffix base wrong: {base_fv!r}")
    check(m_fv == frozenset({"female version"}), f"suffix markers wrong: {m_fv}")

    # 4. Named mix is distinguishing, not baked into the base.
    base_llm, m_llm = split_title("You're The One For Me (Labor Of Love Mix)")
    check(base_llm == "you re the one for me", f"named-mix base wrong: {base_llm!r}")
    check(m_llm == frozenset({"labor of love mix"}), f"named-mix marker wrong: {m_llm}")

    # 5. 12"/7" stay distinguishing even with a "version" word.
    _, m_12 = split_title('Doctor Love (12" Version)')
    check(m_12 and any("12" in x for x in m_12), f'12" not distinguishing: {m_12}')

    # Bracketed Camelot key is dropped too.
    base_k, m_k = split_title("Some Track (8A)")
    check(base_k == "some track" and not m_k, f"bracket key not dropped: {base_k!r}/{m_k}")


# ---------------------------------------------------------------------------
# True duplicates -> exact (collapse)
# ---------------------------------------------------------------------------

def test_true_dupes_exact() -> None:
    art = "John Davis & The Monster Orchestra"
    a = mk(art, "Ain't That Enough For You (Original Version)", 537, 120.0)
    b = mk(art, "Ain't That Enough for You", 536, 120.0)
    matched, typ = verdict(a, b)
    check(matched, "original-version dupe did not match")
    check(typ == "exact", f"original-version dupe not exact: {typ}")

    art2 = "The Salsoul Orchestra"
    a2 = mk(art2, "Ooh I Love It (Love Break) (Love Break)", 300, 118.0)
    b2 = mk(art2, "Ooh I Love It (Love Break)", 300, 118.0)
    matched2, typ2 = verdict(a2, b2)
    check(matched2, "doubled-tag dupe did not match")
    check(typ2 == "exact", f"doubled-tag dupe not exact: {typ2}")


# ---------------------------------------------------------------------------
# Conflicting version tags or divergent length -> different_versions (called out)
# ---------------------------------------------------------------------------

def test_different_versions_called_out() -> None:
    cases = [
        # Two different, non-empty hard tags -> different versions even at a close
        # length.
        ("D-Train", "You're The One For Me (Remix)", 360, 117.0, "",
         "You're The One For Me (Labor Of Love Mix)", 380, 117.0, ""),
        # Plain vs a named mix, with a big length gap.
        ("Change", "A Lover's Holiday", 237, 120.0, "",
         "A Lover's Holiday (Jim Burgess Mix)", 384, 120.0, ""),
        ("First Choice", 'Doctor Love (Tom Moulton 12" Mix)', 400, 120.0, "",
         'Doctor Love (Shep Pettibone Special 12" Mix)', 410, 120.0, ""),
        # Same artist field; the Joey Negro version differs by mix + remixer.
        ("Ashford & Simpson", 'Found A Cure (12" Disco Mix)', 390, 121.0, "",
         "Found A Cure (Joey Negro Dub Mix)", 395, 121.0, "Joey Negro"),
        ("Loose Joints", "Is It All Over My Face? (Female Vocal)", 360, 122.0, "",
         "Is It All Over My Face? - Female Version - 8A", 361, 122.0, ""),
    ]
    for art, t1, l1, b1, r1, t2, l2, b2, r2 in cases:
        a = mk(art, t1, l1, b1, r1)
        c = mk(art, t2, l2, b2, r2)
        matched, typ = verdict(a, c)
        check(matched, f"{art} '{t1}' vs '{t2}' did not cluster (not called out)")
        check(typ == "different_versions",
              f"{art} '{t1}' vs '{t2}' classified {typ}, expected different_versions")


# ---------------------------------------------------------------------------
# Same recording, tagged/encoded differently -> looks_same (reviewed, not exact)
# ---------------------------------------------------------------------------

def test_looks_same_called_out() -> None:
    cases = [
        # One hard tag vs plain, identical length + BPM -> the same take.
        ("Chic", 'Chic Cheer (12" Mix)', 283, 113.0, "",
         "Chic Cheer (2006 Remaster)", 283, 113.0, ""),
        # Remaster tag is soft: same recording, better fidelity.
        ("Patrice Rushen", "Forget Me Nots (Remastered)", 284, 113.8, "",
         "Forget Me Nots", 275, 114.7, ""),
        # Neutral tag drift only, tiny length wobble -> same.
        ("Grace Jones", "Pull Up To The Bumper (Original Mix)", 273, 109.3, "",
         "Pull Up to the Bumper", 282, 109.1, ""),
    ]
    for art, t1, l1, b1, r1, t2, l2, b2, r2 in cases:
        a = mk(art, t1, l1, b1, r1)
        c = mk(art, t2, l2, b2, r2)
        matched, typ = verdict(a, c)
        check(matched, f"{art} '{t1}' vs '{t2}' did not cluster")
        check(typ == "looks_same",
              f"{art} '{t1}' vs '{t2}' classified {typ}, expected looks_same")


# ---------------------------------------------------------------------------
# Safety: the loose Tier C rescue surfaces for review, never auto-collapses;
# and genuinely unrelated tracks still don't match.
# ---------------------------------------------------------------------------

def test_rescue_safety() -> None:
    # Two near-identical recordings with a slightly-off (typo'd) plain title and
    # identical length+BPM. Derive a threshold that puts the title score inside
    # the loose band so the pair is rescued but must be review-only (variant).
    a = mk("Artist X", "Some Long Song Title", 300, 120.0)
    b = mk("Artist X", "Some Long Song Titel", 300, 120.0)
    ts = similarity(a["base_title"], b["base_title"])
    thr = ts + 0.15  # ts lands in [thr-0.20, thr-0.10): loose-only rescue
    matched, _, exact_ok = _pair_matches(a, b, thr, 0.80)
    check(matched, "loose-band pair should be rescued for review")
    check(not exact_ok, "loose-band pair must not be exact-eligible")
    groups = cluster_duplicates([a, b], thr, 0.80)
    check(bool(groups), "loose-band pair should form a group")
    if groups:
        typ, _ = classify_group(groups[0]["members"], groups[0]["exact_eligible"])
        # Identical length + BPM, no conflicting tags -> looks_same (never exact).
        check(typ == "looks_same",
              f"loose-rescued group must be looks_same, got {typ}")

    # Unrelated songs: dissimilar title, far length/BPM -> no match, no rescue.
    na = mk("Artist X", "Completely Different Song", 200, 100.0)
    nb = mk("Artist X", "Another Unrelated Tune", 400, 130.0)
    matched_n, _, _ = _pair_matches(na, nb, 0.87, 0.80)
    check(not matched_n, "unrelated tracks should not match")


def main() -> int:
    test_split_title()
    test_true_dupes_exact()
    test_different_versions_called_out()
    test_looks_same_called_out()
    test_rescue_safety()

    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("OK: all matcher checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
