#!/usr/bin/env python3
"""Render an upgrade report from find_upgrades.py for review (read-only).

The report groups every lossy playlist track that has a higher-quality file in
your library into THREE tables, by how sure the better file is the same song:

  1. Exact upgrades       -- same recording for sure; apply the whole table at once.
  2. Looks like the same  -- almost certainly the same take; review one at a time.
  3. Different versions   -- a higher-quality DIFFERENT version; review one at a time.

Each table lists BOTH files in the pair (the better file kept, the lossy copy it
would replace) with the playlists each lives in, so the cross-playlist reach of a
swap is always visible.

`--no-upgrade` additionally lists the lossy tracks with no better file found.

Usage:
    python3 show_upgrades.py upgrades.json                # the three tables
    python3 show_upgrades.py upgrades.json --no-upgrade   # + the no-better list
    python3 show_upgrades.py upgrades.json --format tsv   # tab-separated
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TYPE_LABELS = {
    "exact": "Exact upgrades",
    "looks_same": "Looks like the same",
    "different_versions": "Different versions",
}
TYPE_ORDER = ["exact", "looks_same", "different_versions"]
HEADERS = ["#", "Keep", "Artist - Title", "Quality (fmt · time · bpm)",
           "Lives in (non-backup playlists)", "Note"]


def _mmss(seconds: int) -> str:
    if not seconds:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _bpm(b) -> str:
    return f"{b:.1f}" if b else "?"


def _quality(t: dict) -> str:
    return (f"{t.get('bitrate', 0)}k {t.get('ext') or '?'} · "
            f"{_mmss(t.get('length', 0))} · {_bpm(t.get('bpm', 0))}")


def _song(t: dict) -> str:
    return f"{t.get('artist', '')} - {t.get('title', '')}"


def upgrade_type(u: dict) -> str:
    mt = u.get("match_type", "exact")
    return mt if mt in TYPE_LABELS else "exact"


def _current_lives(u: dict) -> str:
    paths = [m["playlist_path"] for m in u.get("playlists", [])]
    cell = (f"({len(paths)}) " + "; ".join(paths)) if paths else "(no non-backup playlist)"
    n_prot = len(u.get("protected_playlists", []))
    if n_prot:
        cell += f"  [+{n_prot} protected]"
    return cell


def _upgrade_lives(u: dict) -> str:
    paths = u.get("upgrade_playlists", [])
    return (f"({len(paths)}) " + "; ".join(paths)) if paths else "(library only)"


def _jump(u: dict) -> str:
    cur, up = u["current"], u["upgrade"]
    note = (f"{cur.get('bitrate', 0)}k {cur.get('ext') or '?'} -> "
            f"{up.get('bitrate', 0)}k {up.get('ext') or '?'}")
    if u.get("also_available"):
        note += f"  (+{u['also_available']} more)"
    return note


def _type_rows(report: dict, mt: str):
    """Two rows per upgrade group of kind `mt`: the better file (KEEP) then the
    lossy copy it would replace. The # appears once per group."""
    out = []
    for i, u in enumerate(report.get("upgrades", []), 1):
        if upgrade_type(u) != mt:
            continue
        out.append((str(i), "KEEP", _song(u["upgrade"]), _quality(u["upgrade"]),
                    _upgrade_lives(u), _jump(u)))
        out.append(("", "", _song(u["current"]), _quality(u["current"]),
                    _current_lives(u), u.get("version_note", "") or ""))
    return out


def _print_table(rows_data, fmt: str) -> None:
    if fmt == "tsv":
        print("\t".join(HEADERS))
        for r in rows_data:
            print("\t".join(r))
        return
    print("| " + " | ".join(HEADERS) + " |")
    print("|" + "|".join(["---"] * len(HEADERS)) + "|")
    for r in rows_data:
        cells = [c.replace("|", "\\|") for c in r]
        print("| " + " | ".join(cells) + " |")


def print_report(report: dict, fmt: str, show_no_upgrade: bool) -> None:
    pl = report.get("playlist", {})
    s = report.get("summary", {})
    counts = {t: sum(1 for u in report.get("upgrades", []) if upgrade_type(u) == t)
              for t in TYPE_ORDER}

    print(f"Playlist: {pl.get('path')}")
    print(
        f"{s.get('upgrades_found', 0)} of {s.get('candidates_scanned', 0)} lossy "
        f"tracks (<= {report.get('thresholds', {}).get('max_bitrate', 320)}k) have a "
        f"higher-quality file in your library "
        f"({counts['exact']} exact, {counts['looks_same']} look the same, "
        f"{counts['different_versions']} different versions; "
        f"library {s.get('library_size', 0)} tracks)."
    )

    blurbs = {
        "exact": "Same recording for sure -- a confident upgrade. Apply the whole "
                 "table at once: swap all, or skip all.",
        "looks_same": "Almost certainly the same take (matching length + BPM, just "
                      "tagged/encoded differently). Review one at a time.",
        "different_versions": "A higher-quality DIFFERENT version (different "
                              "length/BPM or distinct tags). Review one at a time; "
                              "skipping is common here.",
    }

    for ti, mt in enumerate(TYPE_ORDER, 1):
        data = _type_rows(report, mt)
        print(f"\n### Table {ti} - {TYPE_LABELS[mt]} ({counts[mt]} upgrade(s))")
        print(blurbs[mt])
        print()
        if not data:
            print("_none_")
            continue
        _print_table(data, fmt)

    print(f"\nShown: {s.get('upgrades_found', 0)} of {s.get('upgrades_found', 0)} "
          f"upgrades (complete -- nothing truncated). Each pair: the kept better "
          f"file (KEEP) and the lossy copy it would replace.")

    if show_no_upgrade:
        nu = report.get("no_upgrade_tracks", [])
        print(f"\nNo better file found ({len(nu)} -- already the best copy you own):")
        for t in nu:
            print(f"  - {t.get('artist', '')} - {t.get('title', '')} "
                  f"[{t.get('bitrate', 0)}k {t.get('ext') or '?'}]")


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
