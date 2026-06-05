"""
Adapted from Green Goblins scripts. Very similar to demo
only alignments are 0x920
"""

import os, struct, sys, glob, json, argparse
sys.path.append(os.path.abspath('./myScripts'))
sys.path.append(os.path.abspath('.'))
import DemoTools.demoTextExtractor as DTE

parser = argparse.ArgumentParser(description='Split ZMOVIE.STR into individual .bin files.')
parser.add_argument('filename', type=str, help='Input ZMOVIE.STR file to split.')
parser.add_argument('output', nargs='?', type=str, help='Output directory for .bin files and extracted text.')

BLOCK_SIZE   = 0x920
UNKNOWN_TAIL = 0x118
GRAPHICS_END = BLOCK_SIZE - UNKNOWN_TAIL  # 0x808

def getOffsets(toc: bytes) -> list:
    demoNum = 4 # If we figure out where this is we can implement it.
    offsets = []
    counter = 16
    for i in range(demoNum):
        offset = struct.unpack("<I", toc[counter : counter + 4])[0]
        offsets.append(offset * BLOCK_SIZE)
        counter += 8
    return offsets

def getSubtitleSubset(movieData: bytes) -> bytes:
    """
    Subtitle block is always block 0. bytes[0x0E:0x10] = chunk_cnt.
    If chunk_cnt == 2, graphics overflow into block 1 starting at 0x28.
    Subtitle entries begin at 0x38; we pass everything up to GRAPHICS_END
    per block so getTextHexes can split text from graphics naturally.
    """
    block0 = movieData[0:BLOCK_SIZE]
    chunk_cnt = struct.unpack('<H', block0[0x0E:0x10])[0]

    subset = block0[0x38:GRAPHICS_END]

    if chunk_cnt == 2:
        block1 = movieData[BLOCK_SIZE:2 * BLOCK_SIZE]
        subset += block1[0x28:GRAPHICS_END]

    return subset

def main(args=None):
    if args is None:
        args = parser.parse_args()

    filename = args.filename
    outputDir = args.output if args.output else os.path.join(os.path.dirname(os.path.abspath(filename)), '../zmovie')

    with open(filename, 'rb') as zmFile:
        zmData = zmFile.read()

    os.makedirs(os.path.join(outputDir, 'bins'), exist_ok=True)
    os.makedirs(os.path.join(outputDir, 'texts'), exist_ok=True)

    zMovieScript = {}

    movieOffsets = getOffsets(zmData[0:BLOCK_SIZE])
    movieOffsets.append(len(zmData))
    print(movieOffsets)

    for i in range(len(movieOffsets) - 1):
        # Write the output movie file
        with open(f'{outputDir}/bins/zmovie-{i:02}.bin', 'wb') as f:
            start = movieOffsets[i]
            end = movieOffsets[i + 1]
            f.write(zmData[start : end])

    bin_files = glob.glob(os.path.join(f"{outputDir}/bins", '*.bin'))

    bin_files.sort(key=lambda f: int(f.split('/')[-1].split('-')[1].split(".")[0]))

    for bin_file in bin_files:
        with open(bin_file, 'rb') as movieTest:
            fname = os.path.basename(bin_file)
            DTE.filename = fname
            movieData = movieTest.read()

        basename = fname.split('.')[0]

        # Skip entries whose block 0 isn't a subtitle metadata block
        # (e.g. d2 zmovie-03, where block 0 is XA stream data).
        mini_hdr = struct.unpack('<H', movieData[0x32:0x34])[0]
        sub_len  = struct.unpack('<I', movieData[0x34:0x38])[0]
        if mini_hdr != 0x0010 or sub_len >= 0x800:
            print(f'{basename}: no subtitle block (mini_hdr=0x{mini_hdr:04X}), skipping')
            continue

        subset = getSubtitleSubset(movieData)
        textHexes, graphicsBytes, coords = DTE.getTextHexes(subset)
        texts = DTE.getDialogue(textHexes, graphicsBytes)
        timings = coords

        zMovieScript[basename] = [DTE.textToDict(texts), DTE.textToDict(timings)]
        DTE.writeTextToFile(f'{outputDir}/texts/{basename}.txt', texts)

    with open(f'{outputDir}/zMovie-out.json', 'w') as f:
        json.dump(zMovieScript, f, ensure_ascii=False)

if __name__ == "__main__":
    args = parser.parse_args()
    main(args)
