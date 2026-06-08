---
name: audio-converter
description: >-
  Convert audio files between rekordbox/CDJ-compatible formats using macOS's
  built-in afconvert -- no third-party tools, nothing to install. Handles four
  conversions: WAV->AIFF and AIFF->WAV (lossless, no quality loss) and WAV->AAC
  and AIFF->AAC (lossy, smaller files; AAC defaults to 320 kbps, written as
  .m4a). It deliberately REFUSES two cases: lossy->lossless (e.g. AAC->WAV/AIFF,
  which can't recover discarded audio) and same-format conversions
  (WAV->WAV, AIFF->AIFF, AAC->AAC). This is a STANDALONE file converter -- it
  reads and writes files only and never touches the rekordbox database, so
  rekordbox can stay open. Use it whenever the user wants to convert, transcode,
  or change the format of audio files -- e.g. "convert these AIFFs to WAV",
  "turn my WAVs into AIFF so the tags stick", "compress this folder to AAC for
  the CDJs", "make these smaller", "batch convert a folder to AAC" -- including
  loose phrasings like "change these to wav" or "shrink these files." MP3 is not
  supported yet (it would need a third-party encoder). It accepts single files,
  multiple files, or a whole folder, writes output next to the originals, and
  never deletes or overwrites the originals.
---

# audio-converter

Convert WAV/AIFF audio to **WAV, AIFF, or AAC** using macOS's built-in
**`afconvert`**. No Homebrew, no `pip`, no bundled binaries — `afconvert` ships
with macOS at `/usr/bin/afconvert`. This skill is **standalone**: it reads and
writes files only and **never touches the rekordbox database**, so rekordbox can
stay open.

## Why it works this way

`afconvert` (Apple CoreAudio) natively encodes WAV, AIFF, and AAC, so the skill
is just a thin, deterministic wrapper around it — all the policy lives in the
Python so runs are predictable and you orchestrate and relay.

The script lives in `scripts/` and is run with `python3` from that directory. It
needs only the Python standard library plus the system `afconvert`/`afinfo`
tools. **macOS only.**

## What it will and won't convert

Only these four conversions are allowed:

| From → To | Kind |
| --- | --- |
| WAV → AIFF | lossless — no quality loss |
| AIFF → WAV | lossless — no quality loss |
| WAV → AAC (`.m4a`) | lossy — smaller files |
| AIFF → AAC (`.m4a`) | lossy — smaller files |

Two cases are **refused on purpose**, and the script reports a clear reason:

- **Lossy → lossless** (e.g. AAC → WAV/AIFF). AAC already discarded audio;
  re-wrapping it in a lossless container can't bring that back — it just makes a
  bigger file that only *looks* lossless.
- **Same format → same format** (WAV→WAV, AIFF→AIFF, AAC→AAC). Pointless, and
  re-encoding AAC would only degrade it further.

WAV↔AIFF preserves the source bit depth (16- or 24-bit). AAC defaults to
**320 kbps**. **MP3 is not supported yet** — it's the one format `afconvert`
can't produce, so it would need a third-party encoder (deferred for now).

## The workflow

### Step 1 — Confirm the inputs and the target format

Establish *which files* and *what target*. The input can be one file, several
files, or a folder (folders convert every supported file inside, non-recursive).
If the user's target would hit a refused case (e.g. they ask to turn AAC into
WAV), say so up front rather than running it.

### Step 2 — Dry run (preview, writes nothing)

```
python3 scripts/convert.py <paths...> --to <wav|aiff|aac> --dry-run
```

This lists exactly what would be written (`planned: wav -> aac`) and flags any
files that would be skipped (refused conversion, or an output that already
exists). Show this to the user to confirm before writing.

### Step 3 — Convert

```
python3 scripts/convert.py <paths...> --to <wav|aiff|aac>
```

Options:
- `--bitrate 256k` — AAC bitrate (default `320k`; ignored for WAV/AIFF).
- `--overwrite` — replace an existing output file (default is to **skip** it).
- `--format json` — machine-readable output instead of the table.

Output files land **next to the originals** with the new extension (`.wav`,
`.aiff`, or `.m4a`). **Originals are never modified or deleted.** The script
prints a summary table and exits non-zero if any conversion errored.

Then relay the result: how many converted, anything skipped and why.

## Guardrails

- **Standalone and non-destructive.** Reads/writes files only; never opens or
  edits the rekordbox database. Originals are always kept. Existing outputs are
  skipped unless `--overwrite` is given.
- **Refuses bad conversions.** Lossy→lossless and same-format conversions are
  blocked with a clear message — don't try to work around them.
- **macOS only.** If `afconvert` isn't found the script stops with an
  explanation. MP3 output is not available (deferred — would need ffmpeg/lame).
