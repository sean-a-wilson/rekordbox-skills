#!/usr/bin/env python3
"""Convert audio files between rekordbox/CDJ-compatible formats using macOS's
built-in `afconvert` -- no third-party tools, nothing to install.

Standalone: it reads and writes files only. It never touches the rekordbox
database, so rekordbox can stay open.

Allowed conversions (and only these):
    WAV  -> AIFF   (lossless, no quality loss)
    AIFF -> WAV    (lossless, no quality loss)
    WAV  -> AAC    (lossy, smaller files)
    AIFF -> AAC    (lossy, smaller files)

Two conversions are refused on purpose:
    * lossy -> lossless (e.g. AAC -> WAV/AIFF) -- encoding already discarded
      audio that re-wrapping in a lossless container cannot recover.
    * same format -> same format (WAV->WAV, AIFF->AIFF, AAC->AAC) -- pointless,
      and re-encoding AAC would only degrade it further.

Usage:
    python3 convert.py track.aiff --to wav
    python3 convert.py a.wav b.aiff --to aac
    python3 convert.py /path/to/folder --to aiff            # batch a folder
    python3 convert.py track.wav --to aac --bitrate 256k    # override AAC rate
    python3 convert.py /folder --to wav --dry-run           # preview only

Output: a markdown summary table (or `--format json`), with a footer confirming
how many inputs were processed. Exit 0 if every planned conversion succeeded,
1 if any errored.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Source formats the skill understands.
LOSSLESS_EXTS = {"wav", "aiff", "aif"}
LOSSY_EXTS = {"aac", "m4a"}  # mp3 intentionally omitted -- deferred (would need ffmpeg/lame)
SUPPORTED_SRC = LOSSLESS_EXTS | LOSSY_EXTS

# User-facing target names -> the file extension we actually write.
TARGET_EXT = {"wav": "wav", "aiff": "aiff", "aac": "m4a"}
LOSSLESS_TARGETS = {"wav", "aiff"}

# afconvert format/data tokens per target. Bit depth ({n}) is filled in for the
# lossless PCM targets from the source file; AAC ignores it.
AFCONVERT_FILEFMT = {"wav": "WAVE", "aiff": "AIFF", "aac": "m4af"}


def family(ext: str) -> str:
    """Collapse an extension to its format family (aif==aiff, m4a==aac)."""
    ext = ext.lower().lstrip(".")
    if ext in ("aiff", "aif"):
        return "aiff"
    if ext in ("aac", "m4a"):
        return "aac"
    return ext


def decide_conversion(src_ext: str, target: str) -> str:
    """Return "ok" if src_ext -> target is allowed, else a human reason string.

    This is the whole policy of the skill and the only logic that's unit-tested.
    """
    src_ext = src_ext.lower().lstrip(".")
    if src_ext not in SUPPORTED_SRC:
        return f"unsupported source format .{src_ext}"
    if target not in TARGET_EXT:
        return f"unknown target format '{target}'"

    if family(src_ext) == target:
        return f"already {target} -- same-format conversion is blocked"

    src_lossless = src_ext in LOSSLESS_EXTS
    if not src_lossless and target in LOSSLESS_TARGETS:
        return "refusing lossy -> lossless: encoding can't recover discarded audio"

    return "ok"


def source_bit_depth(path: Path) -> int:
    """Best-effort source PCM bit depth via `afinfo`; defaults to 16 if unknown.

    afinfo's "Data format" line appears in two shapes:
        ... lpcm (0x...) 24-bit big-endian signed integer
        ... Int16, interleaved
    """
    try:
        out = subprocess.run(
            ["afinfo", str(path)], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return 16
    for line in out.splitlines():
        if "Data format" not in line:
            continue
        m = re.search(r"(\d+)-bit", line) or re.search(r"Int(\d+)", line)
        if m:
            depth = int(m.group(1))
            return depth if depth in (16, 24, 32) else 16
    return 16


def data_format(target: str, bit_depth: int) -> str:
    """afconvert -d token for the target (endianness follows the container)."""
    if target == "wav":
        return f"LEI{bit_depth}"
    if target == "aiff":
        return f"BEI{bit_depth}"
    return "aac"


def parse_bitrate(text: str) -> int:
    """'320k' / '320000' / '256K' -> bits per second."""
    t = text.strip().lower().rstrip("bps").strip()
    if t.endswith("k"):
        return int(float(t[:-1]) * 1000)
    return int(t)


def collect_inputs(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Expand the given paths into concrete source files.

    A directory contributes its supported audio files (non-recursive); a file is
    taken as-is. Returns (files, notes) where notes explains anything skipped.
    """
    files: list[Path] = []
    notes: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            found = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower().lstrip(".") in SUPPORTED_SRC
            )
            if not found:
                notes.append(f"{p}: no supported audio files in folder")
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            notes.append(f"{raw}: not found")
    return files, notes


