# rekordbox-skills

A collection of [Claude Code](https://claude.com/claude-code) skills to help
manage a [rekordbox](https://rekordbox.com) library — clone it, point Claude at
it, and ask in plain language.

> macOS / Linux only. Setup relies on symlinks committed to the repo, which don't
> work on Windows.

**Disclaimer:** I do not support the use of AI in music making, DJing or any form of the Arts. I do think AI can be used to automate the mundane.

## Available skills

- **[playlist-dedupe](.agents/skills/playlist-dedupe/)** — find and prune
  duplicate tracks in a playlist (or your whole library), keeping the best-quality
  copy. Version-aware and reviews every group with you before changing anything.
- **[upgrade-finder](.agents/skills/upgrade-finder/)** — scan a playlist for
  low-bitrate tracks and find higher-quality copies of the same recording already
  in your library (lossless beats lossy, then higher bitrate). Read-only: it shows
  a report and changes nothing.
- **[audio-converter](.agents/skills/audio-converter/)** — convert audio files
  between WAV, AIFF, and AAC using macOS's built-in `afconvert` (nothing to
  install). Standalone and non-destructive; it won't do lossy→lossless or
  same-format conversions. (MP3 not supported yet.)

Each skill's own README explains what it does and how to use it.

## How it's laid out

- Canonical skill files live under `.agents/skills/<name>/`.
- A committed symlink `.claude/skills/<name> -> ../../.agents/skills/<name>` is what
  Claude Code discovers (Claude Code only scans `.claude/skills/`, not `.agents/`).

## Getting started

1. Clone this repo:

   ```sh
   git clone <repo-url> rekordbox-skills
   ```

2. Install [Claude Code](https://claude.com/claude-code). Individual skills may
   have extra prerequisites (e.g. Python packages) — see each skill's README.

3. Launch Claude from inside the repo, giving it access to your rekordbox library:

   ```sh
   cd rekordbox-skills
   claude --add-dir ~/Library/Pioneer/rekordbox
   ```

   - Launching in the repo loads the skills (via the committed `.claude/skills/`
     symlinks — Claude discovers skills from the directory you start in).
   - `--add-dir ~/Library/Pioneer/rekordbox` lets Claude read your library files
     directly, including `master.db` and any backups a skill creates.

4. Ask Claude to do the thing — e.g. *"dedupe my 'Disco' playlist."*
