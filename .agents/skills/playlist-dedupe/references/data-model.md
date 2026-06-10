# rekordbox data model (playlist-dedupe)

The schema, title parsing, matching tiers, and the editing API this skill writes
through all live in the shared canonical reference:

➡ [`../../../shared/references/data-model.md`](../../../shared/references/data-model.md)

That document is the authoritative reference for playlist-dedupe — it covers
DjmdContent fields, `split_title` version markers, the title gate + duration/BPM
rescue, the membership/folder tables, the `apply_core.py` editing-API gotchas
(attribute bypass, commit-on-remove, adds-before-removes), and the determinism
notes. There is nothing dedupe-specific beyond it.
