#!/usr/bin/env python3
"""veripfy - compute AccurateRip v1/v2 checksums for lossless audio files.

Decoding is delegated to ffmpeg, so any lossless format ffmpeg can read works:
FLAC, WAV/W64/RF64, AIFF, ALAC (.m4a), WavPack, Monkey's Audio, TTA, TAK, ...

AccurateRip checksums are defined on CD audio (44.1 kHz, 16-bit, stereo).
By default the list of files is treated as one album in order: the first file
is track 1 and the last file is the final track, which matters because
AccurateRip skips the first 5 sectors (minus one sample) of the first track and
the last 5 sectors of the last track.

An offset correction (--offset) shifts the whole disc's audio stream by a
number of samples before checksumming, borrowing samples across track
boundaries from the adjacent files, exactly as re-reading with a different
drive offset would.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import numpy as np

__version__ = "1.0.0"

SAMPLES_PER_SECTOR = 588                         # 2352 bytes / 4 bytes per stereo sample
FIRST_TRACK_START = 5 * SAMPLES_PER_SECTOR        # first counted multiplier on track 1
LAST_TRACK_SKIP = 5 * SAMPLES_PER_SECTOR          # trailing samples ignored on last track
CHUNK_SAMPLES = 1 << 20                           # 4 MiB of PCM per read
MASK32 = np.uint64(0xFFFFFFFF)

AUDIO_EXTENSIONS = {
    ".flac", ".wav", ".wave", ".w64", ".rf64", ".aif", ".aiff", ".aifc",
    ".m4a", ".mp4", ".alac", ".caf", ".wv", ".ape", ".tta", ".tak",
}

LOSSLESS_CODECS = {
    "flac", "alac", "wavpack", "ape", "tta", "tak", "mlp", "truehd",
}  # plus every pcm_* codec


@dataclass
class Result:
    path: str
    track: int
    first: bool = False
    last: bool = False
    v1: int | None = None
    v2: int | None = None
    samples: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    match: str | None = None    # "v2" / "v1" / None after a lookup
    confidence: int = 0         # confidence of the matching entry
    db_confidence: int = 0      # highest confidence for this track in the database
    acc: object = None          # scratch state between scan() and finish()
    head: object = None
    tail: object = None


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #

def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def is_glob(s: str) -> bool:
    return any(c in s for c in "*?[")


def expand_inputs(args: list[str], recursive: bool) -> tuple[list[str], list[str]]:
    """Turn files, directories and glob patterns into an ordered file list."""
    files: list[str] = []
    problems: list[str] = []
    for arg in args:
        arg = os.path.expanduser(arg)
        if os.path.isfile(arg):
            files.append(arg)
        elif os.path.isdir(arg):
            found = []
            if recursive:
                for root, _, names in os.walk(arg):
                    found += [os.path.join(root, n) for n in names]
            else:
                found = [os.path.join(arg, n) for n in os.listdir(arg)]
            found = [f for f in found
                     if os.path.isfile(f) and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
            if not found:
                problems.append(f"no audio files found in directory: {arg}")
            files += sorted(found, key=natural_key)
        elif is_glob(arg):
            found = [f for f in glob.glob(arg, recursive=True) if os.path.isfile(f)]
            if not found:
                problems.append(f"pattern matched no files: {arg}")
            files += sorted(found, key=natural_key)
        else:
            problems.append(f"no such file: {arg}")

    seen, unique = set(), []
    for f in files:
        key = os.path.realpath(f)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, problems


# --------------------------------------------------------------------------- #
# Decoding
# --------------------------------------------------------------------------- #

def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries",
         "stream=codec_name,sample_rate,channels,sample_fmt,bits_per_sample,bits_per_raw_sample",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise RuntimeError("no audio stream found")
    return streams[0]


def check_format(info: dict) -> list[str]:
    """Return reasons why the stream is not CD-format lossless audio."""
    issues = []
    codec = info.get("codec_name", "?")
    if not (codec.startswith("pcm_") or codec in LOSSLESS_CODECS):
        issues.append(f"codec '{codec}' is not lossless")
    if int(info.get("sample_rate", 0)) != 44100:
        issues.append(f"sample rate is {info.get('sample_rate')} Hz, not 44100")
    if int(info.get("channels", 0)) != 2:
        issues.append(f"{info.get('channels')} channel(s), not stereo")

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    bits = as_int(info.get("bits_per_raw_sample")) or as_int(info.get("bits_per_sample"))
    if info.get("sample_fmt") not in ("s16", "s16p") and bits != 16:
        issues.append(f"bit depth is {bits or info.get('sample_fmt')}, not 16-bit")
    return issues


def pcm_stream(path: str):
    """Yield the decoded audio as numpy uint32 arrays (L in low 16 bits, R in high)."""
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    leftover = b""
    try:
        while True:
            data = proc.stdout.read(CHUNK_SAMPLES * 4)
            if not data:
                break
            data = leftover + data
            usable = len(data) - (len(data) % 4)
            leftover = data[usable:]
            if usable:
                yield np.frombuffer(data[:usable], dtype="<u4")
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace").strip()
        proc.stderr.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(err or f"ffmpeg exited with status {rc}")
    if leftover:
        raise RuntimeError("decoded stream ended with a partial sample")


# --------------------------------------------------------------------------- #
# Checksum
# --------------------------------------------------------------------------- #

class Accumulator:
    """AccurateRip v1 + v2 sums.

    For every stereo sample (as uint32) at 1-based position m inside the
    checked range:  product = sample * m  (64-bit)
        v1 += low32(product)
        v2 += low32(product) + high32(product)      (all sums mod 2^32)
    """

    def __init__(self):
        self.lo = 0
        self.hi = 0

    def add(self, samples: np.ndarray, first_mult: int, lo: int, hi: int | None) -> None:
        """Add samples whose multipliers are first_mult, first_mult+1, ...;
        only multipliers in [lo, hi] (hi=None: unbounded) are counted."""
        n = len(samples)
        if n == 0:
            return
        a = max(0, lo - first_mult)
        b = n if hi is None else min(n, hi - first_mult + 1)
        if a >= b:
            return
        mult = np.arange(first_mult + a, first_mult + b, dtype=np.uint64)
        prod = samples[a:b].astype(np.uint64) * mult       # < 2^64, no overflow
        self.lo = (self.lo + int((prod & MASK32).sum(dtype=np.uint64))) & 0xFFFFFFFF
        self.hi = (self.hi + int((prod >> np.uint64(32)).sum(dtype=np.uint64))) & 0xFFFFFFFF

    @property
    def v1(self) -> int:
        return self.lo

    @property
    def v2(self) -> int:
        return (self.lo + self.hi) & 0xFFFFFFFF


def check_range(first: bool, last: bool, length: int) -> tuple[int, int]:
    return (FIRST_TRACK_START if first else 1,
            length - LAST_TRACK_SKIP if last else length)


def scan(res: Result, force: bool, offset: int) -> Result:
    """Decode one file once.

    Computes the checksum contribution of the file's own samples with the
    offset applied, and keeps the |offset| samples at its head and tail so the
    neighbouring tracks can borrow them afterwards (see finish()).

    With offset N the corrected track is disc[start+N : start+N+length]:
    own sample i lands at multiplier i - N + 1.
    """
    try:
        info = probe(res.path)
        issues = check_format(info)
        if issues:
            if not force:
                res.error = "; ".join(issues) + " (use --force to convert anyway)"
                return res
            res.warnings.append("converted: " + "; ".join(issues)
                                + " - checksums will not match AccurateRip")

        k = abs(offset)
        hold = LAST_TRACK_SKIP + k   # tail held back until the length is known
        acc = Accumulator()
        lo_bound = FIRST_TRACK_START if res.first else 1
        tail = np.empty(0, dtype=np.uint32)
        head_parts: list[np.ndarray] = []
        head_len = 0
        total = 0                    # own samples already handed to acc / tail
        done = 0                     # own samples already added to acc
        for chunk in pcm_stream(res.path):
            if head_len < k:
                take = chunk[: k - head_len].copy()
                head_parts.append(take)
                head_len += len(take)
            total += len(chunk)
            buf = np.concatenate((tail, chunk)) if len(tail) else chunk
            if len(buf) > hold:
                # Everything here sits safely before the last-track cut-off.
                body = buf[:-hold] if hold else buf
                acc.add(body, done - offset + 1, lo_bound, None)
                done += len(body)
                tail = buf[-hold:].copy() if hold else np.empty(0, dtype=np.uint32)
            else:
                tail = buf.copy()

        length = total
        lo_bound, hi_bound = check_range(res.first, res.last, length)
        acc.add(tail, done - offset + 1, lo_bound, hi_bound)

        res.samples = length
        res.acc = acc
        res.head = np.concatenate(head_parts) if head_parts else np.empty(0, dtype=np.uint32)
        res.tail = tail[-k:] if k else np.empty(0, dtype=np.uint32)
        if length == 0:
            res.warnings.append("file contains no audio")
        elif length % SAMPLES_PER_SECTOR:
            res.warnings.append(
                f"length ({length} samples) is not a whole number of CD sectors; "
                "file may not be an exact CD rip")
        if (res.first or res.last) and length < LAST_TRACK_SKIP + FIRST_TRACK_START:
            res.warnings.append("track shorter than AccurateRip's skipped region")
        if k and length < k:
            res.error = f"track ({length} samples) is shorter than the offset ({offset})"
    except Exception as e:  # noqa: BLE001 - report any failure per file
        res.error = str(e)
    return res


def finish(results: list[Result], offset: int, linked: bool) -> None:
    """Add the samples each track borrows from its neighbour because of the
    offset, then fix the final checksums. Beyond the disc edges (or when the
    neighbour is unknown) the borrowed samples are silence."""
    k = abs(offset)
    for i, r in enumerate(results):
        if r.error:
            continue
        L = r.samples
        lo_bound, hi_bound = check_range(r.first, r.last, L)
        if offset > 0:
            # multipliers L-N+1 .. L come from the start of the next track
            nb = results[i + 1] if linked and i + 1 < len(results) else None
            if nb is not None and not nb.error:
                r.acc.add(nb.head[:k], L - k + 1, lo_bound, hi_bound)
            elif not r.last:
                r.warnings.append(f"next track unavailable; last {k} samples "
                                  "of the offset-corrected track assumed silent")
        elif offset < 0:
            # multipliers 1 .. |N| come from the end of the previous track
            nb = results[i - 1] if linked and i > 0 else None
            if nb is not None and not nb.error:
                r.acc.add(nb.tail[-k:], 1, lo_bound, hi_bound)
            elif not r.first:
                r.warnings.append(f"previous track unavailable; first {k} samples "
                                  "of the offset-corrected track assumed silent")
        r.v1, r.v2 = r.acc.v1, r.acc.v2
    for r in results:
        r.acc = r.head = r.tail = None


# --------------------------------------------------------------------------- #
# AccurateRip database lookup
# --------------------------------------------------------------------------- #

AR_URL = "http://www.accuraterip.com/accuraterip/{a}/{b}/{c}/dBAR-{n:03d}-{id1:08x}-{id2:08x}-{cddb:08x}.bin"


@dataclass
class Lookup:
    status: str                 # "matched" | "partial" | "no-match" | "not-found" | "skipped" | "error"
    message: str
    disc_id: str | None = None
    url: str | None = None


def disc_ids(lengths: list[int], pregap: int,
             data_track: tuple[int, int] | None = None) -> tuple[int, int, int]:
    """AccurateRip id1, id2 and freedb id from audio track lengths in sectors.

    Track offsets are LBAs (track 1 starts at `pregap`, normally 0).
    data_track is (start LBA, disc lead-out LBA) of an enhanced CD's data
    track; it counts towards the freedb id only."""
    offsets, pos = [], pregap
    for n in lengths:
        offsets.append(pos)
        pos += n
    leadout = pos
    cddb_offsets = offsets
    if data_track:
        # AccurateRip counts only audio tracks but uses the disc's real lead-out.
        cddb_offsets, leadout = offsets + [data_track[0]], data_track[1]
    cddb_leadout = leadout
    id1 = (sum(offsets) + leadout) & 0xFFFFFFFF
    id2 = (sum(max(o, 1) * (i + 1) for i, o in enumerate(offsets))
           + leadout * (len(lengths) + 1)) & 0xFFFFFFFF

    def digits(x: int) -> int:
        return sum(int(d) for d in str(x))
    n = sum(digits((o + 150) // 75) for o in cddb_offsets)
    t = (cddb_leadout + 150) // 75 - (cddb_offsets[0] + 150) // 75
    cddb = ((n % 255) << 24) | (t << 8) | len(cddb_offsets)
    return id1, id2, cddb


def parse_db(data: bytes) -> list[list[tuple[int, int]]]:
    """Parse a dBAR .bin file into blocks of (confidence, crc) per track."""
    blocks, pos = [], 0
    while pos + 13 <= len(data):
        ntracks = data[pos]
        pos += 13                                  # count + id1 + id2 + cddb
        entries = []
        for _ in range(ntracks):
            if pos + 9 > len(data):
                raise ValueError("truncated AccurateRip response")
            conf, crc, _frame450 = struct.unpack_from("<BII", data, pos)
            entries.append((conf, crc))
            pos += 9
        blocks.append(entries)
    if not blocks:
        raise ValueError("empty AccurateRip response")
    return blocks


def lookup(results: list[Result], pregap: int, data_track: tuple[int, int] | None,
           db_file: str | None, timeout: float) -> Lookup:
    lengths = []
    for r in results:
        lengths.append(-(-r.samples // SAMPLES_PER_SECTOR))   # round up to sectors
    id1, id2, cddb = disc_ids(lengths, pregap, data_track)
    n = len(results)
    disc_id = f"{n:03d}-{id1:08x}-{id2:08x}-{cddb:08x}"
    url = AR_URL.format(a=f"{id1 & 0xF:x}", b=f"{id1 >> 4 & 0xF:x}", c=f"{id1 >> 8 & 0xF:x}",
                        n=n, id1=id1, id2=id2, cddb=cddb)
    try:
        if db_file:
            with open(db_file, "rb") as fh:
                data = fh.read()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": f"veripfy/{__version__}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        blocks = parse_db(data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return Lookup("not-found", "disc not found in the AccurateRip database", disc_id, url)
        return Lookup("error", f"lookup failed: HTTP {e.code}", disc_id, url)
    except (OSError, ValueError) as e:
        return Lookup("error", f"lookup failed: {e}", disc_id, url)

    matched = 0
    for i, r in enumerate(results):
        entries = [b[i] for b in blocks if i < len(b)]
        r.db_confidence = max((c for c, _ in entries), default=0)
        for version, crc in (("v2", r.v2), ("v1", r.v1)):
            confs = [c for c, x in entries if x == crc]
            if confs:
                r.match, r.confidence = version, max(confs)
                break
        matched += r.match is not None

    if matched == n:
        return Lookup("matched", f"all {n} tracks accurately ripped", disc_id, url)
    if matched:
        return Lookup("partial", f"{matched} of {n} tracks accurately ripped", disc_id, url)
    return Lookup("no-match", "no tracks matched the AccurateRip database "
                  "(wrong offset correction, a different pressing, or rip errors)", disc_id, url)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def fmt_len(samples: int) -> str:
    secs, rem = divmod(samples, 44100)
    m, s = divmod(secs, 60)
    frames = rem // SAMPLES_PER_SECTOR
    return f"{m:d}:{s:02d}.{frames:02d}"


def print_table(results: list[Result], use_color: bool, full_path: bool, offset: int,
                lk: Lookup | None) -> None:
    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if use_color else s

    looked_up = lk is not None and lk.status in ("matched", "partial", "no-match")
    print(f"Offset correction: {offset:+d} samples" if offset else "Offset correction: none")
    hdr = f"{'Track':>5}  {'Length':>9}  {'AR v1':8}  {'AR v2':8}  "
    if looked_up:
        hdr += f"{'AccurateRip':<20}  "
    print(c("1", hdr + "File"))
    for r in results:
        name = r.path if full_path else os.path.basename(r.path)
        pos = "F" if r.first and not r.last else "L" if r.last and not r.first else \
              "FL" if r.first and r.last else ""
        track = f"{r.track:>3}{pos:<2}"
        if r.error:
            pad = f"{'':20}  " if looked_up else ""
            print(f"{track}  {'':>9}  {c('31', 'ERROR')}     {'':8}  {pad}{name}")
            print(c("31", f"{'':7}{r.error}"))
            continue
        line = f"{track}  {fmt_len(r.samples):>9}  {r.v1:08X}  {r.v2:08X}  "
        if looked_up:
            if r.match:
                cell = c("32", f"{'match ' + r.match + ' conf ' + str(r.confidence):<20}")
            else:
                cell = c("31", f"{'no match (db ' + str(r.db_confidence) + ')':<20}")
            line += cell + "  "
        print(line + name)
        for w in r.warnings:
            print(c("33", f"{'':7}warning: {w}"))

    if lk is not None:
        colour = {"matched": "32", "partial": "33", "no-match": "31",
                  "not-found": "33", "error": "31", "skipped": "33"}[lk.status]
        print()
        if lk.disc_id:
            print(f"AccurateRip disc ID: {lk.disc_id}")
        print(c(colour, f"AccurateRip: {lk.message}"))


def print_json(results: list[Result], offset: int, lk: Lookup | None) -> None:
    out = []
    for r in results:
        out.append({
            "file": r.path,
            "track": r.track,
            "first_track": r.first,
            "last_track": r.last,
            "samples": r.samples if not r.error else None,
            "accuraterip_v1": f"{r.v1:08X}" if r.v1 is not None else None,
            "accuraterip_v2": f"{r.v2:08X}" if r.v2 is not None else None,
            "match": r.match,
            "confidence": r.confidence if r.match else None,
            "db_confidence": r.db_confidence if lk and lk.status in
                             ("matched", "partial", "no-match") else None,
            "warnings": r.warnings,
            "error": r.error,
        })
    doc = {"offset": offset, "tracks": out}
    if lk is not None:
        doc["lookup"] = {"status": lk.status, "message": lk.message,
                         "disc_id": lk.disc_id, "url": lk.url}
    json.dump(doc, sys.stdout, indent=2)
    print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="veripfy",
        description="Compute AccurateRip v1 and v2 checksums for lossless audio files "
                    "and verify them against the AccurateRip database.",
        epilog=(
            "With no inputs, veripfy scans the current directory (add -r to include "
            "subdirectories). "
            "Inputs may be files, directories, or glob patterns (quote them to let veripfy "
            "expand them, e.g. '*.flac' or '**/*.flac'). Files are treated as one album in "
            "the order given (glob/directory matches are sorted naturally); in the output "
            "F marks the first track and L the last. Supported: anything ffmpeg decodes "
            "losslessly - FLAC, WAV, W64, RF64, AIFF, ALAC, WavPack, APE, TTA, TAK. "
            "The database lookup needs the complete disc (every track, in order). "
            "Exit status: 0 ok, 1 a file failed, 2 bad usage, "
            "3 lookup done but not every track matched."
        ),
    )
    p.add_argument("inputs", nargs="*", metavar="FILE|DIR|GLOB",
                   help="audio files, directories or glob patterns "
                        "(default: the current directory)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tracks", action="store_true",
                      help="treat every file as a middle track (no first/last skipping)")
    mode.add_argument("--single", action="store_true",
                      help="treat every file as its own disc (first and last skipping on each)")
    p.add_argument("--first", type=int, metavar="N", default=None,
                   help="track number of the first file (default 1)")
    p.add_argument("--total", type=int, metavar="N", default=None,
                   help="total tracks on the disc, if the files are only part of it")
    p.add_argument("-o", "--offset", type=int, default=0, metavar="SAMPLES",
                   help="offset correction in samples applied before checksumming. "
                        "Positive N shifts the audio N samples earlier (the corrected track "
                        "starts N samples later in the disc stream); negative shifts it later. "
                        "Samples cross into the adjacent file; beyond the disc edges, silence.")
    p.add_argument("-n", "--no-lookup", action="store_true",
                   help="don't query the AccurateRip database, only print the checksums")
    p.add_argument("--pregap", type=int, default=0, metavar="SECTORS",
                   help="start sector of track 1 for the disc ID (hidden track one audio "
                        "length, if the rip excluded it; default 0)")
    p.add_argument("--data-track", metavar="START:LEADOUT",
                   help="enhanced CD: start sector (LBA) of the data track after the audio "
                        "and the disc's lead-out sector, as shown by e.g. cd-info or the "
                        "rip log; needed for the disc ID")
    p.add_argument("--db-file", metavar="PATH",
                   help="use a downloaded dBAR-*.bin file instead of querying the server")
    p.add_argument("--timeout", type=float, default=15, metavar="SECONDS",
                   help="network timeout for the lookup (default 15)")
    p.add_argument("-r", "--recursive", action="store_true",
                   help="descend into subdirectories of directory arguments")
    p.add_argument("-s", "--sort", action="store_true",
                   help="sort all files naturally by path instead of keeping argument order")
    p.add_argument("-f", "--force", action="store_true",
                   help="process non-CD-format files by converting to 44.1kHz/16-bit/stereo")
    p.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1,
                   help="files to process in parallel (default: CPU count)")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--full-path", action="store_true", help="show full paths in table output")
    p.add_argument("--no-color", action="store_true", help="disable coloured output")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)

    data_track = None
    if a.data_track:
        try:
            start, end = (int(x) for x in a.data_track.split(":"))
            data_track = (start, end)
        except ValueError:
            p.error("--data-track expects START:LEADOUT, e.g. 207106:210385")

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"veripfy: '{tool}' not found in PATH; please install ffmpeg", file=sys.stderr)
            return 2

    if not a.inputs:
        print(f"veripfy: no files given; scanning {os.getcwd()}"
              + (" and its subdirectories" if a.recursive else ""), file=sys.stderr)
    files, problems = expand_inputs(a.inputs or ["."], a.recursive)
    for msg in problems:
        print(f"veripfy: {msg}", file=sys.stderr)
    if not files:
        return 2
    if a.sort:
        files.sort(key=natural_key)

    first_no = a.first or 1
    total = a.total or (first_no + len(files) - 1)
    if total < first_no + len(files) - 1:
        print("veripfy: --total is smaller than the number of tracks given", file=sys.stderr)
        return 2

    results = []
    for i, f in enumerate(files):
        n = first_no + i
        if a.tracks:
            first = last = False
        elif a.single:
            first = last = True
        else:
            first, last = n == 1, n == total
        results.append(Result(path=f, track=n, first=first, last=last))

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(lambda r: scan(r, a.force, a.offset), results))
    finish(results, a.offset, linked=not a.single)

    # The checksums are complete at this point; the lookup only adds to them.
    lk = None
    if not a.no_lookup or a.db_file:
        if a.tracks or a.single:
            lk = Lookup("skipped", "lookup skipped: needs album mode (not --tracks/--single)")
        elif first_no != 1 or total != len(files):
            lk = Lookup("skipped", "lookup skipped: needs every track of the disc")
        elif any(r.error for r in results):
            lk = Lookup("skipped", "lookup skipped: some files could not be read")
        else:
            lk = lookup(results, a.pregap, data_track, a.db_file, a.timeout)

    if a.json:
        print_json(results, a.offset, lk)
    else:
        use_color = not a.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        print_table(results, use_color, a.full_path, a.offset, lk)

    if problems or any(r.error for r in results):
        return 1
    if lk is not None and lk.status in ("partial", "no-match"):
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