def convert_one(src: Path, target: str, bitrate: int, overwrite: bool,
                dry_run: bool) -> dict:
    """Plan (and unless dry-run, perform) one conversion. Returns a result row."""
    src_ext = src.suffix.lower().lstrip(".")
    verdict = decide_conversion(src_ext, target)
    out = src.with_suffix("." + TARGET_EXT[target])
    row = {"input": str(src), "output": str(out), "status": "", "detail": ""}

    if verdict != "ok":
        row["status"] = "skipped"
        row["detail"] = verdict
        return row

    if out.exists() and not overwrite:
        row["status"] = "skipped"
        row["detail"] = "output exists (use --overwrite)"
        return row

    if dry_run:
        row["status"] = "planned"
        row["detail"] = f"{src_ext} -> {target}"
        return row

    cmd = ["afconvert", "-f", AFCONVERT_FILEFMT[target]]
    if target == "aac":
        cmd += ["-d", "aac", "-b", str(bitrate)]
    else:
        cmd += ["-d", data_format(target, source_bit_depth(src))]
    cmd += [str(src), str(out)]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        row["status"] = "error"
        row["detail"] = msg[-1] if msg else "afconvert failed"
        return row
    row["status"] = "converted"
    row["detail"] = f"{src_ext} -> {target}"
    return row


def render_table(rows: list[dict], notes: list[str]) -> str:
    lines = ["| # | Input | -> | Result |", "|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        result = r["status"] if r["status"] == "converted" else f"{r['status']}: {r['detail']}"
        lines.append(f"| {i} | {Path(r['input']).name} | {Path(r['output']).name} | {result} |")
    out = "\n".join(lines)
    out += f"\n\nProcessed: {len(rows)} of {len(rows)} input file(s)."
    if notes:
        out += "\n\nNotes:\n" + "\n".join(f"  - {n}" for n in notes)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert WAV/AIFF audio to WAV, AIFF, or AAC via macOS afconvert."
    )
    ap.add_argument("paths", nargs="+", help="Input file(s) and/or folder(s) to convert.")
    ap.add_argument("--to", required=True, choices=sorted(TARGET_EXT),
                    help="Target format: wav, aiff, or aac (.m4a).")
    ap.add_argument("--bitrate", default="320k",
                    help="AAC bitrate, e.g. 320k or 256k (default 320k; ignored for wav/aiff).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Overwrite an existing output file instead of skipping it.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without writing any files.")
    ap.add_argument("--format", choices=["text", "json"], default="text",
                    help="Output format (default text).")
    args = ap.parse_args()

    if shutil.which("afconvert") is None:
        raise SystemExit(
            "afconvert not found. It ships with macOS at /usr/bin/afconvert -- "
            "this skill is macOS-only."
        )

    try:
        bitrate = parse_bitrate(args.bitrate)
    except ValueError:
        raise SystemExit(f"invalid --bitrate: {args.bitrate!r} (try 320k or 256k)")

    files, notes = collect_inputs(args.paths)
    rows = [convert_one(f, args.to, bitrate, args.overwrite, args.dry_run) for f in files]

    if args.format == "json":
        json.dump({"target": args.to, "results": rows, "notes": notes},
                  sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(render_table(rows, notes))

    return 1 if any(r["status"] == "error" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
