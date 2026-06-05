---
name: upgrade-finder
description: >-
  Scan a rekordbox playlist for low-bitrate tracks (320 kbps and below) and
  search the entire rekordbox library for a higher-quality file of the SAME
  recording that the user already owns -- lossless beats lossy, then higher
  bitrate. Produces a read-only report table: the song in the playlist with its
  bitrate/format, the better file found with its bitrate/format, and the list of
  playlists that hold the low-quality copy (so they could all be upgraded). It is
  strictly read-only: it takes NO action and never edits the library, so
  rekordbox can stay open. Use this whenever the user wants to find better-quality
  versions they already have, "upgrade my MP3s to FLAC/WAV", check if a playlist's
  tracks have a higher-bitrate copy elsewhere, or asks "is there a better file of
  these songs in my library" -- even phrased loosely like "which of these are low
  quality and do I have something better." This skill only REPORTS upgrades;
  actually swapping files is out of scope (that's a separate, future step).
---

# upgrade-finder

Take a playlist, find every lossy track at **320 kbps or below**, and search the
**whole rekordbox library** for a file of the **same recording** that ranks higher
on quality (lossless beats lossy, then higher bitrate). Output a **read-only
report table** — the user can see exactly which playlist tracks they already own a
better copy of, and where those low-quality copies live.

This is the read-only sibling of `playlist-dedupe`: same encrypted-DB access via
`pyrekordbox`, same version-aware matcher and quality ranking, but it **scans the
whole library instead of one playlist** and **never writes anything**. It only
reports — applying an upgrade (swapping the playlist entry to the better file) is
deliberately out of scope.

## Why it works this way

rekordbox stores its library in an **encrypted** SQLite database (`master.db`);
plain `sqlite3` fails on it, so the scripts use `pyrekordbox`, which finds the
library and key automatically. All matching and quality ranking happens **inside
the Python scripts** so runs are deterministic — you orchestrate and relay.

If `pyrekordbox` is missing, install it once:
`pip3 install pyrekordbox --break-system-packages`

Scripts live in `scripts/` and are run with `python3` from that directory (they
import a shared `rb_common.py`). The matcher and data model are documented in
`references/data-model.md`.

**Everything here is read-only. rekordbox can stay open the whole time.**

## What counts as an "upgrade"

An upgrade is a **higher-quality file of the same recording** the user already
owns. Matching is intentionally strict so the report never suggests a *different
version* as an upgrade:

- **Same recording** — strong artist+title match, an **identical version-marker
  set** (so an `(Extended Mix)` is never offered as an upgrade for the plain mix),
  and a matching duration when both are known. The loose fuzzy rescue used by
  dedupe is *not* accepted here.
- **A real quality jump** — the library file is **lossless while the playlist
  track is lossy** (lossless always wins, regardless of kbps), **or** it has a
  **strictly higher bitrate** in the same lossless-ness. A same-format,
  same-bitrate file that's merely a larger file is *not* reported.

Candidates are the playlist's tracks that are **lossy and ≤ 320 kbps** (lossless
files are already best-quality and are skipped).

## The workflow

### Step 1 — Resolve and CONFIRM the playlist (gate 1)

Playlist names are ambiguous ("CR - Disco" matches many playlists in different
folders). Resolve and show the full folder path so the user picks the exact one:

```
python3 scripts/resolve_playlist.py "<name the user said>"
```

This prints JSON with every match's `id`, full `path`, and `track_count`. Show the
candidates as folder paths and **confirm a single `id` before continuing.**

**For a whole folder** (e.g. "the CR - Disco folder"): resolve the name, identify
its **child playlists** (the entries whose `path` sits under the folder, excluding
`Backup` / `Claude Backups`), then run Step 2 for **each child playlist** and
present the reports together. The script scans one playlist per run by design.

### Step 2 — Scan the library for upgrades (read-only)

```
python3 scripts/find_upgrades.py <confirmed_id> --out /tmp/rb-upgrades.json
```

Read-only: it reads the playlist and the whole library and writes a JSON report
file — it never touches the database. Options:

- `--max-bitrate 320` — the candidate ceiling (default 320). Raise it to also
  consider, say, 320→lossless on tracks just above the line.
- `--title-threshold 0.87` / `--artist-threshold 0.80` — fuzzy match tuning.
- `--exclude-path "Backup"` — terms (comma-separated) that mark a playlist as
  protected; protected playlists are still counted but footnoted, not listed.

### Step 3 — Show the report table

```
python3 scripts/show_upgrades.py /tmp/rb-upgrades.json
```

This prints the table the user wants — **paste it complete** (the footer's
`Shown: N of N` confirms nothing was truncated):

| # | Song (in playlist) | Current (bitrate · format) | Upgrade found (bitrate · format) | Playlists that could be upgraded |

- The **Current** cell is the playlist track's bitrate + format; **Upgrade found**
  is the better file's bitrate + format (with `(+N more)` when several qualify).
- **Playlists that could be upgraded** lists every non-protected playlist that
  holds the low-quality copy (footnoting how many protected/Backup ones also do),
  so the user sees the full reach of swapping in the better file.
- Add `--no-upgrade` to also list the lossy tracks with **no** better copy (the
  "already the best you own" set). `--format tsv` for tab-separated output.

Then summarize for the user: how many of the playlist's lossy tracks have an
upgrade available, and call out anything noteworthy (e.g. a track whose better
copy is spread across many playlists).

## Guardrails

- **Read-only, always.** No script here writes to the database; there is no apply
  step. rekordbox can stay open.
- **Strict matching** — same recording + identical version markers + a genuine
  quality jump. When a match looks questionable, say so; fuzzy matching can still
  mis-pair messy metadata. Raise it with the user rather than implying certainty.
- **This skill only reports.** If the user wants to actually swap files / apply the
  upgrades, that's a separate action this skill does not perform — tell them so
  rather than attempting any edit.
