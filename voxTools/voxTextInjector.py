"""
Adapted from Green Goblins scripts. 
This is really heavily based on his awesome work. 

Script for working with Metal Gear Solid data

Copyright (C) 2023 Green_goblin (https://mgsvm.blogspot.com/)

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

"""

import os, sys
sys.path.append(os.path.abspath('./myScripts'))
import re
import glob
import struct
import progressbar
import translation.radioDict as RD
import json

import voxTools.voxTextExtractor as DTE # Leave for referential
from common.structs import subtitle

version = "usa"
version = "jpn"
disc = 1

# Toggles
debug = True

# Directory configs
inputDir = f'workingFiles/{version}-d{disc}/vox/bins'
outputDir = f'workingFiles/{version}-d{disc}/vox/newBins'
injectJson = f'build-proprietary/vox/voxText-{version}-d{disc}.json'
os.makedirs(outputDir, exist_ok=True)

# Collect files to use
bin_files = glob.glob(os.path.join(inputDir, '*.vox'))
bin_files.sort(key=lambda f: int(f.split('-')[-1].split('.')[0]))

# Collect source json to inject
injectTexts = json.load(open(injectJson, 'r'))


# Defs below

def assembleTitles(texts: dict, timings: dict) -> list [subtitle]:
    subsList = []
    for i in range(len(texts)):
        index = "{:02}".format(i + 1)
        start = timings.get(index).split(",")[0]
        duration = timings.get(index).split(",")[1]
        a = subtitle(texts.get(index), start, duration)
        subsList.append(a)
    
    return subsList

def genSubBlock(subs: list [subtitle] ) -> bytes:
    """
    Injects the new text to the original data, returns the bytes. 
    Also returns the index we were at when we finished. 

    """ 
    newBlock = b''
    for i in range(len(subs) -1):
        length = struct.pack("I", len(bytes(subs[i])) + 4)
        newBlock += length + bytes(subs[i])
    
    # Add the last one
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

def getvoxDiagHeader(data: bytes) -> bytes:
    """
    Returns the header portion only for a given dialogue section.
    """
    headerLength = struct.unpack("H", data[14:16])[0] + 4
    return data[:headerLength]

# if debug:
#     print(f'Only injecting vox 29!')
#     bin_files = ['workingFiles/jpn-d1/vox/bins/vox-0029.bin']

if __name__ == "__main__":
    """
    Main logic is here.
    """
    for file in bin_files:
        print(os.path.basename(f"{file}: "), end="")
        filename = os.path.basename(file)
        basename = filename.split(".")[0]

        # if injectTexts[basename] is None:
        if basename not in injectTexts:
            print(f'{basename} was not in the json. Skipping...\r', end="")
            continue
        
        # Initialize the vox data and the dictionary we're using to replace it.
        origvoxData = open(file, 'rb').read()
        origBlocks = len(origvoxData) // 0x800 # Use this later to check we hit the same length!
        voxDict: dict = injectTexts[basename][0]
        voxTimings: dict = injectTexts[basename][1]
        
        subtitles = assembleTitles(voxDict, voxTimings)

        offsets = DTE.getTextAreaOffsets(origvoxData)
        # nextStart = 1 # index of subtitle to encode. No longer needed.
        newvoxData = origvoxData[0 : offsets[0]] # UNTIL the header
        
        for Num in range(len(offsets)):
            oldHeader = getvoxDiagHeader(origvoxData[offsets[Num]:])
            oldLength = struct.unpack("H", oldHeader[1:3])[0]
            frameStart = struct.unpack("I", oldHeader[4:8])[0]
            frameLimit = struct.unpack("I", oldHeader[8:12])[0]
            # Get only subtitles in this section.
            subsForSection = []
            for sub in subtitles:
                if frameStart <= sub.startFrame < frameLimit:
                    subsForSection.append(sub)
            newSubBlock = genSubBlock(subsForSection) # TODO: CODE THIS DEF
            newLength = len(oldHeader) + len(newSubBlock)

            newHeader = bytes.fromhex("03") + struct.pack("H", newLength) + bytes(1) + struct.pack("II", frameStart, frameLimit) + oldHeader[12:16] + struct.pack("I", len(oldHeader) + len(newSubBlock) - 4) + oldHeader[20:]
            newvoxData += newHeader + newSubBlock
            # Add the rest of the data from this to the next offset OR until end of original vox. 
            if Num < len(offsets) - 1: # if it is NOT the last... 
                newvoxData += origvoxData[offsets[Num] + oldLength: offsets[Num + 1]]
            else:
                newvoxData += origvoxData[offsets[Num] + oldLength: ]
            # if debug:
            #     print(newSubBlock.hex(sep=" ", bytes_per_sep=4))
        
        """# Buffer the vox to 0x800 block
        if len(newvoxData) % 0x800 != 0:
            if len(newvoxData) // 0x800 < len(origvoxData) // 0x800:
                newvoxData += bytes(len(newvoxData) % 0x800)
            else:
                checkBytes = newvoxData[len(newvoxData) - len(origvoxData):]
                if checkBytes == bytes(len(checkBytes)):
                    newvoxData = newvoxData[:len(newvoxData) - len(checkBytes)]"""
        
        # Adjust length to match original file, or align to 0x800 if it grew.
        if len(newvoxData) == len(origvoxData):
            print("Alignment correct!")
        elif len(newvoxData) < len(origvoxData): # new vox shorter
            newvoxData += bytes(len(origvoxData) - len(newvoxData))
            if len(newvoxData) % 0x800 == 0:
                print("Alignment correct!")
        else: # New file is longer
            checkBytes = newvoxData[len(origvoxData):]  # Only the excess bytes
            if checkBytes == bytes(len(checkBytes)):
                # All extra bytes are null — safe to truncate back to original size
                newvoxData = newvoxData[:len(origvoxData)]
                print("Alignment correct! (trimmed trailing nulls)")
            else:
                # Legitimately larger; align to 0x800 boundary
                print(f'WARNING: New vox is larger than original ({len(newvoxData)} > {len(origvoxData)})')
                if len(newvoxData) % 0x800 != 0:
                    paddingNeeded = 0x800 - (len(newvoxData) % 0x800)
                    newvoxData += bytes(paddingNeeded)
                    print(f'  Added {paddingNeeded} bytes padding -> {len(newvoxData)} bytes ({len(newvoxData) // 0x800} blocks)')
                print(f'  STAGE.DIR offsets will need updating!')

        newBlocks = len(newvoxData) // 0x800
        if newBlocks != origBlocks:
            print(f"  {basename}: {len(newvoxData)} / {len(origvoxData)}")
            print(f'  BLOCK MISMATCH: New={newBlocks} blocks, Old={origBlocks} blocks.')

        # Finished work! Write the new file. 
        newFile = open(f'{outputDir}/{basename}.vox', 'wb')
        newFile.write(newvoxData)
        newFile.close()
        print(f'VOX Data successfully Output to new files!')




    """
    # not really needed just for reference.
    for key in injectTexts:
        print(key)
        voxDict: dict = injectTexts[key]
        
    """