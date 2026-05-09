"""
Standalone diagnostic: extract all ZMOVIE.STR entries to .str (raw CD sectors)
and convert each to .mp4 via ffmpeg.

Mirrors ZmovieConversionThread in src/mainwindow.py but loops over every
entry sequentially and prints ffmpeg stderr so we can see where conversion
breaks.

Usage:
    python exportAllZmovies.py /path/to/ZMOVIE.STR [output_dir]

If output_dir is omitted, files are written next to ZMOVIE.STR in
./zmovie_export/.
"""

import os
import sys
import argparse
import subprocess

_here    = os.path.dirname(os.path.abspath(__file__))
_scripts = os.path.dirname(_here)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from zmovieTools import extractZmovie as ZM


def convertEntry(zmovieData: bytes, entryIndex: int, outputDir: str) -> bool:
    strPath = os.path.join(outputDir, f"zmovie_{entryIndex:02}.str")
    mp4Path = os.path.join(outputDir, f"zmovie_{entryIndex:02}.mp4")

    print(f"\n=== Entry {entryIndex} ===")
    print(f"  Extracting raw sectors -> {strPath}")
    try:
        ZM.extractEntryVideo(zmovieData, entryIndex, strPath)
    except Exception as e:
        print(f"  [extractEntryVideo FAILED] {e}")
        return False

    size = os.path.getsize(strPath)
    print(f"  Wrote {size} bytes ({size // 2352} sectors)")

    cmd = ['ffmpeg', '-y', '-i', strPath, mp4Path]
    print(f"  Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  [ffmpeg not found on PATH]")
        return False

    if proc.returncode == 0:
        mp4Size = os.path.getsize(mp4Path) if os.path.exists(mp4Path) else 0
        print(f"  OK -> {mp4Path} ({mp4Size} bytes)")
        return True

    print(f"  [ffmpeg exit {proc.returncode}]")
    print("  --- ffmpeg stderr ---")
    for line in proc.stderr.splitlines()[-40:]:
        print(f"    {line}")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zmovie_path', help='Path to ZMOVIE.STR')
    ap.add_argument('output_dir', nargs='?', default=None,
                    help='Output directory (default: ./zmovie_export next to ZMOVIE.STR)')
    args = ap.parse_args()

    if not os.path.isfile(args.zmovie_path):
        print(f"ERROR: file not found: {args.zmovie_path}")
        return 1

    outputDir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.zmovie_path)), 'zmovie_export')
    os.makedirs(outputDir, exist_ok=True)
    print(f"Output dir: {outputDir}")

    with open(args.zmovie_path, 'rb') as f:
        zmovieData = f.read()
    print(f"Loaded {len(zmovieData)} bytes from {args.zmovie_path}")

    offsets = ZM.getEntryOffsets(zmovieData)
    offsets.append(len(zmovieData))
    print(f"Entry offsets (block_num * 0x920): {[hex(o) for o in offsets]}")

    succeeded = 0
    for i in range(ZM.NUM_ENTRIES):
        if convertEntry(zmovieData, i, outputDir):
            succeeded += 1

    print(f"\n=== Summary: {succeeded}/{ZM.NUM_ENTRIES} entries converted ===")
    return 0 if succeeded == ZM.NUM_ENTRIES else 2


if __name__ == "__main__":
    sys.exit(main())
