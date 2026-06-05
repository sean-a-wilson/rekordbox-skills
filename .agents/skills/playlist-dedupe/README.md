# playlist-dedupe

Find and prune duplicate tracks in a rekordbox playlist (or across your whole
library), keeping the best-quality copy. It's **version-aware**: it tells genuine
alternate versions (extended / dub / remix) apart from true duplicates, and walks
you through every group before changing anything.

## Prerequisites

- rekordbox 6, with a library (`master.db`) on this machine.
- Python 3 and `pyrekordbox`:

  ```sh
  pip3 install pyrekordbox --break-system-packages
  ```

`master.db` is an **encrypted** SQLite database — plain `sqlite3` can't read it.
`pyrekordbox` finds the library and decryption key automatically, so you never
point the skill at a path.

## Using it

Just ask Claude in plain language once the repo is loaded (see the repo root
README for launch instructions):

> "dedupe my 'Disco' playlist"
> "this playlist has a bunch of dupes"

Claude drives the scripts and relays each decision to you. **You can leave
rekordbox open during detection and review — it only needs to be closed when
changes are written.**

## How it works

All the real work (fuzzy matching, quality ranking, cross-playlist mapping)
happens inside deterministic Python scripts; Claude orchestrates and relays. The
flow is a read-only detection pass, a guided review, and a single guarded write:

1. **Resolve the playlist.** Names are ambiguous ("ML - Disco" can match several
   playlists in different folders), so Claude shows you the full folder paths and
   you confirm the exact one.
2. **Detect duplicates (read-only).** Tracks are grouped by version-aware
   artist + title similarity, corroborated by duration and BPM. Results are
   written to a manifest file — the database is never touched.
   - **Neutral tags** like `(Original Mix)` and Camelot keys (`- 8A`) are ignored
     so they match the plain title.
   - **Distinguishing tags** — `(Extended Mix)`, `(Dub)`, `7"`/`12"`, a remixer
     credit, or a named mix like `(Jim Burgess Mix)` — are kept as version markers.
   - Each group is classified **exact** (same recording) or **version_variant**
     (genuinely different takes, or two versions that landed in the same
     playlist). Variants start **pending** and must be ruled on.
3. **Review each group, one at a time.** For every group you decide:
   - **Keep which copy, or keep both?** (Claude recommends the quality winner; you
     confirm or override.)
   - **Replace the loser only in this playlist, or everywhere it appears?**
     (Default is this playlist only.)
4. **Apply — only after every group is decided.** The write is heavily guarded
   (see Safety).

### How the recommended winner is chosen

Per group, the suggested keeper ranks highest on:

1. **Lossless beats lossy** — any wav/aiff/flac/alac beats any mp3/m4a/aac,
   regardless of kbps.
2. **Higher bitrate** among files of the same lossless-ness.
3. **Larger file size**, then a stable tiebreak so the choice is reproducible.

You can always override the recommendation during review.

## Safety

- **Nothing is written until you've reviewed every group** and given the go-ahead.
  A variant left pending makes the apply step refuse to run.
- It **will not write while rekordbox is open.**
- Before any edit, two backups are made:
  1. A **timestamped full copy of `master.db`** in a `rekordbox-db-backups/` folder
     beside your library. **These are kept forever** — copying one back over
     `master.db` (with rekordbox closed) fully reverts a run.
  2. **In-app snapshot playlists** under a `Claude Backups/` folder in rekordbox,
     mirroring the source folder structure, so you have a restore point you can see.
- Playlists whose folder path contains "Backup" are **protected** and never modified.
- Edits are ordered so the worst case leaves duplicates in place, never a gap:
  all winner-adds are committed first, then losers are removed.

## Running the scripts directly (development)

Scripts must be run from the `scripts/` directory (they import a shared
`rb_common.py` by relative import):

```sh
cd scripts
python3 test_matcher.py          # unit-test the matcher, no DB access
python3 resolve_playlist.py "name"
python3 detect_duplicates.py <id> --out /tmp/rb-manifest.json
python3 show_manifest.py /tmp/rb-manifest.json
python3 decide.py /tmp/rb-manifest.json --group N --keep <content_id>
python3 apply_changes.py /tmp/rb-manifest.json --apply
```

See `references/data-model.md` for the rekordbox schema details the scripts rely on.
