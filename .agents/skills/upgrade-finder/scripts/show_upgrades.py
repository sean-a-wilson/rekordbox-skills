#!/usr/bin/env python3
"""Render an upgrade report from find_upgrades.py for review (read-only).

The default view is the table the user asked for -- one row per playlist track
that has a higher-quality version elsewhere in the library:

    # | Song (in playlist) | Current | Upgrade found | Playlists that could be upgraded

`--no-upgrade` additionally lists the lossy tracks with no better file found
(the "already the best copy you own" set).

Usage:
    python3 show_upgrades.py upgrades.json                # the upgrade table
    python3 show_upgrades.py upgrades.json --no-upgrade   # + the no-better list
    python3 show_upgrades.py upgrades.json --format tsv   # tab-separated
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _mmss(seconds: int) -> str:
    if not seconds:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _quality(t: dict) -> str:
    """e.g. '320k mp3' or '1411k wav'."""
    ext = t.get("ext") or "?"
    return f"{t.get('bitrate', 0)}k {ext}"


def _song(t: dict) -> str:
    return f"{t.get('artist', '')} - {t.get('title', '')} ({_mmss(t.get('length', 0))})"


def _playlists_cell(u: dict) -> str:
    paths = [m["playlist_path"] for m in u.get("playlists", [])]
    cell = "; ".join(paths) if paths else "(none)"
    n_prot = len(u.get("protected_playlists", []))
    if n_prot:
        cell += f"  [+{n_prot} protected]"
    return cell


def _upgrade_cell(u: dict) -> str:
    cell = _quality(u["upgrade"])
    if u.get("also_available"):
        cell += f"  (+{u['also_available']} more)"
    return cell


def rows(report: dict):
    out = []
    for i, u in enumerate(report.get("upgrades", []), 1):
        out.append((
            str(i),
            _song(u["current"]),
            _quality(u["current"]),
            _upgrade_cell(u),
            _playlists_cell(u),
        ))
    return out


def print_report(report: dict, fmt: str, show_no_upgrade: bool) -> None:
    pl = report.get("playlist", {})
    s = report.get("summary", {})
    data = rows(report)
    headers = ["#", "Song (in playlist)", "Current", "Upgrade found",
               "Playlists that could be upgraded"]

    print(f"Playlist: {pl.get('path')}")
    print(
        f"{s.get('upgrades_found', 0)} of {s.get('candidates_scanned', 0)} lossy "
        f"tracks (<= {report.get('thresholds', {}).get('max_bitrate', 320)}k) have a "
        f"higher-quality version in your library "
        f"(library {s.get('library_size', 0)} tracks)."
    )
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

    print(f"\nShown: {len(data)} of {len(data)} upgrade rows (complete -- nothing truncated).")

    if show_no_upgrade:
        nu = report.get("no_upgrade_tracks", [])
        print(f"\nNo better file found ({len(nu)} -- already the best copy you own):")
        for t in nu:
            print(f"  - {t.get('artist', '')} - {t.get('title', '')} [{_quality(t)}]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an upgrade report for review.")
    ap.add_argument("report", help="Path to the JSON report from find_upgrades.")
    ap.add_argument("--format", choices=["md", "tsv"], default="md")
    ap.add_argument("--no-upgrade", action="store_true",
                    help="Also list the lossy tracks with no higher-quality version.")
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print_report(report, args.format, args.no_upgrade)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
