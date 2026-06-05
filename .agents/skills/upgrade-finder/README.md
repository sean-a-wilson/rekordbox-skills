# upgrade-finder

Scan a rekordbox playlist for low-bitrate tracks (**320 kbps and below**) and
search your **whole library** for a higher-quality file of the *same recording*
you already own — lossless beats lossy, then higher bitrate. It produces a
**read-only report table** and **takes no action**: it just tells you which tracks
you have a better copy of, and where the low-quality copies live.

It's the read-only sibling of [`playlist-dedupe`](../playlist-dedupe/): same
encrypted-DB access, the same version-aware matcher and quality ranking, but it
searches the entire library and never changes anything.

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

- **Strictly read-only.** No script here writes to the database; there is no apply
  step. It cannot modify or delete anything, and works fine with rekordbox open.
- **It only reports.** Actually swapping a playlist entry to the better file is
  *out of scope* for this skill — that's a separate, future step.
- Fuzzy matching can still mis-pair messy metadata, so treat the report as a
  high-confidence shortlist, not gospel — spot-check anything surprising.

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
```

See `references/data-model.md` for the rekordbox schema details the scripts rely on.
