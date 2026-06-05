#!/usr/bin/env python3
"""Render a dedupe manifest for review.

Two views, both read-only:

  * default (overview table) -- one row per loser across all groups, with Type
    and Decision columns. Pending version variants sort to the top. Good for a
    quick scan of everything at once.

  * --group N -- a detail card for a single duplicate group, for the
    one-at-a-time review loop: every copy side by side with Time/BPM/bitrate,
    the match type + version note, the recommended keeper, the current decision,
    and the exact decide.py commands to confirm or change it.

Usage:
    python3 show_manifest.py manifest.json                # overview table
    python3 show_manifest.py manifest.json --group 3      # one group's card
    python3 show_manifest.py manifest.json --format tsv   # tab-separated overview
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _mmss(seconds: int) -> str:
    if not seconds:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _bpm(b) -> str:
    return f"{b:.1f}" if b else "?"


def track_label(t: dict) -> str:
    """e.g. 'G.Q. - Disco Nights (Rock Freak) [wav 1411k · 5:51 · 122.8]'."""
    return (f"{t['artist']} - {t['title']} "
            f"[{t['ext']} {t['bitrate']}k · {_mmss(t.get('length', 0))} "
            f"· {_bpm(t.get('bpm', 0))}]")


def winner_of(group: dict) -> dict:
    wid = group.get("winner_content_id")
    for m in group["members"]:
        if m["content_id"] == wid:
            return m
    return group["members"][0]


def losers_of(group: dict) -> list[dict]:
    wid = group.get("winner_content_id")
    return [m for m in group["members"] if m["content_id"] != wid]


def decision_label(group: dict) -> str:
    d = group.get("decision", "collapse")
    if d == "pending":
        return "PENDING"
    if d == "keep_all":
        return "keep both"
    return f"collapse ({group.get('scope', 'target_only')})"


def affected_playlists(group: dict) -> list[str]:
    """Playlists this group's current decision touches -- exactly the playlists
    in its computed actions. Empty for keep_all/pending (nothing decided yet).
    Keyed by full folder PATH (names are not unique)."""
    return sorted({a["playlist_path"] for a in group.get("actions", [])})


def rows(manifest: dict):
    out = []
    for i, g in enumerate(manifest["groups"], 1):
        winner = track_label(winner_of(g))
        typ = "variant" if g.get("match_type") == "version_variant" else "exact"
        dec = decision_label(g)
        aff = ", ".join(affected_playlists(g)) or "(undecided)"
        for loser in losers_of(g):
            out.append((str(i), f"{g['confidence']}", typ, dec,
                        winner, track_label(loser), aff))
    return out


def print_overview(manifest: dict, fmt: str) -> None:
    pl = manifest.get("playlist", {})
    s = manifest.get("summary", {})
    data = rows(manifest)
    headers = ["#", "Conf", "Type", "Decision", "Winner (kept)", "Loser", "Playlists affected"]

    print(f"Playlist: {pl.get('path')}")
    print(
        f"Groups: {s.get('duplicate_groups')} "
        f"({s.get('version_variant_groups', 0)} variants, {s.get('pending_groups', 0)} pending) | "
        f"Loser tracks: {s.get('losing_tracks')} | "
        f"Removals: {s.get('membership_removals')} | Winner adds: {s.get('winner_adds')} | "
        f"Protected skipped: {s.get('protected_playlists_skipped')}"
    )
    if manifest.get("protected_playlists_skipped"):
        print("Protected (left untouched): " + "; ".join(manifest["protected_playlists_skipped"]))
    print()

    if fmt == "tsv":
        print("\t".join(headers))
        for r in data:
            print("\t".join(r))
    else:
        print("| " + " | ".join(headers) + " |")
        print("|" + "|".join(["---"] * len(headers)) + "|")
        for r in data:
            cells = [c.replace("|", "\\|") for c in r]
            print("| " + " | ".join(cells) + " |")

    print(f"\nShown: {len(data)} of {len(data)} duplicate rows (complete -- nothing truncated).")
    if s.get("pending_groups"):
        print(f"\n{s['pending_groups']} PENDING version-variant group(s) must be decided "
              f"(decide.py) before apply will run.")


def print_card(manifest: dict, n: int) -> None:
    groups = manifest.get("groups", [])
    if not (1 <= n <= len(groups)):
        raise SystemExit(f"--group {n} out of range (1..{len(groups)}).")
    g = groups[n - 1]
    rec = g.get("recommended_winner_content_id")
    wid = g.get("winner_content_id")
    typ = "VERSION VARIANT" if g.get("match_type") == "version_variant" else "exact duplicate"

    print(f"Group {n} of {len(groups)} -- {typ}"
          + (f"  ({g['version_note']})" if g.get("version_note") else ""))
    print(f"Confidence: {g['confidence']}   Decision: {decision_label(g)}")
    print()
    for m in g["members"]:
        tags = []
        if m["content_id"] == rec:
            tags.append("recommended")
        if m["content_id"] == wid and g.get("decision") == "collapse":
            tags.append("KEEPING")
        tag = f"  <- {', '.join(tags)}" if tags else ""
        print(f"  {track_label(m)}{tag}")
        # Show where this copy lives (non-protected playlists), so the
        # scope choice is informed.
        live = sorted({mm["playlist_path"] for mm in m.get("memberships", [])
                       if not mm.get("excluded")})
        if live:
            print(f"        in: {'; '.join(live)}")
    print()

    aff = affected_playlists(g)
    if g.get("decision") == "collapse":
        print(f"Current plan: keep id {wid}, scope {g.get('scope')}.")
        print(f"  Affected playlists: {', '.join(aff) if aff else '(none)'}")
    elif g.get("decision") == "keep_all":
        print("Current plan: keep all copies (no change).")
    else:
        print("Current plan: PENDING -- choose below before apply.")

    print()
    print("To decide this group:")
    ids = [m["content_id"] for m in g["members"]]
    print(f"  Keep one (this playlist only): decide.py <manifest> --group {n} --keep <id>")
    print(f"  Keep one (everywhere):         decide.py <manifest> --group {n} --keep <id> --scope everywhere")
    print(f"  Keep both:                     decide.py <manifest> --group {n} --keep-both")
    print(f"  copy ids: {', '.join(ids)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a dedupe manifest for review.")
    ap.add_argument("manifest", help="Path to manifest.json from detect_duplicates.")
    ap.add_argument("--group", type=int, help="Show a single group's detail card (1-based).")
    ap.add_argument("--format", choices=["md", "tsv"], default="md")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.group is not None:
        print_card(manifest, args.group)
    else:
        print_overview(manifest, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
