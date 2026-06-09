# version-finder

Ask *"what versions of this track do I have?"* and get a **read-only table** of
every version, edit, and remix of that song already in your rekordbox library —
the original, the extended mix, the dub, each remix — with the format, bitrate,
BPM, key, and the playlists each file lives in. It groups true versions of the
*same song* together instead of dumping a flat filename list the way rekordbox's
native search does.

It's a close cousin of [`upgrade-finder`](../upgrade-finder/): same encrypted-DB
access, the same version-aware matcher and playlist-path resolver — but it scans
your whole library from a free-text query and **changes nothing**.

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

> "what versions of Sister Sledge - Lost In Music do I have?"
> "what remixes of Chic - Le Freak are in my library?"
> "show me every version of First Choice - Let No Man Put Asunder"
> "do I have the extended mix of …, and which playlists is it in?"

Claude runs the scan and shows you the table. **Because nothing is ever written,
you can leave rekordbox open.**

## What you get

A table, one row per file you own of the queried song:

| Version | Format | Bitrate | BPM | Key | Playlists |
|---|---|---|---|---|---|
| Original | wav | 1411k | 114.8 | 7A | CR - Disco - All; ML - Disco  [+38 protected] |
| Original | mp3 | 320k | 114.8 | 7A | CR - Disco House - Hype |
| (Extended Mix) | aiff | 1411k | 115.0 | 7A | CR - Disco - Extended |

- **One row per distinct file** — so two copies in different formats or bitrates
  are both visible and easy to compare. Byte-identical true dupes collapse into
  one row with a `×N` note (use [`playlist-dedupe`](../playlist-dedupe/) if you
  want to prune those).
- **Version** is the version name; `Original` is the plain, neutral version.
- **Playlists** shows every (non-protected) crate holding that file, footnotes how
  many Backup crates also do, and shows `—` when a file is in no playlist.

Add `--format tsv` for tab-separated output.

## Catching renamed edits & sample flips (optional)

Fuzzy title matching can't connect a renamed edit to its original — e.g.
*"Mark Knight, Wh0 - Clap Your Hands"* is an edit of *"Chic - Everybody Dance"*
but shares no title text. To link them, add a **bracketed token** to the track's
**Comments** field in rekordbox:

```
[edit of: Chic - Everybody Dance]
[sample: The Whispers - Headlights]
[remix of: ...]   [flip of: ...]   [bootleg of: ...]
```

version-finder **reads** these (it never writes them), so a query for the
original pulls in your declared edits, and a matched edit shows what it samples.
The token can sit anywhere in the field, so it coexists with MIK energy/key text.

## Safety

- **Read-only.** It never writes to the database and never touches audio files, so
  it's safe to run with rekordbox open.
- **Never invents a version** — every row is a real file in your library; if
  nothing matches, it tells you so rather than guessing.
- **Backup playlists are shown but flagged**, never silently hidden.
- Fuzzy matching can still mis-pair messy metadata, so treat the table as a
  high-confidence shortlist and spot-check anything surprising.

## Running the scripts directly (development)

Run from the `scripts/` directory (they import a shared `rb_common.py` by relative
import):

```sh
cd scripts
python3 test_matcher.py                                          # unit-test the matcher, no DB access
python3 find_versions.py "Artist - Title" --out /tmp/rb-versions.json
python3 show_versions.py /tmp/rb-versions.json
python3 show_versions.py /tmp/rb-versions.json --format tsv
```

See `references/data-model.md` for the rekordbox schema details the scripts rely on.
