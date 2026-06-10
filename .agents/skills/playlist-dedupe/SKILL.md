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

Scripts live in `scripts/` and are run with `python3` (they import shared helpers
from `.agents/shared/`, added to `sys.path` automatically, so the working
directory doesn't matter). Read `references/data-model.md` if you need to go beyond
what the scripts expose.

## The workflow at a glance

1. **Resolve** the playlist and confirm the exact one (gate 1).
2. **Detect** duplicates — read-only — and group the songs into **three tables**:
   **(1) exact dupes**, **(2) looks like the same**, **(3) different versions**.
3. **Review.** **Table 1 is decided in bulk** — every copy is shown, then you ask
   two questions once for the whole table: *replace all the exact dupes?* and
   *replace them everywhere, or only this playlist?* **Tables 2 and 3 are walked
   one at a time** — for each group the user picks **keep which copy / keep both**
   and **this playlist only / everywhere**. You record choices with `decide.py`
   (`--all-exact` for table 1, `--group N` for tables 2 & 3). Tables 2 & 3 start
   **PENDING** and must each be decided.
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

Each group is classified into one of **three kinds** (the three review tables):

- **exact** — same recording for sure (same base title, same version tags,
  matching length, strong title match). The keeper is pre-decided (`collapse`,
  recommended winner), but the **scope is left UNSET** — it is never assumed.
  These are decided **in bulk** (decide.py `--all-exact`); apply refuses while any
  scope is unset.
- **looks_same** — not provably identical, but **no conflicting version tags** and
  the **durations + BPM line up tightly** (within ~8% length and ~1.5 BPM), so it
  is almost certainly the same take — a remaster, neutral tag drift, a quality
  upgrade, or a loose-rescued garbled title. Starts **`pending`**; reviewed one at
  a time, never auto-collapsed.
- **different_versions** — genuinely different takes: **two distinct, non-empty
  version tags** (e.g. "(Remix)" vs "(Labor Of Love Mix)" — a *remaster* tag does
  not count, it is the same recording), **or** a notably different length/BPM.
  Starts **`pending`**; reviewed one at a time (keep-both is common here).

Apply refuses to run while any group is `pending` or has an unset scope. The loose
duration+BPM rescue (length ~2s, BPM ~1.0) pulls garbled-title same-recordings
together for review (as `looks_same`) but never auto-collapses them.

The recommended winner per group follows the quality rule (lossless > bitrate >
size). `--title-threshold` / `--artist-threshold` (defaults 0.87 / 0.80) tune
matching; the title threshold applies to the cleaned base title.

A quick self-check of the matcher logic (no database) lives in
`scripts/test_matcher.py` — run `python3 test_matcher.py` after changing it.

### Step 3 — Review the three tables (gate 2)

Start with the overview so you and the user see the whole picture. It prints
**three tables**, one per group kind, each listing **every copy** with its
quality and the **playlists it lives in**:

```
python3 scripts/show_manifest.py /tmp/rb-manifest.json             # the three tables
python3 scripts/show_manifest.py /tmp/rb-manifest.json --group N   # one group's card
python3 scripts/show_manifest.py /tmp/rb-manifest.json --protected # list skipped Backup playlists
```

Each table has columns **# | Keep | Artist - Title | Quality (fmt · time · bpm) |
Lives in (non-backup playlists) | Note**. There is one row **per copy** so the
user sees every file (including the keeper); the `Lives in` cell lists every
non-backup playlist that copy sits in (with a count), so the cross-playlist
spread is always visible. **Lead the review by pasting all three tables complete**
(the footer's `Shown: N of N` must match) — it is the first thing the user should
see. Protected (Backup) playlists stay collapsed to a one-line count; expand with
`--protected` only if the user asks.

**Table 1 — Exact dupes: decide in bulk.** Show the table, then ask the user the
two questions **once for the whole table**:

1. **Replace all the exact dupes?** (keep each group's quality winner) — or keep
   them all.
2. **Everywhere, or this playlist only?** **Always ask — there is no default.**

Record the answer once with `--all-exact`:

```
python3 scripts/decide.py /tmp/rb-manifest.json --all-exact --scope everywhere    # replace all, every non-backup playlist
python3 scripts/decide.py /tmp/rb-manifest.json --all-exact --scope target-only   # replace all, this playlist only
python3 scripts/decide.py /tmp/rb-manifest.json --all-exact --keep-both           # keep all (no change for table 1)
```

**Tables 2 & 3 — Looks-same and Different-versions: walk one at a time.** Present
each group with its card (`--group N`). The card shows every copy side by side
with **Time / BPM / bitrate / ext**, the recommended keeper, where each copy
lives, and the version note. For each group ask the user the two questions:

1. **Keep which copy, or keep both?** (You recommend the quality winner; they
   confirm or override. For *different versions*, keep-both is common.)
2. **Replace the loser everywhere it appears, or only in this playlist?**
   **Always ask this — there is no default.** Use the "Lives in" cell to tell the
   user exactly which playlists hold the copy. *Same song, just higher bitrate →*
   usually **everywhere** (`--scope everywhere`); *a different version you only
   want gone from here →* **this playlist only** (`--scope target-only`).

Record each answer immediately with `decide.py` (read-only — edits only the
manifest, never the database):

```
# --scope is REQUIRED with --keep (no default -- ask the user, then record their choice)
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep <content_id> --scope everywhere     # every non-backup playlist
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep <content_id> --scope target-only   # only this playlist
python3 scripts/decide.py /tmp/rb-manifest.json --group N --keep-both
```

The card prints each copy's `content_id`; pass the keeper's id to `--keep`.

- **Protected (Backup) playlists** — by default, any playlist whose full folder
  path contains "Backup" is never modified (covers a playlist named "… Backup"
  and anything inside a "Backups/" folder). The overview shows only the count;
  expand the list with `--protected` (or read `protected_playlists_skipped`) if
  the user wants to know a dupe was left in backups.
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
- **Every group must be decided before submit.** Tables 2 & 3 (`looks_same`,
  `different_versions`) start `pending` and make apply refuse to run until each is
  resolved with `decide.py --group N` — keep one copy, or `--keep-both`. Table 1
  (`exact`) is decided in bulk with `decide.py --all-exact`.
- **Scope has no default — it is always asked.** Every `collapse` group starts
  with scope **unset** and apply refuses until you explicitly choose — once for
  table 1 (`--all-exact --scope ...`), per group for tables 2 & 3
  (`--group N ... --scope ...`). `everywhere` removes the lower-quality copy from
  **every non-backup playlist it sits in** (and keeps the better file);
  `target_only` removes it only from the playlist being deduped. Always surface
  this choice — each table's "Lives in" cell shows the full footprint, and the
  header shows how many groups still "need scope".
- On submit the write order is fixed and safe: **archive the whole DB →
  create backup playlists → update real playlists** (adds committed before any
  remove). The permanent full-DB backups in `rekordbox-db-backups/` are the
  restore path of last resort — to revert a run, close rekordbox and copy the
  relevant `master-<stamp>.db` back over `master.db`.
- Never write while rekordbox is running.
- Present **every** group before submitting — never silently skip one. Paste all
  three tables, and use the card (`--group N`) for tables 2 & 3 so the user sees
  Time/BPM/bitrate and can judge versions.
- Fuzzy matching can still mis-group versions — the confidence score, the
  three-way classification, and the per-group review of tables 2 & 3 exist for
  this reason. A `looks_same` group can still turn out to be a different version
  (and vice-versa); the user's per-group review is the backstop. When in doubt,
  raise it with the user.
