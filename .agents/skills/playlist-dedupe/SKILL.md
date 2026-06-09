---
name: playlist-dedupe
description: >-
  Find and prune duplicate songs in a rekordbox playlist, keeping the
  highest-quality file (lossless wins, then highest bitrate). Version-aware: it
  distinguishes genuine alternate versions (extended/dub/remix) from true dupes
  using title tags plus duration/BPM, and reviews them with you one group at a
  time -- you pick which copy to keep (or keep both) and whether to clean just
  this playlist or replace the loser everywhere it appears. Use this whenever the
  user wants to clean up, dedupe, find duplicates in, or remove repeated/
  lower-quality tracks from a rekordbox playlist or their rekordbox library --
  even if they just say "this playlist has a bunch of dupes" or "I have the same
  song twice in different quality." Reviews every group before changing anything
  and never writes while rekordbox is open.
---

# playlist-dedupe

Detect duplicate songs within a rekordbox playlist, keep the best-quality copy,
and replace each removed duplicate with the kept copy everywhere else it lives.
The user always reviews a complete manifest before any change is written.

## Why it works this way

rekordbox stores playlists in an **encrypted** SQLite database (`master.db`).
Plain `sqlite3` fails on it; the scripts use `pyrekordbox`, which finds the
library and key automatically. All heavy lifting (fuzzy matching, quality
ranking, cross-playlist mapping) happens **inside the Python scripts** so runs
are deterministic and cheap -- you orchestrate and relay, the scripts decide.

If `pyrekordbox` is missing, install it once:
`pip3 install pyrekordbox --break-system-packages`

Scripts live in `scripts/` and are run with `python3` from that directory (they
import a shared `rb_common.py`). Read `references/data-model.md` if you need to
go beyond what the scripts expose.

## The workflow at a glance

1. **Resolve** the playlist and confirm the exact one (gate 1).
2. **Detect** duplicates — read-only — and group the songs.
3. **Review each group, one at a time.** For every duplicate set the user
   chooses: **keep which copy, or keep both**, and **replace the loser only in
   this playlist, or everywhere it appears.** You record each choice with
   `decide.py`. Version variants start **PENDING** and must be decided.
4. **Submit** — only after every group is decided. On submit `apply_changes.py`
   does, in order: **archive the whole database → create backup playlists for
   each affected playlist → update the real playlists.**

Steps 1–3 are read-only, so **rekordbox can stay open during review.** It only
needs to be **closed at submit** (step 4). **Stop and confirm with the user at
each gate** — this is the whole safety model.

Run the steps in order.

### Step 1 — Resolve and CONFIRM the playlist (gate 1)

Playlist names are ambiguous: "ML - Disco" matches seven playlists in different
folders. Never assume. Resolve the name and show the user the full folder path
so they pick the exact one:

```
python3 scripts/resolve_playlist.py "<name the user said>"
```

This prints JSON with every match's `id`, full `path`, and `track_count`. Show
the candidates as folder paths (e.g. `ML / ML - Classics / ML - Disco`) and ask
the user which one. **Do not proceed until they confirm a single `id`.** If the
user already gave an unambiguous full path, you may still run this to get the id.

### Step 2 — Detect duplicates (read-only)

```
python3 scripts/detect_duplicates.py <confirmed_id> --out /tmp/rb-manifest.json
```

This is read-only -- it writes a manifest file, never the database. It groups
tracks by **version-aware** artist+title similarity, corroborated by **track
duration and BPM**. Title parsing:

- **Neutral tags** ("(Original Mix)", "(Original Version)", "(Stereo Mix)") and
  **Camelot key tags** ("- 8A") are ignored so they match the plain title.
