# AGENTS.md

Guidance for AI coding agents (Claude Code, Cursor, etc.) working in this repo.
`CLAUDE.md` points here.

## What this repo is

A collection of agent **skills to help manage a [rekordbox](https://rekordbox.com)
library** — clone it, point an agent at it, and the user asks in plain language.
This repo is the **single source of truth**; it's meant to be cloned and shared.

## Skills & routing

Each skill lives under `.agents/skills/<name>/` and has its own `SKILL.md`
(execution instructions) and `README.md` (overview). Route to a skill when the
user's request matches:

| Skill | What it does | Route here when… |
| --- | --- | --- |
| **[playlist-dedupe](.agents/skills/playlist-dedupe/)** | Finds and prunes duplicate tracks in a playlist or the whole library, keeping the best-quality copy; version-aware and reviews every group with the user before writing. | The user wants to clean up, dedupe, find duplicates in, or remove repeated / lower-quality tracks from a rekordbox playlist or library — e.g. *"this playlist has a bunch of dupes"* or *"I have the same song twice in different quality."* |
| **[upgrade-finder](.agents/skills/upgrade-finder/)** | Scans a playlist's low-bitrate (≤320k) tracks and searches the whole library for a higher-quality file of the same recording (lossless beats lossy, then higher bitrate). Read-only report; takes no action. | The user wants to find higher-quality versions they already own — e.g. *"are there better copies of these tracks?"*, *"which of these MP3s do I have a FLAC/WAV of?"*, *"find upgrades for my Disco playlist."* |
| **[audio-converter](.agents/skills/audio-converter/)** | Converts audio files between WAV, AIFF, and AAC using macOS's built-in `afconvert` (no installs). Standalone — touches files only, not the rekordbox DB. Refuses lossy→lossless and same-format conversions. | The user wants to convert, transcode, or change the format of audio files — e.g. *"convert these AIFFs to WAV"*, *"turn my WAVs into AIFF"*, *"compress this folder to AAC for the CDJs"*, *"shrink these files."* (MP3 not supported yet.) |

When a skill matches, load its `SKILL.md` and follow it — the skill's own docs are
authoritative for its workflow, schema, and guardrails (see e.g.
`.agents/skills/playlist-dedupe/references/data-model.md`).

## How it's laid out

- Canonical skill files live under `.agents/skills/<name>/` — **edit these.**
- A committed symlink `.claude/skills/<name> -> ../../.agents/skills/<name>` is what
  Claude Code's skill discovery scans (it only looks in `.claude/skills/`, not
  `.agents/`). The symlink points straight at the canonical files, so edits under
  `.agents/skills/` take effect immediately — never edit through the symlink path.
- Committed symlinks are followed on macOS/Linux but **not Windows**.

## Adding a new skill

Follow the same shape as the existing skills so they stay consistent and discoverable:

1. **Create the skill folder** at `.agents/skills/<name>/` containing:
   - `SKILL.md` — the agent execution instructions, with YAML frontmatter (`name`
     and a thorough `description`; the description is what triggers routing, so spell
     out what the skill does and when to use it, including example user phrasings).
   - `README.md` — **always include one**, written for a human reading the folder:
     what the skill does, prerequisites, how to use it, and safety notes.
   - `scripts/` for any code (run from that directory; share helpers via relative
     import like `rb_common.py`) and `references/` for deeper notes (schema, design).
2. **Create the discovery symlink** so Claude Code can find it (it only scans
   `.claude/skills/`). Use a **relative** target so it resolves on any clone:

   ```sh
   ln -s ../../.agents/skills/<name> .claude/skills/<name>
   ```

3. **Register it for discoverability** — update both top-level docs:
   - **`AGENTS.md`**: add a row to the *Skills & routing* table above (what it does +
     when to route to it).
   - **`README.md`**: add an entry to the *Available skills* list (human-facing
     one-liner linking to the skill's folder).
4. **Verify**: `readlink .claude/skills/<name>` resolves, and the skill loads when
   you launch Claude in the repo.

## Private project planning

When the user says **"Create a project [name]"** (or "start a project for X", "new project: X", "plan out X"), read `.new-features/AGENTS.md` and follow the instructions there to create a project plan file. The `.new-features/` folder is gitignored — plan files are local only and deleted once the skill ships.

## Working in this repo

- **Launch**: start the agent in this repo so the `.claude/skills/` symlinks load
  the skills, and give it access to the library for reading `master.db` / backups:

  ```sh
  claude --add-dir ~/Library/Pioneer/rekordbox
  ```

  (Skill scripts find the DB on their own via `pyrekordbox`, so `--add-dir` is only
  for the agent's own file access.)
- **Running a skill's scripts**: run them from that skill's `scripts/` directory —
  they import a shared `rb_common.py` by relative import. Example:

  ```sh
  cd .agents/skills/playlist-dedupe/scripts
  python3 test_matcher.py
  ```
- **Dependency** (for skills that touch the DB): `pip3 install pyrekordbox --break-system-packages`.
  `master.db` is an encrypted SQLCipher database — always open it through
  `pyrekordbox`, never plain `sqlite3`.
