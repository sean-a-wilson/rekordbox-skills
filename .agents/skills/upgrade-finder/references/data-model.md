# rekordbox data model (upgrade-finder)

The shared schema, `split_title` version markers, the title gate + duration/BPM
rescue, the membership/folder tables, and the editing API used by the optional
apply step all live in the canonical reference:

➡ [`../../../shared/references/data-model.md`](../../../shared/references/data-model.md)

## Skill-specific notes

- **Whole-library scan.** `db.get_content()` enumerates **every** track in the
  library (not just one playlist). `find_upgrades.py` flattens each row with
  `rb_common.track_facts` and searches it for a higher-quality copy of each lossy
  playlist candidate.
- **Membership column.** The report's "playlists this could upgrade" column comes
  from `DjmdSongPlaylist`: every row carrying a candidate's `ContentID`
  (`find_upgrades.memberships_for`). Playlists whose path contains an exclude term
  (default `Backup`) are flagged protected (counted, not listed).
- **Apply path.** Scanning is read-only (rekordbox can stay open). The optional,
  opt-in apply step (swap the lossy entry for the better file) writes through the
  shared `apply_core.py` — same safety model as playlist-dedupe (permanent DB
  backup, in-app snapshots, rekordbox-must-be-closed, never-assume-scope). See the
  editing-API section of the canonical doc.
