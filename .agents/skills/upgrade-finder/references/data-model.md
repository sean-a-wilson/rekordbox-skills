# rekordbox data model (for the upgrade-finder skill)

Notes on the parts of the rekordbox 6/7 database the scripts rely on. Read this
only if you need to extend the scripts beyond what they already expose. This skill
is **read-only** — it never writes the database — so the matching/quality notes
below are what matter; there is no editing path to worry about.

## Access

`master.db` is an **encrypted SQLCipher** database — `sqlite3` reports
"file is not a database". Use `pyrekordbox`:

```python
from pyrekordbox import Rekordbox6Database
db = Rekordbox6Database()   # auto-discovers library + key
```

Install: `pip3 install pyrekordbox --break-system-packages`

## Tables used

### DjmdContent — one row per track/file
Relevant fields:
- `ID` (str) — content id
- `Title`, `Artist.Name` (relationship), `BitRate` (kbps int), `FileSize` (bytes)
- `FolderPath` — absolute file path; the extension is the reliable lossless signal
- `FileType` — integer code (1=mp3, 4=m4a, 11=wav, 12=aiff, …); ambiguous, so the
  scripts key off the file extension instead.
- `Length` (int) — track duration in **seconds**. Used as a matching signal: two
  candidates within ~3s / 2% are the same recording; a notable gap marks a version
  variant. Stored on every track fact and shown in review (`5:51`).
- `BPM` (int) — tempo stored as an integer **×100** (`12280` → `122.8`). The scripts
  divide by 100. Used to corroborate a match (within ~0.5) and shown in review.
- `Key` (relationship) — musical key (`Key.ScaleName`). Captured for display; not a
  matching signal.
- `Remixer` (relationship/str) — remixer credit. A populated remixer is treated as a
  distinguishing version marker even when the title doesn't spell the version out.

### Version markers (how titles are parsed)
`rb_common.split_title(title)` → `(base, markers)`. Parenthetical/bracketed segments
**and any number of trailing `- <tag>` suffixes** are classified (suffixes are peeled
right-to-left, so `… - Female Version - 8A` strips the key then the version tag):
- **neutral** → dropped, so the tag matches the plain title. Neutral covers an exact
  match in `NEUTRAL_MARKERS` ("original mix", "album version", "main mix", …), a
  **neutral-word + version/mix phrase** (e.g. "Original Version", "Stereo Mix" — only
  filler words before a generic `version`/`mix`), and **Camelot key tags** like `8A` /
  `12B` (DJ annotations, not versions). So `Disco Nights (Rock Freak) (Original Mix)`
  and `… (Original Version)` both reduce to base `disco nights rock freak`.
- **distinguishing** → pulled out as a marker; differing marker sets ⇒ version variant.
  Distinguishing covers a `DISTINGUISHING_KEYWORDS` term (remix, edit, extended, dub,
  instrumental, radio, club, vocal, acapella, vip, bootleg, rework, live, …), a `7"`/`12"`
  segment, and a **named version** — a custom name ending in `mix`/`version`, e.g.
  `Labor Of Love Mix`, `Jim Burgess Mix`, `Female Version`. (This is the difference from
  a *neutral* version phrase: a real name in front makes it a distinct take.)
- **title_part** (anything else, e.g. "Rock Freak", "Love Break") → kept in the base,
  since it identifies the song. Repeated identical title-parts are de-duplicated, so
  `(Love Break) (Love Break)` doesn't inflate the base vs the single-tag twin.

A group is **exact** only when all members share the same marker set, their lengths
agree, **and** no member pair was joined by the loose duration/BPM rescue (see below);
otherwise it is a **version_variant** (defaults to `pending`, surfaced for review).

### Title gate + duration/BPM rescue (`detect_duplicates._pair_matches`)
Two tracks are grouped when their **base titles** clear the title threshold, with two
fallbacks for messy tags, then an artist gate:
- **Tier A** — `t_sim ≥ title_thr`.
- **Tier B** — `t_sim ≥ title_thr − 0.10` and length *and* BPM close (`lengths_close`
  ~3s/2%, `bpms_close` ~0.5).
- **Tier C (loose)** — `t_sim ≥ title_thr − 0.20` and length within ~2s
  (`lengths_very_close`) *and* BPM within ~1.0 (`bpms_very_close`).

Tier C exists to *surface* near-identical recordings with garbled titles for review.
A pair joined only by Tier C is **not exact-eligible**: any group it touches is forced
to `version_variant` (`pending`), so the loose rescue can never silently collapse a
track — it only puts it in front of the user.

### DjmdSongPlaylist — playlist membership (the join table)
- `ID` (str) — membership row id
- `PlaylistID`, `ContentID`, `TrackNo`
- A track in N playlists has N rows here. This is how the report's "playlists that
  could be upgraded" column is computed: find every row with a candidate's
  `ContentID` (`find_upgrades.memberships_for`).

### DjmdContent — whole-library scan
- `db.get_content()` enumerates **every** track in the library (not just one
  playlist). `find_upgrades.py` flattens each row with `rb_common.track_facts` and
  searches it for a higher-quality copy of each playlist candidate.

### DjmdPlaylist — the playlist/folder tree
- `ID` (str), `Name`, `ParentID` (str; root sentinel is the string `"root"`)
- Folders and playlists share this table; walk `ParentID` to build a path. A
  playlist whose path contains an exclude term (default `Backup`) is flagged
  protected in the report (counted, not listed).

## Read-only

This skill never writes the database — there is no `add_to_playlist` /
`remove_from_playlist` / `commit` path, no backups, and rekordbox can stay open.
Applying an upgrade (swapping a playlist entry to the better file) is out of scope.

## Determinism notes
- Normalization and similarity live in `rb_common.py` and are fixed functions.
- The winner ranking key ends in the content id so ties resolve identically every
  run; the manifest is reproducible for a given library + thresholds.
