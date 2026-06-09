#!/usr/bin/env python3
"""Render a version report from find_versions.py (read-only).

One row per distinct file you own of the queried song:

    Version | Format | Bitrate | BPM | Key | Playlists

A row that came in via -- or carries -- a Comments relationship token is
annotated inline in the Version cell, e.g.
"Clap Your Hands - edit of Chic - Everybody Dance", so renamed edits read clearly
without adding a column the request didn't ask for.

Usage:
    python3 show_versions.py /tmp/rb-versions.json              # the version table
    python3 show_versions.py /tmp/rb-versions.json --format tsv # tab-separated
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DASH = "—"  # em dash


def _bpm(v: dict) -> str:
    b = v.get("bpm") or 0
    return f"{b:g}" if b else DASH


def _key(v: dict) -> str:
    return v.get("key") or DASH


def _bitrate(v: dict) -> str:
    cell = f"{v.get('bitrate', 0)}k"
    if v.get("count", 1) > 1:
        cell += f"  x{v['count']}"
    return cell


def _version(v: dict) -> str:
    rel = v.get("relationship")
    if rel:
        # Renamed edit / sample: show the file's own title + the relationship.
        return f"{v.get('title', '')} {DASH} {rel}"
    return v.get("label", "")


def _playlists(v: dict) -> str:
    paths = v.get("playlists", [])
    cell = "; ".join(paths) if paths else DASH
    n_prot = len(v.get("protected_playlists", []))
    if n_prot:
        cell += f"  [+{n_prot} protected]"
    return cell


def rows(report: dict):
    out = []
    for v in report.get("versions", []):
        out.append((
            _version(v),
            v.get("format", "?"),
            _bitrate(v),
            _bpm(v),
            _key(v),
            _playlists(v),
        ))
    return out


def print_report(report: dict, fmt: str) -> None:
    q = report.get("query", {})
    s = report.get("summary", {})
    data = rows(report)
    headers = ["Version", "Format", "Bitrate", "BPM", "Key", "Playlists"]

    label = q.get("raw") or f"{q.get('artist', '')} - {q.get('title', '')}"
    print(f"Query: {label}")
    if not data:
        print(
            f"No versions of this song found in your library "
            f"(scanned {s.get('library_size', 0)} tracks). Nothing to show."
        )
        return
    print(
        f"{s.get('version_rows', 0)} file(s) across your library "
        f"(scanned {s.get('library_size', 0)} tracks)."
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

    print(f"\nShown: {len(data)} of {len(data)} version rows (complete {DASH} nothing truncated).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a version report for review.")
    ap.add_argument("report", help="Path to the JSON report from find_versions.")
    ap.add_argument("--format", choices=["md", "tsv"], default="md")
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print_report(report, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
