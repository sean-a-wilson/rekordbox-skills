---
name: upgrade-finder
description: >-
  Scan a rekordbox playlist for low-bitrate tracks (320 kbps and below) and
  search the entire rekordbox library for a higher-quality file of the SAME
  recording that the user already owns -- lossless beats lossy, then higher
  bitrate. Produces a read-only report table: the song in the playlist with its
  bitrate/format, the better file found with its bitrate/format, and the list of
  playlists that hold the low-quality copy (so they could all be upgraded).
  Scanning is read-only -- rekordbox can stay open. There is also an OPTIONAL,
  opt-in apply step that performs the swap (replace the lossy file with the better
  one) the same way playlist-dedupe does: per-song review with a binary scope
  (this playlist only, or everywhere it appears), a permanent DB backup and in-app
  snapshots first, and rekordbox must be closed. Use this whenever the user wants
  to find better-quality versions they already have, "upgrade my MP3s to FLAC/WAV",
  check if a playlist's tracks have a higher-bitrate copy elsewhere, or asks "is
  there a better file of these songs in my library" -- even phrased loosely like
  "which of these are low quality and do I have something better" -- and, when they
  ask, to actually apply those upgrades.
---

# upgrade-finder

Take a playlist, find every lossy track at **320 kbps or below**, and search the
**whole rekordbox library** for a file of the **same recording** that ranks higher
on quality (lossless beats lossy, then higher bitrate). Output a **read-only
report table** — the user can see exactly which playlist tracks they already own a
better copy of, and where those low-quality copies live.

It's a close cousin of `playlist-dedupe`: same encrypted-DB access via
`pyrekordbox`, same version-aware matcher and quality ranking, but it **scans the
whole library instead of one playlist**. Scanning (Steps 1–3) **never writes
anything**, so rekordbox can stay open. Applying an upgrade — swapping the lossy
playlist entry for the better file — is an **optional Step 4** the user opts into;
it reuses the dedupe skill's hardened write pipeline (backup + snapshots +
adds-before-removes) and requires rekordbox to be closed.

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

**Scanning (Steps 1–3) is read-only — rekordbox can stay open.** The optional
apply step (Step 4) is the one exception: it writes, so rekordbox must be closed.

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

**Stop here unless the user asks to apply the upgrades.** Steps 1–3 are the whole
job for "just tell me what I have a better copy of." Only continue to Step 4 when
they explicitly want to perform the swaps.

### Step 4 — Apply the upgrades (OPTIONAL, writes — rekordbox must be CLOSED)

This swaps the lossy file for the better one, exactly like `playlist-dedupe`'s
apply flow: each upgrade is a one-winner (the better file) / one-loser (the lossy
copy) group, reviewed per song, then written by the **same hardened
`apply_changes.py`** the dedupe skill uses (permanent full-DB backup → in-app
playlist snapshots → adds-before-removes → post-write verification + audit log).

**1. Build the apply manifest** from the report:

```
python3 scripts/build_upgrade_manifest.py /tmp/rb-upgrades.json --out /tmp/rb-upgrade-manifest.json
```

Read-only DB access (it gathers each file's playlist memberships). Every upgrade
starts **pending** — nothing can be written until each is decided.

**2. Review each upgrade with the user, one at a time** (the report's `#` matches
the group number). For each, present the swap and the **binary scope choice**:

> `Cerrone - Supernature` — 256k m4a → 1411k aiff. Replace it in **this playlist
> only**, **everywhere it appears** (non-protected), or **skip**?

Record the answer:

```
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 1 --apply                      # this playlist only
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 2 --apply --scope everywhere   # everywhere it appears
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 3 --skip                       # leave as-is
```

The winner is always the better file, so the user only picks scope or skips —
they never name a file. `everywhere` reaches exactly the non-protected playlists
in the report's "Playlists that could be upgraded" column; Backups are never
touched. **Apply refuses while any group is still pending**, so leave nothing
undecided.

**3. Dry run, then write.** With no `--apply`, the script writes nothing and
prints every planned snapshot + ADD/REMOVE — show this to confirm:

```
python3 scripts/apply_changes.py /tmp/rb-upgrade-manifest.json
```

Then, **only with rekordbox closed**, write for real:

```
python3 scripts/apply_changes.py /tmp/rb-upgrade-manifest.json --apply
```

It saves a permanent copy of `master.db` under `rekordbox-db-backups/`, snapshots
every affected playlist under `Claude Backups/`, then swaps in the better files.
Tell the user to reopen rekordbox to see the results.

## Guardrails

- **Read-only by default.** Steps 1–3 never write; rekordbox can stay open. The
  apply step (Step 4) is the single exception and is strictly opt-in — never run
  it unless the user has asked to actually swap files.
- **Apply is gated.** `apply_changes.py` refuses while rekordbox is running and
  while any upgrade is undecided, always takes a permanent full-DB backup and
  in-app snapshots first, adds winners before removing losers (so no track is ever
  lost), and never touches protected (Backup) playlists.
- **Strict matching** — same recording + identical version markers + a genuine
  quality jump. When a match looks questionable, say so; fuzzy matching can still
  mis-pair messy metadata. Raise it with the user rather than implying certainty —
  this matters most before an apply, since a wrong match would swap in the wrong
  file.
