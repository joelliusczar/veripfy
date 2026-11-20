# veripfy

Calculates AccurateRip v1 and v2 checksums for lossless audio files, with
optional offset correction, and checks them against the AccurateRip database.

## Requirements

- Linux with Python 3.10+
- [numpy](https://numpy.org/)
- [ffmpeg](https://ffmpeg.org/) (`ffmpeg` and `ffprobe` must be on your `PATH`)

## Install

```bash
./install.sh
```

This installs veripfy for your user only, with no root or sudo needed:

| Path | Contents |
| --- | --- |
| `~/.local/share/veripfy/` | A copy of `veripfy.py` and its own virtual environment with numpy |
| `~/.local/bin/veripfy` | The launcher you run, which is on the PATH on most Linux systems |

Then run `veripfy` from any directory.

You can run `./install.sh` as often as you like, with no cleanup in between:

- It copies the current `veripfy.py` each time, so after editing the script,
  run `./install.sh` again to install your changes.
- It reuses the existing virtual environment, and only installs Python
  packages that are missing or out of date.
- If the virtual environment is broken (for example after a Python upgrade),
  it rebuilds it.
- It replaces each file in one step, so an interrupted run never leaves a
  half-written install.

Other commands:

```bash
./install.sh --recreate    # also rebuild the virtual environment from scratch
./install.sh --uninstall   # remove the installed copy and launcher
```

More details:

- **ffmpeg:** the script checks for Python 3.10+ and ffmpeg. ffmpeg is a system
  package pip can't install, so if it's missing the script prints the install
  command for your distro.
- **System Python:** packages only ever go into veripfy's own virtual
  environment, never your system Python.
- **Creating the environment:** the script tries Python's `venv` module first,
  then `virtualenv`. If neither is available, it falls back to the system
  Python as long as numpy is already installed there. On Debian/Ubuntu,
  `sudo apt install python3-venv` provides the `venv` module.
- **Existing launcher:** the script won't overwrite a `~/.local/bin/veripfy`
  that it didn't create.
- **PATH:** if `~/.local/bin` isn't on your PATH, the script prints the line
  to add to `~/.bashrc`.
- **Install location:** set `VERIPFY_HOME` (program files) and/or
  `VERIPFY_BIN_DIR` (launcher) to install somewhere else, for example
  `VERIPFY_BIN_DIR=~/bin ./install.sh`. Use the same values with
  `--uninstall`.

### Running from the repository instead

For working on veripfy itself, `./setup.sh` creates a `.venv` inside the
repository and a `./veripfy` launcher that runs the repository's copy
directly, so edits take effect immediately without reinstalling.
`./setup.sh --recreate` rebuilds that environment.

You can also run `./veripfy.py` directly if numpy is installed in your system
Python. The examples below use `./veripfy.py`; after installing, `veripfy`
works the same way.

## Supported formats

Any lossless format ffmpeg can decode: FLAC, WAV, W64, RF64, AIFF, ALAC (.m4a),
WavPack (.wv), Monkey's Audio (.ape), TTA and TAK.

AccurateRip only covers CD audio (44.1 kHz, 16-bit, stereo). veripfy rejects
lossy files and files in any other format. `--force` converts them anyway, but
their checksums won't match anything in the database.

## Usage

Pass the files as arguments. They're treated as one album in the order you
list them, so the first file is track 1 and the last is the final track:

```bash
./veripfy.py "01 - Intro.flac" "02 - Song Two.flac" "03 - Outro.flac"
```

With an offset correction (for example, a drive read offset of +6 samples):

```bash
./veripfy.py -o 6 "01 - Intro.flac" "02 - Song Two.flac" "03 - Outro.flac"
```

Other ways to give it a list:

```bash
# No arguments: scan the current directory (add -r to include subfolders)
cd ~/Music/Album && veripfy


# Let the shell expand the glob (sorted alphabetically by bash)
./veripfy.py ~/Music/Album/*.flac

# Quote the glob so veripfy expands it (sorted naturally, so track 2 comes before track 10)
./veripfy.py '~/Music/Album/*.flac'

# Recursive glob
./veripfy.py '~/Music/Album/**/*.flac'

# A whole album folder
./veripfy.py ~/Music/Album/

# A list of paths kept in a text file, one per line
xargs -d '\n' ./veripfy.py -o 6 < tracklist.txt

# Hashes only, no database lookup, as JSON
./veripfy.py -n --json *.flac
```

If your file names don't sort into track order, list the files in the right
order yourself or add `-s` to sort them naturally. Quote any path that
contains spaces.

## Example output

```
Offset correction: +6 samples
Track     Length  AR v1     AR v2     AccurateRip           File
  1F     4:12.40  41FC55FF  4A142395  match v2 conf 12      01 - Intro.flac
  2      3:25.25  0F58CF77  128812E7  match v1 conf 5       02 - Song Two.flac
  3L     5:03.33  1854DC4D  1C5F5860  no match (db 15)      03 - Outro.flac

AccurateRip disc ID: 003-000000cb-0000029c-06000103
AccurateRip: 2 of 3 tracks accurately ripped
```

- `F` and `L` mark the first and last track. AccurateRip leaves out the first
  2939 samples of the first track and the last 2940 samples of the last track.
- Length is shown as `minutes:seconds.frames` (75 CD frames per second).
- For a match, the AccurateRip column shows whether the v2 or v1 checksum
  matched and its confidence (how many other rips agree). For no match, it
  shows the highest confidence the database holds for that track.
- The hashes always print, even if the disc isn't in the database or the
  lookup fails.

## Offset correction

`-o` / `--offset SAMPLES` shifts the whole disc's audio before checksumming,
the way re-ripping with a different drive offset would:

- A positive number moves the audio that many samples earlier; a negative
  number moves it later.
- Samples that cross a track boundary are taken from the neighbouring file.
- Past the start or end of the disc, the missing samples are treated as
  silence.

If a disc matches nothing, the usual cause is a wrong offset, so try your
drive's read offset.

## AccurateRip lookup

The lookup needs every track of the disc, in order. It is skipped (with a note)
when you pass only some tracks, use `--tracks` or `--single`, or a file can't
be read. It's a plain HTTP request to accuraterip.com, and all it sends is the
disc ID, which is worked out from the track lengths.

Some discs need extra information to get the right disc ID:

```bash
# Hidden audio before track 1 that the rip left out: give its length in sectors
./veripfy.py --pregap 15220 *.flac

# Enhanced CD (a data track after the audio): data track start and disc lead-out
# sectors, from the rip log or `cd-info`
./veripfy.py --data-track 207106:210385 *.flac

# Check against a database file you've already downloaded, without going online
./veripfy.py --db-file dBAR-012-0013bd5a-00b8d489-c60af50d.bin *.flac
```

## Requiring a rip log

With `-l` / `--require-log`, each directory you pass (or the current
directory, if you pass none) must contain a rip log, meaning any file ending
in `.log` such as an EAC, XLD or whipper log. With `-r` the log can be
anywhere below the directory. A directory without one is skipped, with a
message saying so, and isn't checksummed. Files passed by name or glob aren't
affected.

```bash
veripfy -l ~/Music/Album/
```

If any directory was skipped and nothing else went wrong, veripfy exits with
status 5. `--require-log` has no effect with `--tags-only`, since that does
no checksums.

## Checking tags

`-t` / `--check-tags` also checks each file for missing tags. By default it
requires `title`, `artist`, `album`, `track`, `disc` and `albumartist`. A tag
that's present but empty counts as missing.

```bash
./veripfy.py -t *.flac
```

```
Track     Length  AR v1     AR v2     AccurateRip           File
  1F     4:12.40  41FC55FF  4A142395  match v2 conf 12      01 - Intro.flac
  2      3:25.25  0F58CF77  128812E7  match v1 conf 5       02 - Song Two.flac
       missing tags: disc, albumartist
...
Tags: 1 of 3 tracks missing tags (title, artist, album, track, disc, albumartist)
```

To require a different set, pass `--tags` with a comma-separated list (this
turns on the check by itself):

```bash
./veripfy.py --tags title,artist,album,albumartist,track,disc,date,genre *.flac
```

Tag names are matched ignoring case, spaces, `_` and `-`, and common
spellings from different formats are accepted, so `track` also finds
`TRACKNUMBER`, `albumartist` finds `ALBUM ARTIST` or `album_artist`, and
`date` finds `YEAR`. With `--json`, each track gets a `missing_tags` list.

To check only the tags, use `-T` / `--tags-only`. It skips the AccurateRip
checksums and lookup and doesn't decode the audio, so it's fast. It exits
with status 4 if any file is missing a tag, and works with `--tags`:

```bash
veripfy -T ~/Music/Album/ && echo "all tagged"
```

```
Track  File
    1  01 - Intro.flac
    2  02 - Song Two.flac
       missing tags: disc, albumartist
    3  03 - Outro.flac

Tags: 1 of 3 tracks missing tags (title, artist, album, track, disc, albumartist)
```

## Options

| Option | Description |
| --- | --- |
| `-o`, `--offset SAMPLES` | Offset correction applied before checksumming |
| `-n`, `--no-lookup` | Don't query the AccurateRip database, only print the hashes |
| `--db-file PATH` | Use a downloaded `dBAR-*.bin` file instead of querying the server |
| `--pregap SECTORS` | Start sector of track 1 (hidden track audio left out of the rip) |
| `--data-track START:LEADOUT` | Enhanced CD data track start and disc lead-out sectors |
| `--timeout SECONDS` | Network timeout for the lookup (default 15) |
| `--first N` | Track number of the first file (default 1) |
| `--total N` | Total tracks on the disc, if the files are only part of it |
| `--tracks` | Treat every file as a middle track (no first/last skipping) |
| `--single` | Treat every file as its own disc (first and last skipping on each) |
| `-r`, `--recursive` | Look in subfolders of directory arguments |
| `-l`, `--require-log` | Skip directories that don't contain a rip log (`*.log`) |
| `-s`, `--sort` | Sort all files naturally instead of keeping argument order |
| `-t`, `--check-tags` | Also report files missing title, artist, album, track, disc or albumartist |
| `-T`, `--tags-only` | Only check tags: no checksums or lookup; exit status 4 if any are missing |
| `--tags LIST` | Comma-separated tags to require instead (turns on the tag check) |
| `-f`, `--force` | Convert files that aren't CD format (checksums won't match the database) |
| `-j`, `--jobs N` | Files to process in parallel (default: CPU count) |
| `--json` | JSON output |
| `--full-path` | Show full paths in the table |
| `--no-color` | Turn off coloured output (also respects `NO_COLOR`) |

## Exit status

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A file couldn't be read or processed |
| 2 | Bad usage, or no input files found |
| 3 | The lookup ran but not every track matched |
| 4 | With `--check-tags`, `--tags` or `--tags-only`: some files are missing tags |
| 5 | With `--require-log`: a directory was skipped because it has no log |

## License

MIT. See [LICENSE](LICENSE).
