"""
Adapted from demo files.
Very special thanks for the groundwork on this to Green_goblin
Copyright (C) 2023 Green_goblin (https://mgsvm.blogspot.com/)

"""

import os, sys
sys.path.append(os.path.abspath('./myScripts'))
import re
import glob
import struct
import argparse
import progressbar
import translation.radioDict as RD
import json

import DemoTools.demoTextExtractor as DTE
from common.structs import subtitle

parser = argparse.ArgumentParser(description='Assemble ZMOVIE.STR from bin files, optionally injecting new subtitles.')
parser.add_argument('input', type=str, help='Input directory containing .bin files (from movieSplitter.py).')
parser.add_argument('output', type=str, help='Output ZMOVIE.STR file path.')
parser.add_argument('-s', '--source', type=str, required=True, help='Original ZMOVIE.STR (used for the file header).')
parser.add_argument('-j', '--inject', nargs='?', type=str, help='JSON file containing subtitle data to inject. If omitted, all bins are passed through unchanged.')
parser.add_argument('-n', '--new-bins', nargs='?', type=str, help='Output directory for individually modified .bin files.')

# Toggles
debug = True


def assembleTitles(texts: dict, timings: dict) -> list [subtitle]:
    subsList = []
    for i in range(len(texts)):
        index = "{:02}".format(i + 1)
        start = timings.get(index).split(",")[0]
        duration = timings.get(index).split(",")[1]
        a = subtitle(texts.get(index), start, duration)
        subsList.append(a)

    return subsList

"""
# TODO:
- change key to int
- make sure range hits all texts
"""

def genSubBlock(subs: list [subtitle] ) -> bytes:
    """
    Injects the new text to the original data, returns the bytes.
    Also returns the index we were at when we finished.

    """
    newBlock = b''
    for i in range(len(subs)):
        length = struct.pack("I", len(bytes(subs[i])) + 4)
        newBlock += length + bytes(subs[i])

    # Add the last one
    if len(subs) == 1:
        newBlock += bytes(4) + bytes(subs[0])
    else:
        newBlock += bytes(4) + bytes(subs[-1])

    return newBlock

def injectSubtitles(originalBinary: bytes, newTexts: dict, frameLimit: int = 1, timings: dict = None) -> bytes:
    """
    Injects the new text to the original data, returns the bytes.
    Also returns the index we were at when we finished.

    New vers: Framelimit is the end of a cutscene segment.
    """

    def encodeNewText(text: str, timing: str):
        """
        Simple. Encodes the dialogue as bytes.
        Adds the buffer we need to be divisible by 4...
        Return the new bytes.
        """
        timings = int(timing.split(','))
        start = timings[0]
        duration = timings[1]

        subtitleBytes: bytes = struct.pack("III", start, duration, 0)
        subtitleBytes += RD.encodeJapaneseHex(text, bank=3)[0]
        bufferNeeded = 4 - (len(subtitleBytes) % 4)
        for j in range(bufferNeeded):
            newBytes += b'\x00'
            j += 1

        return subtitleBytes



    newBytes = b""
    firstLengthBytes = originalBinary[18:20]
    firstLength = struct.unpack('<H', firstLengthBytes)[0]
    offset = 8 + firstLength # This is our starting point for the dialogue.

    newBytes += originalBinary[0: offset]

    # i = startingNum
    while i <= len(newTexts):
        start, duration = timings.get(f"{i}").split(",")
        start = int(start)
        duration = int(duration)
        if originalBinary[offset] == 0x00:
            # Find the length here (This is stupid!)
            origTextData = originalBinary[offset: offset + originalBinary.find(b'\x00', offset + 16)] # We can add the buffer later
            bufferNeeded = 4 - (len(origTextData) % 4)
            origTextLength = len(origTextData) + bufferNeeded
            origTextData = originalBinary[offset: offset + origTextLength]

            # Now create the new one.
            newText = encodeNewText(newTexts[str(i)])
            newBytes = newBytes + origTextData[0:4] + struct.pack("<I", start) + struct.pack("<I", duration) + origTextData[12:16] + newText
            i += 1
            offset += origTextLength
            break
        else:
            origLength = originalBinary[offset]
            origTextData = originalBinary[offset: offset + origLength]
            origTextLength = len(origTextData)
            # New Text
            newText = encodeNewText(newTexts[str(i)])
            newLength = len(newText) + 16
            newBytes += newLength.to_bytes() + origTextData[1:4] + struct.pack("<I", start) + struct.pack("<I", duration) + origTextData[12:16] + newText

            i += 1
            offset += origTextLength

    return newBytes

# def getDemoDiagHeader(data: bytes) -> bytes:
#     """
#     Returns the header portion only for a given dialogue section.
#     """
#     headerLength = struct.unpack("H", data[14:16])[0] + 4
#     return data[:headerLength]

def main(args=None):
    if args is None:
        args = parser.parse_args()

    inputDir = args.input
    outputFile = args.output
    sourceFile = args.source
    newBinsDir = args.new_bins if args.new_bins else os.path.join(inputDir, '../newBins')

    os.makedirs(newBinsDir, exist_ok=True)
    os.makedirs(os.path.dirname(outputFile) if os.path.dirname(outputFile) else '.', exist_ok=True)

    bin_files = glob.glob(os.path.join(inputDir, '*.bin'))
    bin_files.sort(key=lambda f: int(f.split('-')[-1].split('.')[0]))

    injectTexts = json.load(open(args.inject, 'r')) if args.inject else {}

    completeFile = b''
    # Get original file header
    completeFile += open(sourceFile, 'rb').read(0x920)

    # Build zmovie files
    for file in bin_files:
        print(os.path.basename(f"{file}: "), end="")
        filename = os.path.basename(file)
        basename = filename.split(".")[0]

        if basename not in injectTexts:
            # Passthrough: no injection data for this bin, use original unchanged
            print(f'{basename}: Passing through...\r', end="")
            completeFile += open(file, 'rb').read()
            continue

        # Initialize the demo data and the dictionary we're using to replace it.
        origDemoData = open(file, 'rb').read()
        origBlocks = len(origDemoData) // 0x920 # Use this later to check we hit the same length!

        demoDict: dict = injectTexts[basename][0]
        demoTimings: dict = injectTexts[basename][1]

        subtitles = assembleTitles(demoDict, demoTimings)
        newDemoData = origDemoData[0 : 0x38] # Static start point
        # Encode and add subtitles
        newDemoData += genSubBlock(subtitles)
        # Pad length to 0x800:
        newDemoData += bytes(0x800 - len(newDemoData))
        # Add the rest of original zmovie data
        newDemoData += origDemoData[0x800:]

        # Finished work! Write the new file.
        newFile = open(f'{newBinsDir}/{basename}.bin', 'wb')
        newFile.write(newDemoData)
        newFile.close()
        completeFile += newDemoData

    print(f'New Demo Files have been injected!')
    # Since we're here, write the whole zmovie file to disk!
    with open(outputFile, 'wb') as out:
        out.write(completeFile)

if __name__ == "__main__":
    args = parser.parse_args()
    main(args)
