---
name: upgrade-finder
description: >-
  Scan a rekordbox playlist for low-bitrate tracks (320 kbps and below) and
  search the entire rekordbox library for a higher-quality file the user already
  owns -- lossless beats lossy, then higher bitrate. Produces a read-only report
  in THREE tables by how sure the better file is the same song: (1) exact
  upgrades, (2) looks like the same, (3) higher-quality different versions. Each
  row shows both files with their bitrate/format and the playlists each lives in.
  Scanning is read-only -- rekordbox can stay open. There is also an OPTIONAL,
  opt-in apply step that performs the swap (replace the lossy file with the better
  one) the same way playlist-dedupe does: table 1 (exact) decided in bulk, tables
  2 and 3 reviewed one at a time, an always-asked binary scope (everywhere it
  appears, or this playlist only -- no default), a permanent DB backup and in-app
  snapshots first, and rekordbox must be closed. Use this whenever the user wants
  to find better-quality versions they already have, "upgrade my MP3s to FLAC/WAV",
  check if a playlist's tracks have a higher-bitrate copy elsewhere, or asks "is
  there a better file of these songs in my library" -- even phrased loosely like
  "which of these are low quality and do I have something better" -- and, when they
  ask, to actually apply those upgrades.
---

# upgrade-finder

Take a playlist, find every lossy track at **320 kbps or below**, and search the
**whole rekordbox library** for a higher-quality file (lossless beats lossy, then
higher bitrate) of the same song. Output a **read-only report in three tables** —
**exact upgrades**, **looks like the same**, and **higher-quality different
versions** — so the user sees exactly which playlist tracks they already own a
better copy of, how confident the match is, and where those copies live.

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

An upgrade is a **higher-quality file** the user already owns of the same song.
Every match is sorted into one of **three tables** by how sure it is the same
recording, so the user can act confidently on the safe ones and review the rest:

- **exact** — same recording for sure: strong artist+title match, an **identical
  version-marker set** (so an `(Extended Mix)` is not treated as the plain mix),
  and a matching duration. Confident upgrades, decided **in bulk**.
- **looks_same** — no conflicting version tags and the **durations + BPM line up
  tightly** (within ~8% length and ~1.5 BPM): almost certainly the same take — a
  remaster, neutral tag drift, or a quality re-encode. Reviewed one at a time.
- **different_versions** — a higher-quality file that is a **genuinely different
  version**: conflicting version tags (e.g. an Extended/Dub/named mix — a
  *remaster* does not count, it's the same recording) or a notably different
  length/BPM. Reviewed one at a time; **skipping is common** here.

A **real quality jump** is required for any of them: the library file is
**lossless while the playlist track is lossy** (lossless always wins, regardless
of kbps), **or** it has a **strictly higher bitrate** at the same lossless-ness. A
same-format, same-bitrate file that's merely larger is never reported.

Each candidate is reported once, in the **most-confident table** where a quality
upgrade exists (exact > looks_same > different_versions). Candidates are the
playlist's tracks that are **lossy and ≤ 320 kbps** (lossless files are already
best-quality and are skipped).

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

### Step 3 — Show the three report tables

```
python3 scripts/show_upgrades.py /tmp/rb-upgrades.json
```

