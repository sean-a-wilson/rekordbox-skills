# audio-converter

Convert audio files between **WAV, AIFF, and AAC** using macOS's built-in
`afconvert` — **no third-party tools and nothing to install.** It's a
**standalone** converter: it reads and writes files only and never touches your
rekordbox library, so rekordbox can stay open.

## What it does

Four conversions, and only these:

| From → To | Kind |
|---|---|
| WAV → AIFF | lossless — no quality loss |
| AIFF → WAV | lossless — no quality loss |
| WAV → AAC (`.m4a`) | lossy — smaller files (defaults to 320 kbps) |
| AIFF → AAC (`.m4a`) | lossy — smaller files (defaults to 320 kbps) |

Two things it **won't** do, on purpose:

- **Lossy → lossless** (e.g. AAC → WAV/AIFF) — once AAC throws audio away, no
  conversion can bring it back; you'd just get a bigger file that looks lossless
  but isn't.
- **Same format → same format** (WAV→WAV, AIFF→AIFF, AAC→AAC) — pointless, and
  re-encoding AAC only degrades it.

WAV↔AIFF keeps the original bit depth (16- or 24-bit). A handy real use:
**WAV→AIFF** gives you cleaner metadata/tags in rekordbox, since AIFF carries
tags better than WAV.

> **MP3 isn't supported (yet).** It's the one common format macOS's `afconvert`
> can't produce, so it would need a third-party encoder. Left out for now.

## Prerequisites

- macOS (uses the built-in `/usr/bin/afconvert` and `afinfo`).
- Python 3.

That's it — no packages to install.

## Using it

Just ask Claude in plain language once the repo is loaded (see the repo root
README for launch instructions):

> "convert these AIFFs to WAV"
> "turn my WAVs into AIFF so the tags stick in rekordbox"
> "compress this folder to AAC for the CDJs"
> "shrink these files to AAC at 256k"

You can hand it a single file, several files, or a whole folder (it converts
every supported file inside). Converted files are written **next to the
originals**, and the **originals are never changed or deleted**.

## Safety

- **Non-destructive.** Output lands beside the originals; originals are kept. An
  existing output file is skipped unless you ask to overwrite.
- **Standalone.** It never opens or edits the rekordbox database — rekordbox can
  stay open the whole time.
- **Refuses bad conversions** (lossy→lossless, same-format) with a clear reason.

## Running the script directly (development)

Run from the `scripts/` directory:

```sh
cd scripts
python3 test_convert.py                                  # unit-test the policy, no afconvert
python3 convert.py track.wav --to aiff                   # one file
python3 convert.py a.wav b.aiff --to aac                 # several files
python3 convert.py /path/to/folder --to aac --dry-run    # preview a folder batch
python3 convert.py /path/to/folder --to aac              # convert (320k default)
python3 convert.py track.wav --to aac --bitrate 256k     # override AAC bitrate
python3 convert.py track.wav --to aiff --overwrite       # replace existing output
```