- **Distinguishing tags** are kept as version markers: keyword tags like
  "(Extended Mix)" / "(Dub)" / "(Female Vocal)", `7"`/`12"` segments, a remixer
  credit, and **named mixes** ("(Labor Of Love Mix)", "(Jim Burgess Mix)") —
  a custom name ending in *mix*/*version* is a distinct take, not neutral.
- Doubled identity tags ("(Love Break) (Love Break)") are de-duplicated.

Each group is classified:

- **exact** — same recording (same base title, same version tags, matching
  length, strong title match). Pre-decided: `collapse`, recommended winner,
  scope `target_only`.
- **version_variant** — genuinely different versions, **or any two
  versions/remixes/edits that landed in the same playlist**: different tags,
  notably different length, or a fuzzy (loose-rescue) title match. Starts
  **`pending`** so it is always **called out** for you to rule on; apply refuses
  to run while any group is pending. The loose duration+BPM rescue (length ~2s,
  BPM ~1.0) pulls garbled-title same-recordings together for review but never
  auto-collapses them.

The recommended winner per group follows the quality rule (lossless > bitrate >
size). `--title-threshold` / `--artist-threshold` (defaults 0.87 / 0.80) tune
matching; the title threshold applies to the cleaned base title.

A quick self-check of the matcher logic (no database) lives in
`scripts/test_matcher.py` — run `python3 test_matcher.py` after changing it.

### Step 3 — Review each group, one at a time (gate 2)

Start with the overview so you and the user see the whole picture, then walk the
groups. Show the **pending variants first** — the manifest already sorts them to
the top.

```
python3 scripts/show_manifest.py /tmp/rb-manifest.json            # overview table
python3 scripts/show_manifest.py /tmp/rb-manifest.json --group N  # one group's card
```

The overview has columns **# | Conf | Type | Decision | Winner | Loser |
Playlists affected**. **Paste it complete** (the footer's `Shown: N of N` must
match) so the user sees everything.

Then **present each group individually** with its card (`--group N`). The card
shows every copy side by side with **Time / BPM / bitrate / ext**, the
recommended keeper, where each copy currently lives, and the version note. For
each group ask the user the two questions:

1. **Keep which copy, or keep both?** (You recommend the quality winner; they
   confirm or override.)
2. **Replace the loser only in this playlist, or everywhere it appears?**
   *Same song, just higher bitrate →* usually **everywhere**. *A different
   version you only want gone from this playlist →* **this playlist only**
   (the default).

Record each answer immediately with `decide.py` (read-only — edits only the
manifest, never the database):

```
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep <content_id>
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep <content_id> --scope everywhere
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep-both
```

The card prints each copy's `content_id`; pass the keeper's id to `--keep`.

- **Protected (Backup) playlists** — by default, any playlist whose full folder
  path contains "Backup" is never modified (covers a playlist named "… Backup"
  and anything inside a "Backups/" folder). Mention any listed under
  `protected_playlists_skipped` so the user knows the dupe was left in backups.
  Change with `--exclude-path "term1,term2"` (or `--exclude-path ""`).

**Every group must be decided before submit.** Re-run the overview to confirm
`pending` is 0. Then summarize what will change and get the user's go-ahead.

### Step 4 — Submit / apply (only after every group is decided)

First confirm **rekordbox is fully closed** (the script also checks). Preview,
then apply:

```
python3 scripts/apply_changes.py /tmp/rb-manifest.json            # dry run preview
python3 scripts/apply_changes.py /tmp/rb-manifest.json --apply    # write for real
```

`--apply` performs two layers of backup before touching anything, then the edits:
1. **Full DB-file backup** — a complete, timestamped copy of `master.db` written
   into a dedicated **`rekordbox-db-backups/`** folder beside it (e.g.
   `rekordbox-db-backups/master-2026-06-04_150312.db`). **These are kept forever**
   — the skill never prunes them. This is the guaranteed restore point: copying
   one back over `master.db` (with rekordbox closed) fully reverts a run.
2. **In-app snapshots** — for every playlist that will be modified, a copy of its
   current contents is created under a root-level **`Claude Backups`** folder.
   The snapshot **mirrors the source folder structure** so backups are easy to
   navigate: a playlist at `ML / ML - Classics / ML - Disco` is copied to
   `Claude Backups / ML / ML - Classics / ML - Disco - Claude Backup - {YYYY-MM-DD | HH:MM}`.
   Folders are created as needed and reused across runs. This gives the user a
   restore point they can see and re-import inside rekordbox. Protected (Backup)
   playlists are never modified, so they're never snapshotted.
3. **Edits** — the order matters and is deliberate: **all winner-adds are
   committed first, then losers are removed.** By committing the adds first, a
   failure during the add phase rolls back cleanly and aborts **before** any
   removal — the worst case leaves duplicates in place, never a gap. Adds are
   idempotent (a winner already present is skipped), so re-running after a
   partial failure is safe. Adds also tolerate playlists whose `Attribute` isn't
   `0` (e.g. `-128`), which `pyrekordbox` would otherwise reject.

   **Removals are sync-safe tombstones, not hard deletes.** Instead of physically
   deleting the loser's playlist row, the skill marks it the way rekordbox itself
   does (`rb_local_deleted=1`, `rb_data_status=262`, a fresh row USN) and closes
   the resulting `TrackNo` gaps. This matters because a hard-deleted row leaves no
   record for **Cloud Library Sync** to upload, so on the next sync the cloud's
   still-present copy wins and the removal silently reverts. A tombstone uploads
   as a real deletion, so the change sticks. (All read paths ignore tombstoned
   rows, so a removed track never reappears in scans.)

After writing, the script **verifies** the database matches the manifest and, on
any mismatch, prints a loud warning pointing at the full DB backup. It writes
`applied_log.json` listing the DB-file backup, every snapshot playlist, and the
verification result. If **Cloud Library Sync** (e.g. Dropbox) is enabled, it
prints a heads-up: removals upload on the next sync, so reopen rekordbox and let
a sync finish before judging the result. Tell the user to reopen rekordbox to
verify, that the `Claude Backups` folder holds their in-app restore points, and
that `rekordbox-db-backups/` holds the permanent full-database backups.

## Quality ranking (how the winner is recommended)

This decides the **recommended** keeper per group; the user can always override
it during review. The recommended winner is the file that ranks highest on:
1. **Lossless beats lossy** — any wav/aiff/aif/flac/alac beats any mp3/m4a/aac,
   regardless of the kbps number (the user's stated rule). Detected by extension.
2. **Higher bitrate** — among files of the same lossless-ness.
3. **Larger file size**, then a stable id tiebreak so the choice is reproducible.

## Guardrails

- Steps 1–3 never write. Only `apply_changes.py --apply` writes.
- **Every group must be decided before submit.** A `version_variant` left
  `pending` makes apply refuse to run (it lists them). Resolve each with
  `decide.py` — keep one copy, or `--keep-both`.
- The default scope is **this playlist only** (`target_only`): collapsing a dupe
  removes the lower-quality copy from the playlist being deduped and leaves other
  playlists alone. **`everywhere`** is the per-group opt-in for when it's truly
  the same song and the better file should replace it library-wide.
- On submit the write order is fixed and safe: **archive the whole DB →
  create backup playlists → update real playlists** (adds committed before any
  remove). The permanent full-DB backups in `rekordbox-db-backups/` are the
  restore path of last resort — to revert a run, close rekordbox and copy the
  relevant `master-<stamp>.db` back over `master.db`.
- Never write while rekordbox is running.
- Present **every** group before submitting — never silently skip one. Use the
  card (`--group N`) so the user sees Time/BPM/bitrate and can judge versions.
- Fuzzy matching can still mis-group versions — the confidence score, the
  exact/variant classification, and the per-group review exist for this reason.
  When in doubt, raise it with the user.
