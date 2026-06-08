# upgrade-finder

Scan a rekordbox playlist for low-bitrate tracks (**320 kbps and below**) and
search your **whole library** for a higher-quality file of the *same recording*
you already own — lossless beats lossy, then higher bitrate. It produces a
**read-only report table**: which tracks you have a better copy of, and where the
low-quality copies live. If you want, an **optional apply step** then performs the
swap for you.

It's a close cousin of [`playlist-dedupe`](../playlist-dedupe/): same encrypted-DB
access, the same version-aware matcher and quality ranking, but it searches the
entire library. Scanning never changes anything; the optional apply step reuses
dedupe's hardened write pipeline.

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

> "are there higher-quality versions of the tracks in my 'CR - Disco' playlist?"
> "which of these MP3s do I have a FLAC or WAV of?"
> "find upgrades for my Disco playlist"

Claude resolves the playlist, runs the scan, and shows you the report. **Because
nothing is ever written, you can leave rekordbox open.**

## What you get

A table, one row per playlist track that has a better copy elsewhere:

| # | Song (in playlist) | Current | Upgrade found | Playlists that could be upgraded |
|---|---|---|---|---|
| 1 | G.Q. - Disco Nights (Rock Freak) | 256k m4a | 1411k aiff | CR - Disco - All; CR - Disco - Cruising  [+21 protected] |

- **Current** — the playlist track's bitrate + format.
- **Upgrade found** — the best higher-quality file of the same recording in your
  library (`(+N more)` when several qualify).
- **Playlists that could be upgraded** — every (non-protected) playlist holding the
  low-quality copy, so you see the full reach of swapping in the better file.

Ask for the `--no-upgrade` view to also list the lossy tracks that have **no**
better copy (already the best you own).

## Applying the upgrades (optional)

If you want to actually swap the lossy files for the better ones, just ask — e.g.
*"go ahead and apply these"*. Claude reviews each upgrade with you one at a time
and, like `playlist-dedupe`, asks **where** to replace each: **this playlist
only** or **everywhere it appears** (your Backup playlists are never touched). You
can skip any you don't want.

Before writing a single change it takes a **permanent copy of your database** and
makes **in-app snapshot playlists** under `Claude Backups/`, and it adds the
better file before removing the old one so a track can never be lost. **Close
rekordbox first** — the apply step won't run while it's open.

## What counts as an upgrade

A **higher-quality file of the same recording**, matched strictly so a *different
version* is never suggested:

- **Same recording** — strong artist + title match, an **identical version-marker
  set** (an `(Extended Mix)` is never offered for the plain mix), matching duration
  when known.
- **A real quality jump** — lossless when the playlist track is lossy (lossless
  always wins, regardless of kbps), or a strictly higher bitrate. A same-format,
  same-bitrate file that's merely larger is not reported.

Candidates are the playlist's **lossy tracks at or below 320 kbps**; lossless files
are already best-quality and skipped.

## Safety

- **Read-only by default.** Scanning never writes to the database and works fine
  with rekordbox open. The apply step is the only thing that writes, and only when
  you ask for it.
- **The apply step is heavily guarded.** It refuses while rekordbox is running,
  refuses until every upgrade has been decided, always makes a permanent full-DB
  backup and in-app snapshots first, adds the better file before removing the old
  one, verifies the result, and never touches Backup playlists.
- Fuzzy matching can still mis-pair messy metadata, so treat the report as a
  high-confidence shortlist, not gospel — spot-check anything surprising,
  especially before applying.

## Running the scripts directly (development)

Run from the `scripts/` directory (they import a shared `rb_common.py` by relative
import):

```sh
cd scripts
python3 test_matcher.py                                  # unit-test the matcher, no DB access
python3 resolve_playlist.py "name"
python3 find_upgrades.py <id> --out /tmp/rb-upgrades.json
python3 show_upgrades.py /tmp/rb-upgrades.json
python3 show_upgrades.py /tmp/rb-upgrades.json --no-upgrade

# optional apply pipeline (writes only with --apply, and only when rekordbox is closed)
python3 build_upgrade_manifest.py /tmp/rb-upgrades.json --out /tmp/rb-upgrade-manifest.json
python3 decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 1 --apply [--scope everywhere]
python3 decide_upgrade.py /tmp/rb-upgrade-manifest.json --group 3 --skip
python3 apply_changes.py /tmp/rb-upgrade-manifest.json            # dry run
python3 apply_changes.py /tmp/rb-upgrade-manifest.json --apply    # write (rekordbox closed)
```

See `references/data-model.md` for the rekordbox schema details the scripts rely on.
