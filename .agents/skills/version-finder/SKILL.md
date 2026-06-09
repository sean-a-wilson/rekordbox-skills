---
name: version-finder
description: >-
  Take an "Artist - Song Title" query and scan the ENTIRE rekordbox library for
  every version, edit, and remix of that song the user already owns -- the
  original, the extended mix, the dub, the radio edit, each remix -- then print a
  read-only table: one row per file with its Version, Format, Bitrate, BPM, Key,
  and the playlists that hold it. Version-aware: it groups true versions of the
  SAME song together (stripping neutral mix tags, keeping distinguishing ones)
  instead of returning a flat filename list the way rekordbox's native search
  does, and it can surface renamed edits / sample flips that share no title text
  via an optional user-authored Comments token like "[edit of: Chic - Everybody
  Dance]". One row per distinct file, so multiple copies in different formats or
  bitrates are all visible. Entirely READ-ONLY -- it never writes to the database
  or touches audio files, so rekordbox can stay open. Use this whenever the user
  wants to know what versions of a track they have: "find all versions of <artist
  - title>", "what remixes of X do I have", "show me every version of this track",
  "do I have the extended mix of ...", "which playlists have my versions of
  <song>", "list all edits/remixes of <artist - title>", "how many versions of
  this song do I own".
---

# version-finder

Take an **"Artist - Song Title"** query, scan the **whole rekordbox library**, and
print a **read-only table** of every version of that song the user owns — the
original, the extended mix, the dub, each remix — with the format, bitrate, BPM,
key, and the playlists each file lives in. It answers the pre-set DJ question
*"what do I actually have for this track?"* without manually searching rekordbox
and eyeballing parenthetical tags.

It's a close cousin of [`upgrade-finder`](../upgrade-finder/): same encrypted-DB
access via `pyrekordbox`, the same version-aware title parser and playlist-path
resolver. But it scans library-wide from a free-text query, **groups by version**
(instead of finding one better copy), and **never writes anything** — there is no
apply step, so rekordbox can stay open the whole time.

## Why it works this way

rekordbox stores its library in an **encrypted** SQLite database (`master.db`);
plain `sqlite3` fails on it, so the scripts use `pyrekordbox`, which finds the
library and key automatically. All matching and grouping happens **inside the
Python scripts** so runs are deterministic — you orchestrate and relay.

If `pyrekordbox` is missing, install it once:
`pip3 install pyrekordbox --break-system-packages`

Scripts live in `scripts/` and are run with `python3` from that directory (they
import a shared `rb_common.py`). The data model is documented in
`references/data-model.md`.

**Everything here is read-only — rekordbox can stay open.**

## What counts as a "version"

A file is included when it is the **same song** as the query:

- **Title match** — a strong token-sorted similarity between the query title and
  the track's *clean base title*. The base has neutral mix tags ("Original Mix",
  "Album Version") stripped and an embedded `"Artist - "` prefix removed, so a
  blank-Artist row like `Sister Sledge - Lost In Music` still matches. The artist
  is only a **soft boost**, never a hard filter — so a remix with an empty Artist
  field still surfaces on a strong title.
- **Comments token** (optional, user-authored) — a bracketed token in the track's
  Comments field such as `[edit of: Chic - Everybody Dance]`, `[sample: ...]`,
  `[remix of: ...]`, `[flip of: ...]`, `[bootleg of: ...]` lets a **renamed edit
  or sample flip** with zero shared title text join the right song. The skill
  only **reads** these — it never writes the Comments field.

Distinct version markers (`Extended Mix`, `Dub`, `Radio Edit`, a remixer credit)
are **kept** so each version is its own group. The neutral bucket is labeled
**Original**.

## The workflow

### Step 1 — Get the query

The user names a track, e.g. *"what versions of Sister Sledge - Lost In Music do I
have?"* Pass it straight through as `"Artist - Song Title"`. If there's no
`" - "`, the whole string is treated as the title.

### Step 2 — Scan the library (read-only)

```
python3 scripts/find_versions.py "Sister Sledge - Lost In Music" --out /tmp/rb-versions.json
```

Read-only: it reads the whole library and writes a JSON report — it never touches
the database. Options:

- `--title-threshold 0.87` / `--artist-threshold 0.80` — fuzzy match tuning
  (rarely needed; raise the title threshold if unrelated songs sneak in, lower it
  if a known version is missed).
- `--exclude-path "Backup"` — comma-separated terms; a playlist whose full folder
  path contains one is flagged **protected** (Backup crates) in the report rather
  than listed in full.

### Step 3 — Show the table

```
python3 scripts/show_versions.py /tmp/rb-versions.json
```

Prints the table the user wants — **paste it complete** (the footer's
`Shown: N of N` confirms nothing was truncated):

| Version | Format | Bitrate | BPM | Key | Playlists |

- **One row per distinct file** — two copies of the same version in different
  format/bitrate are two rows, so the user can compare every copy. Byte-identical
  true dupes collapse into one row with a `×N` note (point the user at
  `playlist-dedupe` if they want to prune those).
- **Version** is the version label (`Original`, `(Extended Mix)`, …). A row that
  came in via — or carries — a Comments token is annotated inline, e.g.
  `Clap Your Hands — edit of Chic - Everybody Dance`.
- **Playlists** lists every non-protected crate holding that file, with a
  `[+N protected]` footnote for Backup crates and `—` when the file is in no
  playlist (imported but not crated). `--format tsv` for tab-separated output.

Then summarize: how many versions the user owns, and call out anything
noteworthy (e.g. an extended mix that only exists as a 192k MP3, or a version
that lives only in Backup playlists).

## Guardrails

- **Read-only — never writes.** No adds, removes, or edits to `DjmdContent` /
  `DjmdSongPlaylist` / `DjmdPlaylist`; never touches or moves audio files. Because
  it never writes, **rekordbox can stay open**.
- **Never invent a version.** Every row maps to a real `DjmdContent` row. If
  nothing matches, say so plainly — do **not** loosen the match until unrelated
  songs appear.
- **Comments tokens are read-only.** The user authors `[edit of: …]` tokens
  manually in rekordbox; this skill only reads them.
- **Backup playlists are shown but flagged**, never silently hidden.
- Fuzzy matching can still mis-pair messy metadata — treat the table as a
  high-confidence shortlist and spot-check anything surprising. Very recent
  rekordbox edits may be invisible until the app closes and checkpoints (WAL);
  note it if results look stale.