This prints **three tables** — **lead the review by pasting all three complete**
(the footer's `Shown: N of N` confirms nothing was truncated). They are the first
thing the user should see; protected (Backup) playlists stay footnoted as a count
so they never bury the tables:

1. **Exact upgrades** — same recording for sure.
2. **Looks like the same** — almost certainly the same take.
3. **Different versions** — a higher-quality *different* version (often skip).

Each table has columns **# | Keep | Artist - Title | Quality (fmt · time · bpm) |
Lives in (non-backup playlists) | Note**, with **two rows per upgrade**: the
better file (marked `KEEP`) then the lossy copy it would replace. The kept file's
`Lives in` shows where you already have it; the lossy copy's `Lives in` is the set
of playlists that could be upgraded (footnoting protected/Backup ones). The
`Note` shows the quality jump (e.g. `256k m4a -> 1411k aiff`) and, for tables 2 &
3, why it isn't an exact match (tag/length differences).

- Add `--no-upgrade` to also list the lossy tracks with **no** better copy (the
  "already the best you own" set). `--format tsv` for tab-separated output.

Then summarize for the user: how many exact / looks-same / different-version
upgrades there are, and call out anything noteworthy (e.g. a different-version
"upgrade" that's really a longer edit they'd want to keep).

**Stop here unless the user asks to apply the upgrades.** Steps 1–3 are the whole
job for "just tell me what I have a better copy of." Only continue to Step 4 when
they explicitly want to perform the swaps.

### Step 4 — Apply the upgrades (OPTIONAL, writes — rekordbox must be CLOSED)

This swaps the lossy file for the better one, exactly like `playlist-dedupe`'s
apply flow: each upgrade is a one-winner (the better file) / one-loser (the lossy
copy) group, written by the **same hardened `apply_changes.py`** the dedupe skill
uses (permanent full-DB backup → in-app playlist snapshots → adds-before-removes →
post-write verification + audit log).

**1. Build the apply manifest** from the report:

```
python3 scripts/build_upgrade_manifest.py /tmp/rb-upgrades.json --out /tmp/rb-upgrade-manifest.json
```

Read-only DB access (it gathers each file's playlist memberships). Group numbers
match the report's tables. Every upgrade starts **pending** — nothing can be
written until each is decided.

**2a. Table 1 (exact) — decide in bulk.** Show the table, then ask the user the
two questions **once for the whole table**: *swap in all the exact upgrades?* and
*everywhere, or this playlist only?* Record with `--all-exact`:

```
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --all-exact --apply --scope everywhere    # swap all, every non-protected playlist
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --all-exact --apply --scope target-only   # swap all, this playlist only
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --all-exact --skip                        # skip all exact upgrades
```

**2b. Tables 2 & 3 — review one at a time** (the report's `#` matches the group
number). For each, present the swap and the **binary scope choice**:

> `Cerrone - Supernature` — 256k m4a → 1411k aiff. Replace it **everywhere it
> appears** (non-protected), in **this playlist only**, or **skip**?

**Always ask the scope — there is no default.** For table 3 (different versions),
**skip is usually right** — the better-quality file is a different take. Record:

```
# --scope is REQUIRED with --apply (no default -- ask the user, then record their choice)
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 15 --apply --scope everywhere    # every non-protected playlist
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 16 --apply --scope target-only   # only this playlist
python3 scripts/decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 20 --skip                        # leave as-is
```

The winner is always the better file, so the user only picks scope or skips —
they never name a file. Scope is never assumed: `everywhere` swaps the lossy file
across every non-protected playlist the copy lives in (the table's "Lives in"
cell shows that full footprint); `target-only` swaps only the scanned playlist.
Backups are never touched. **Apply refuses while any group is pending or has no
scope chosen**, so leave nothing undecided.

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

**Removals are sync-safe tombstones, not hard deletes.** When the lossy loser is
removed, its playlist row is marked the way rekordbox itself marks a deletion
(`rb_local_deleted=1`, `rb_data_status=262`, a fresh row USN) rather than
physically deleted. A hard-deleted row leaves nothing for **Cloud Library Sync**
to upload, so the next sync re-adds it from the cloud and the upgrade silently
reverts; a tombstone uploads as a real deletion and sticks. If Cloud Library Sync
(e.g. Dropbox) is enabled, the script prints a heads-up — reopen rekordbox and
let a sync finish before judging the result. Tell the user to reopen rekordbox to
see the results.

## Guardrails

- **Read-only by default.** Steps 1–3 never write; rekordbox can stay open. The
  apply step (Step 4) is the single exception and is strictly opt-in — never run
  it unless the user has asked to actually swap files.
- **Apply is gated.** `apply_changes.py` refuses while rekordbox is running and
  while any upgrade is undecided, always takes a permanent full-DB backup and
  in-app snapshots first, adds winners before removing losers (so no track is ever
  lost), and never touches protected (Backup) playlists.
- **Confidence is tiered, not strict.** Table 1 (exact) is a sure same-recording
  match and is safe to apply in bulk. Tables 2 & 3 are surfaced on purpose —
  table 3 in particular offers a higher-quality *different version*, which is
  usually a skip. The per-group review of tables 2 & 3 is the backstop: a
  `looks_same` match can still be a different take (and vice-versa), so confirm
  each before applying. A wrong apply swaps in the wrong file — when a match looks
  questionable, say so rather than implying certainty.
