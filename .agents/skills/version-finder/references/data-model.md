# version-finder — data model & matching notes

The schema details and matching rules the scripts rely on. All access is
**read-only** through `pyrekordbox` (the `master.db` is an encrypted SQLCipher
database — never open it with plain `sqlite3`).

## Tables & fields used

- **`DjmdContent`** — one row per track/file. Fields read (via
  `rb_common.track_facts`, plus `Commnt` read directly in `find_versions.py`):
  - `ID` — content id. **A string, not an int** — compare as a string when
    joining to playlist membership.
  - `Title`, `Artist` (a relationship → `.Name`), `Remixer` (relationship).
  - `BPM` — stored as an **integer ×100** (`11477` → `114.77`); always divide via
    `track_facts`, never display raw.
  - `Key` — a **relationship** (`Key.ScaleName`); may be empty → shown as `—`.
  - `BitRate`, `FileSize`, `Length` (seconds), `FolderPath` (for the extension /
    lossless test).
  - `Commnt` — **note the spelling, no `e`.** Free text; may already hold MIK
    energy/key text. Read for the relationship token (below), parsed as a
    delimited token rather than assuming the whole field is the link.
- **`DjmdSongPlaylist`** — membership rows. Filter
  `DjmdSongPlaylist.ContentID == str(content_id)` to find every playlist a file is
  in; `PlaylistID` (string) joins to the playlist index.
- **`DjmdPlaylist`** — playlist/folder nodes; `rb_common.build_playlist_index` +
  `playlist_path` walk `ParentID` up to the root for the full folder path.

## Version grouping & one-row-per-file

- `rb_common.split_title` splits a title into `(base, distinguishing_markers)`:
  neutral mix tags ("Original Mix", "Album Version") are dropped; distinguishing
  tags ("Extended Mix", "Dub", "Radio Edit", a named remix) are kept as a marker
  set; an unknown identity parenthetical stays in the base. A populated `Remixer`
  is folded into the marker set by `track_facts`.
- **Version label** = the marker set via `markers_label`; the **empty** set is
  relabeled `Original` (not rb_common's `plain`).
- **One row per distinct file.** Rows collapse only when they are byte-identical
  true dupes — same marker set **and** same format, bitrate, and length
  (`collapse_key`). Any difference in format or bitrate keeps files on separate
  rows. Collapsed copies show `×N` and union their playlist memberships.

## Clean-base matching (the key correctness step)

`split_title` only peels version tags off the **right** of the title and stops at
the first identity word — so an embedded `"Artist - "` on the **left** stays glued
to the base. For a blank-Artist row like `Sister Sledge - Lost In Music`, the base
is `sister sledge lost in music`, which would pollute a title-only score.

`find_versions.clean_base` fixes this: when the `Artist` relationship is blank and
the title carried a `" - "`, it strips the same leading segment
`rb_common.effective_artist` extracts, leaving `lost in music` to score against
the query title. The artist is only a **soft boost** (`title_match`) — a strong
title alone always passes, so a remix with an empty Artist field still surfaces.

## Comments relationship token (read-only)

A user-authored token in `Commnt` links a renamed edit / sample flip to its
source song so it can join a query with zero shared title text. Grammar (matched
**case-insensitively, anywhere in the field**):

```
[ <rel> [of] : <Artist - Title> ]
   rel ∈ { edit, sample, remix, flip, bootleg }
```

e.g. `[edit of: Chic - Everybody Dance]`, `[sample: The Whispers - Headlights]`.
Both directions are used: a query for the original pulls in its declared edits,
and a matched edit surfaces its declared source (annotated inline in the Version
cell). The skill **never writes** this field.

## Constraints

- **Read-only throughout** — no writes, no apply step; rekordbox can stay open.
- IDs in `DjmdContent` / `DjmdPlaylist` / `DjmdSongPlaylist` are **strings**.
- A song can be in **0 playlists** (imported, not crated) → Playlists shows `—`.
- Backup playlists (path contains a `--exclude-path` term, default `Backup`) are
  still **shown but flagged** (`[+N protected]`), never hidden.
- **WAL:** very recent rekordbox edits may be invisible until the app closes and
  checkpoints. Acceptable for a read-only report — note it if results look stale.
