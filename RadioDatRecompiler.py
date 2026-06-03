"""
Recompiler for MGS RADIO.DAT files. This requires exported Radio XML and .json files created by RadioDatTools.py

Steps to use for translation:

1. Export XML and Json from RadioDatTools from source disk RADIO.DAT
2. Adjust dialogue in the .json file.
3. Run script with both files as arguments. 

We will output to RADIO.DAT or optionally a filename of your choice. 

To do list:
TODO: ~~Element checker~~ I think this is done
TODO: Need logic to re-insert b'\x80' before each punctuation mark that needs it (new lines and japanese supertext pairs {a, b})
TODO: Calculate double byte length characters for length in subtitles
TODO: Remove 'content' attribute passthrough from all elements (VOX_CUES, ANI_FACE, ADD_FREQ, etc.) —
      content is retained in the XML for reference/diffing but all sizes should be recalculated from
      labeled fields at recompile time. Affects both extractor (RadioDatTools.py) and recompiler.

"""

stageBytes: bytearray = b''
debug = False
# ROUND_TRIP: when True, FF01 uses original textHex and FF04 uses verbatim content passthrough.
# Set automatically: True when recompiling as-extracted (no --prepare); False in translation
# mode (--prepare re-encodes text, so getSubtitleBytes must re-encode too).
ROUND_TRIP = False

# ==== Dependencies ==== #

import os, struct, math
import argparse
import xml.etree.ElementTree as ET
import StageDirTools.callsInStageDirFinder as stageTools
import translation.radioDict as RD
import xmlModifierTools as xmlFix

# Debugging for testing calls recompile with correct info
subUseOriginalHex = False
useDWidSaveB = False

# Format flags
USE_LONG = False        # Set True when targeting the patched executable (4-byte size fields)
INTEGRAL = False        # Set True for Integral disc: 0x800-aligned calls, 2-byte block index in stage dir
PAD = False             # Set True to align each call to 0x800 boundaries (set by --pad or --integral)
currentCallDict = ''    # Graphics bytes dict for the current call; updated in main loop

def createLength(payload_size: int) -> bytes:
    """
    Returns the binary size field bytes for a given payload size.
    The stored value includes the size field itself:
      USE_LONG=False → '>H'  value = 2 + payload_size  (original binary)
      USE_LONG=True  → '>L'  value = 4 + payload_size  (patched binary)
    """
    if USE_LONG:
        return struct.pack('>L', 4 + payload_size)
    else:
        try:
            return struct.pack('>H', 2 + payload_size)
        except struct.error as a:
            print(f"[createLength] Size is too large! {a}")
            return bytes.fromhex('FFFF')  # Return max size


newOffsets = {}
stageDirFilename = 'radioDatFiles/STAGE-jpn-d1.DIR' # Deprecated

# ==== DEFS ==== #

# Large steps here
def realignOffsets():
    # TODO: Wtf is this?
    print(f'Offset integrity reviewed and done')

def createBinary(filename: str, binaryData: bytes):
    with open(filename, 'wb') as f:
        f.write(binaryData)
        f.close
    print(f'Binary data created: {filename}, size: {len(binaryData)}!')

# ==== Byte Encoding Defs ==== #

def getFreqbytes(freq: str) -> bytes:
    frequency = round(float(freq) * 100)
    freqBytes = struct.pack('>H', frequency)
    return freqBytes

def getCallHeaderBytes(call: ET.Element) -> bytes:
    """
    Returns the bytes used for a call header. Should always return bytes object with length 11.
    """
    callHeader = b''
    attrs = call.attrib
    freq = getFreqbytes(attrs.get('Freq'))
    unk1 = bytes.fromhex(attrs.get("UnknownVal1"))
    unk2 = bytes.fromhex(attrs.get("UnknownVal2"))
    unk3 = bytes.fromhex(attrs.get("UnknownVal3"))
    lengthBytes = struct.pack('>H', int(attrs.get("Length")) - 9) # The header is 11 bytes, not in the length as shown in bytes

    # Assemble all pieces
    callHeader = freq + unk1 + unk2 + unk3 + bytes.fromhex('80') + lengthBytes
    return callHeader

def getSubtitleBytes(subtitle: ET.Element) -> bytes:
    """
    Returns the bytes for a SUBTITLE command (FF 01).
    ROUND_TRIP=True (default): uses original textHex verbatim — byte-for-byte accurate.
    ROUND_TRIP=False (--prepare / translation mode): re-encodes text via encodeJapaneseHex;
      output may differ from the original where multiple encodings are valid.
    """
    global currentCallDict

    attrs = subtitle.attrib
    face = bytes.fromhex(attrs.get("face"))
    anim = bytes.fromhex(attrs.get("anim"))
    unk3 = bytes.fromhex(attrs.get("unk3"))

    if ROUND_TRIP:
        text_bytes = bytes.fromhex(attrs.get('textHex'))
    else:
        text_bytes, _ = RD.encodeJapaneseHex(attrs.get('text'), currentCallDict, useDoubleLength=useDWidSaveB)
        # Restore in-game newline bytes if the text string contains literal \r\n escapes
        text_bytes = text_bytes.replace(bytes.fromhex('5c725c6e'), bytes.fromhex('8023804e'))
        text_bytes += b'\x00'

    payload = face + anim + unk3 + text_bytes # Null added above
    total = b'\xff\x01' + createLength(len(payload)) + payload
    return total

def getVoxBytes(vox: ET.Element) -> bytes:
    """
    Returns the bytes for a VOX_CUES container (FF 02).
    Structure: FF02 [outer_size] [vox_code 4B] 80 [inner_size] [SUBTITLE children...] 00
    Sizes are recalculated from actual content; content attr is preserved in XML for reference only.
    """
    attrs = vox.attrib
    vox_code = bytes.fromhex(attrs.get('voxCode'))

    inner = b''
    for child in vox:
        inner += handleElement(child)
    inner += b'\x00'

    inner_block = b'\x80' + createLength(len(inner)) + inner
    payload = vox_code + inner_block
    return b'\xff\x02' + createLength(len(payload)) + payload

def getAnimBytes(elem: ET.Element) -> bytes:
    """
    ANI_FACE command (FF 03): faceCharaCode + faceImageName + faceUnk, each 2 bytes (read_word).
    Size is recalculated via createLength; content attr retained in XML for reference only.
    """
    attrs = elem.attrib
    face = bytes.fromhex(attrs.get('face'))
    anim = bytes.fromhex(attrs.get('anim'))
    buff = bytes.fromhex(attrs.get('buff'))
    payload = face + anim + buff 
    return b'\xff\x03' + createLength(len(payload)) + payload

def getFreqAddBytes(elem: ET.Element) -> bytes:
    """
    ADD_FREQ command (FF 04): adds a contact to codec memory.
    Payload: contact_freq (2B big-endian short) + name (null-terminated, game encoding).
    Size recalculated via createLength; content attr retained in XML for reference only.
    """
    attrs = elem.attrib
    freq = getFreqbytes(attrs.get('freq'))
    name_bytes, _ = RD.encodeJapaneseHex(attrs.get('name'), "")
    payload = freq + name_bytes + b'\x00'
    return b'\xff\x04' + createLength(len(payload)) + payload

def getContentBytes(elem: ET.Element) -> bytes:
    """
    FF06 (MUS_CUES), FF08 (SAVEGAME), FF40 (EVAL_CMD), and ADD_FREQ in ROUND_TRIP mode —
    opaque blobs, replayed verbatim.
    When USE_LONG=False: returned as-is.
    When USE_LONG=True: rewrites the outer size field from 2-byte to 4-byte.
      blob layout: [FF(1)] [cmd(1)] [outer_sz(2)] [payload...]
    """
    attrs = elem.attrib
    blob = bytes.fromhex(attrs.get('content'))
    if not USE_LONG:
        return blob
    # Rewrite 2-byte outer size → 4-byte.
    cmd_byte = blob[1:2]
    old_sz = struct.unpack('>H', blob[2:4])[0]
    payload_len = old_sz - 2
    payload = blob[4:4 + payload_len]
    return b'\xff' + cmd_byte + createLength(payload_len) + payload

def getContainerContentBytes(elem: ET.Element) -> bytes:
    """
    The equivelant of Handle element. 
    """
    binary = b''
    for subelem in elem:
        binary = binary + handleElement(subelem)
    binary += bytes.fromhex('00')

    return binary

def getAddFreq(elem: ET.Element) -> bytes:
    """
    This is for FF04, add contact to codec mem. 
    Content/length should be fixed earlier. 
    """
    binary = b''
    content = elem.get('content') + elem.get('name') + "00"
    binary += content.hex()

    return binary

def getMemSaveBytes(elem: ET.Element) -> bytes:
    """
    MEM_SAVE command (FF 05): save-game slot selector.
    Payload: 4 unknown bytes (extracted from content attr) + SAVE_OPT children + 0x00.
    Size is recalculated via createLength; content attr retained in XML for reference only.
    """
    attrs = elem.attrib
    unk4 = bytes.fromhex(attrs.get('content'))[4:8]  # bytes after FF 05 + 2B size field
    inner = b''
    for child in elem:
        inner += handleElement(child)
    inner += b'\x00'
    payload = unk4 + inner
    return b'\xff\x05' + createLength(len(payload)) + payload

def getAskUserBytes(elem: ET.Element) -> bytes:
    """
    ASK_USER command (FF 07): presents a yes/no prompt to the player.
    Payload: USR_OPTN children + 0x00.
    Size is recalculated via createLength; content attr retained in XML for reference only.
    """
    inner = b''
    for child in elem:
        inner += handleElement(child)
    inner += b'\x00'
    return b'\xff\x07' + createLength(len(inner)) + inner

def getIfBytes(elem: ET.Element) -> bytes:
    """
    IF_CHECK command (FF 10): conditional branch.

    Binary payload layout (what radio_if_80047514 receives):
        [GCL condition — opaque, variable length]
        [0x80] [inner_sz 2B] [THEN commands...] [0x00]   ← THEN_DO block
        [FF 12 cond 0x80 sz THEN2 0x00]...               ← zero or more ELSE_IFS
        [FF 11 0x80 sz ELSE 0x00]                         ← optional ELSE
        [0x00]                                            ← "no more branches" terminator
                                                            always present; radio_if checks
                                                            *pScript after skipping each block

    XML children: THEN_DO (synthetic, holds THEN commands), then ELSE_IFS / ELSE siblings.
    Content attr: [FF 10][outer_sz 2B][GCL cond...][0x80][inner_sz 2B] — sizes are stale.
    Condition bytes extracted as content[4:-3]; 0x80+inner_sz suffix rebuilt via createLength.
    """
    attrs = elem.attrib
    content_bytes = bytes.fromhex(attrs.get('content'))
    # content layout: [FF(1)] [10(1)] [outer_sz(2)] [cond(var)] [0x80(1)] [inner_sz(2)]
    cond_bytes = content_bytes[4:-3]  # strip FF+10+outer_sz from front, 0x80+inner_sz from back

    then_inner = b''
    chain_bytes = b''  # ELSE_IFS and ELSE within the IF payload
    for child in elem:
        if child.tag == 'THEN_DO':
            for gc in child:
                then_inner += handleElement(gc)
            then_inner += b'\x00'  # null terminator for the THEN RDCODE_SCRIPT block
        else:
            chain_bytes += handleElement(child)
    chain_bytes += b'\x00'  # "no more branches" terminator (always present; see above)

    inner_block = b'\x80' + createLength(len(then_inner)) + then_inner
    payload = cond_bytes + inner_block + chain_bytes
    return b'\xff\x10' + createLength(len(payload)) + payload

def getElseBytes(elem: ET.Element) -> bytes:
    """
    ELSE command (FF 11): unconditional else branch within an IF chain.

    No outer size field — radio_if_80047514 reads FF+11 directly, advances 2 bytes,
    then calls exec_block with pScript pointing at the 0x80 inner script marker.

    Binary layout (what exec_block receives):
        [0x80] [inner_sz 2B] [ELSE commands...] [0x00]

    Content attr: [FF 11][0x80][inner_sz 2B] — inner size is stale; rebuilt here.
    """
    inner = b''
    for child in elem:
        inner += handleElement(child)
    inner += b'\x00'
    return b'\xff\x11' + b'\x80' + createLength(len(inner)) + inner

def getElseIfBytes(elem: ET.Element) -> bytes:
    """
    ELSE_IFS command (FF 12): conditional else-if branch within an IF chain.

    No outer size field — radio_if_80047514 advances past FF+12, then re-enters
    the condition-evaluation loop. GCL condition is consumed by radio_getNextValue,
    which advances pScript to the 0x80 inner script marker.

    Binary layout (what radio_if_80047514 re-evaluates):
        [GCL condition — opaque, variable length]
        [0x80] [inner_sz 2B] [THEN commands...] [0x00]

    XML children: the THEN commands directly (no THEN_DO wrapper, unlike IF_CHECK).
    Content attr: [FF 12][GCL cond...][0x80][inner_sz 2B] — inner size is stale.
    Condition bytes extracted as content[2:-3]; 0x80+inner_sz rebuilt via createLength.
    """
    attrs = elem.attrib
    content_bytes = bytes.fromhex(attrs.get('content'))
    # content layout: [FF(1)] [12(1)] [cond(var)] [0x80(1)] [inner_sz(2)]
    cond_bytes = content_bytes[2:-3]  # strip FF+12 from front, 0x80+inner_sz from back

    inner = b''
    for child in elem:
        inner += handleElement(child)
    inner += b'\x00'
    return b'\xff\x12' + cond_bytes + b'\x80' + createLength(len(inner)) + inner

def getRndSwitchBytes(elem: ET.Element) -> bytes:
    """
    RND_SWCH command (FF 30): weighted random branch selector.
    Binary: [FF 30][outer_sz 2B][totalWeight 2B][RND_OPTN entries...][0x00]

    totalWeight is recomputed as the sum of all child RND_OPTN weights, so it
    stays correct even when options are added, removed, or reweighted.
    The stored 'totalWeight' XML attribute is informational only at recompile time.

    TODO: Add a validation warning when the stored 'totalWeight' attr does not match
    the computed sum, so edits that accidentally create an inconsistent XML are caught
    early (before the binary is written). Only needed before modifying RND_OPTN entries.
    """
    total_weight = 0
    options_bytes = b''
    for child in elem:
        options_bytes += handleElement(child)
        if child.tag == 'RND_OPTN':
            total_weight += int(child.attrib.get('weight', 0))
    options_bytes += b'\x00'  # RDCODE_NULL terminator

    payload = struct.pack('>H', total_weight) + options_bytes
    return b'\xff\x30' + createLength(len(payload)) + payload

def getRndOptnBytes(elem: ET.Element) -> bytes:
    """
    RND_OPTN entry (0x31): one weighted option within a RND_SWCH.
    Binary: [31][weight 2B][0x80][inner_sz 2B][commands...][0x00]
    No 0xFF prefix. Inner size recalculated via createLength().
    """
    weight = int(elem.attrib.get('weight', 0))
    inner = b''
    for child in elem:
        inner += handleElement(child)
    inner += b'\x00'
    return b'\x31' + struct.pack('>H', weight) + b'\x80' + createLength(len(inner)) + inner

def getGoblinBytes(elem: ET.Element) -> bytes:
    """
    For the innards of the Prompt user and Save Block data
    """
    global subUseOriginalHex
    global useDWidSaveB

    binary = b''
    if elem.tag ==  'USR_OPTN':
        if subUseOriginalHex and elem.get('textHex') is not None:
            content = "07" + int(elem.get('length')).to_bytes().hex() + elem.get('textHex')  # textHex already includes null terminator
        else:
            content = "07" + int(elem.get('length')).to_bytes().hex() + elem.get('text').encode('utf8').hex() + "00"
        binary = bytes.fromhex(content)
        if bytes.fromhex("2E") in binary:
            period = binary.find(bytes.fromhex("2e"))
            binary = binary[0 : period - 1] + bytes.fromhex("80") + binary[period - 1:] # TODO: NEEDS TESTING
    elif elem.tag ==  'SAVE_OPT':
        if subUseOriginalHex and elem.get('contentAHex') is not None:
            contentA_bytes = bytes.fromhex(elem.get('contentAHex'))  # already includes null terminator
            binary = bytes.fromhex("07") + len(contentA_bytes).to_bytes() + contentA_bytes
        else:
            useDouble = useDWidSaveB or subUseOriginalHex
            contentA = RD.encodeJapaneseHex(elem.get('contentA'), "", useDoubleLength=useDouble)[0]
            """if bytes.fromhex("2E") in binary:
                period = contentA.find(bytes.fromhex("2e"))
                contentA = contentA[0 : period] + bytes.fromhex("80") + contentA[period:] # TODO: NEEDS TESTING"""
            binary = bytes.fromhex("07") + (len(contentA) + 1).to_bytes() + contentA + bytes.fromhex("00")
        contentB = elem.get('contentB').encode("shift-jis")
        binary = binary + bytes.fromhex("07") + (len(contentB) + 1).to_bytes() + elem.get('contentB').encode("shift-jis") + bytes.fromhex("00")
    else:
        print(f'WE GOT THE WRONG ELEMENT! Should be goblin, got {elem.text}')
    
    return binary


def handleElement(elem: ET.Element) -> bytes:
    """
    Takes an element and returns the bytes for that element and all subelements. 
    """
    global ROUND_TRIP
    
    binary = b''
    attrs = elem.attrib
    match elem.tag:
        case 'SUBTITLE':
            # ff01
            binary = getSubtitleBytes(elem)
        case 'VOX_CUES': 
            # ff02
            binary = getVoxBytes(elem)
            """case 'ADD_FREQ':
            binary = getFreqAddBytes(elem)"""
        case 'ANI_FACE':
            # ff03
            binary = getAnimBytes(elem)
        case 'ADD_FREQ':
            # ff04
            if ROUND_TRIP:
                binary = getContentBytes(elem) # For testing checksums, because dictionary issues
            else:
                binary = getFreqAddBytes(elem) 
        case 'MEM_SAVE':
            # ff05
            binary = getMemSaveBytes(elem)
        case 'MUS_CUES' | 'SAVEGAME' | 'EVAL_CMD':
            # ff06, ff08, ff40 — opaque content blobs, no translatable children
            binary = getContentBytes(elem)
        case 'ASK_USER':
            # ff07
            binary = getAskUserBytes(elem)
        case 'USR_OPTN' | 'SAVE_OPT':
            binary = getGoblinBytes(elem)
        case 'IF_CHECK':
            # ff10
            binary = getIfBytes(elem)
        case 'ELSE':
            # ff11
            binary = getElseBytes(elem)
        case 'ELSE_IFS':
            # ff12
            binary = getElseIfBytes(elem)
        case 'RND_SWCH':
            # ff30
            binary = getRndSwitchBytes(elem)
        case 'RND_OPTN':
            # 0x31 (no 0xFF prefix)
            binary = getRndOptnBytes(elem)
        case 'THEN_DO':
            binary += getContainerContentBytes(elem)
    
    return binary

def fixStageDirOffsets():
    """
    Takes the finalized offset dict and uses the new values to overwrite values in stage.dir.
    Make sure you've backed up the original stage.dir file!

    Two formats:
      INTEGRAL=True:  2-byte big-endian block index at bytes[6:8], offset = block * 0x800
      INTEGRAL=False: 3-byte big-endian byte offset at bytes[5:8] (USA/JPN)
    """
    global stageBytes
    global newOffsets
    global debug
    global INTEGRAL

    if debug:
        mode = "INTEGRAL (2-byte block index at [6:8])" if INTEGRAL else "USA/JPN (3-byte offset at [5:8])"
        print(f"\n========== STAGE.DIR MODE: {mode} ==========")

        print("\n========== RADIO CALL OFFSET MAP (old -> new) ==========")
        for oldOff, (newOff, callSize) in sorted(newOffsets.items()):
            changed = " *CHANGED*" if oldOff != newOff else ""
            print(f"  old: {oldOff:>8d} (0x{oldOff:06X})  ->  new: {newOff:>8d} (0x{newOff:06X})  size: {callSize:>6d}{changed}")
        print(f"  Total calls: {len(newOffsets)}")

        print("\n========== STAGE.DIR OFFSETS FOUND ==========")
        for key in stageTools.offsetDict.keys():
            stageOff = int(stageTools.offsetDict.get(key)[0])
            rawHex = stageTools.offsetDict.get(key)[1]
            freq = struct.unpack('>H', stageBytes[key + 1: key + 3])[0]
            print(f"  stage.dir pos: {key:>8d} (0x{key:06X})  freq: {freq/100:.2f}  "
                  f"radio offset: {stageOff:>8d} (0x{stageOff:06X})  raw hex: {rawHex}")

        print("\n========== STAGE.DIR OFFSET REPLACEMENT AUDIT ==========")

    for key in stageTools.offsetDict.keys():
        stageOffset = int(stageTools.offsetDict.get(key)[0])
        offsetEntry = newOffsets.get(stageOffset)
        if offsetEntry is None:
            print(f'ERROR! Offset invalid! Key: {key} returned {stageTools.offsetDict.get(key)}')
            continue
        newOffset, callTotalSize = offsetEntry
        if newOffset == stageOffset and INTEGRAL:
            if debug:
                print(f"  SKIP (unchanged): stage pos 0x{key:06X}  offset {stageOffset} (0x{stageOffset:06X})")
            continue

        if INTEGRAL:
            # Integral: write 2-byte big-endian block index to bytes[6:8]
            if newOffset % 0x800 != 0:
                print(f'ERROR! New offset {newOffset} (0x{newOffset:06X}) is not 0x800-aligned! Cannot convert to block index.')
                continue
            newBlock = newOffset // 0x800
            if newBlock > 0xFFFF:
                print(f'ERROR! Block index {newBlock} exceeds 2-byte max (65535)!')
                continue
            oldBytes = stageBytes[key + 6: key + 8]
            stageBytes[key + 6: key + 8] = struct.pack('>H', newBlock)
            newBytes = stageBytes[key + 6: key + 8]
            if debug:
                oldBlock = stageOffset // 0x800
                freq = struct.unpack('>H', stageBytes[key + 1: key + 3])[0]
                print(f"  REPLACE: stage pos 0x{key:06X}  freq {freq/100:.2f}  "
                      f"old block: {oldBlock:>5d} (off 0x{stageOffset:06X}) [{oldBytes.hex()}]  ->  "
                      f"new block: {newBlock:>5d} (off 0x{newOffset:06X}) [{newBytes.hex()}]  "
                      f"(2 bytes written to stageBytes[0x{key+6:06X}:0x{key+8:06X}])")
        else:
            # USA/JPN: write 3-byte big-endian byte offset to bytes[5:8]
            # and recompute byte4 (sector count - 1) so the game reads enough sectors
            withinSector = newOffset % 2048
            sectorsNeeded = math.ceil((withinSector + callTotalSize) / 2048)
            newByte4 = sectorsNeeded - 1
            if newByte4 > 0xFF:
                print(f'ERROR! Sector count {sectorsNeeded} exceeds byte4 max (256)! '
                      f'Call at offset 0x{newOffset:06X} size {callTotalSize}')
                continue

            oldByte4 = stageBytes[key + 4]
            newOffsetHex = struct.pack('>L', newOffset)
            oldBytes = stageBytes[key + 4: key + 8]
            stageBytes[key + 4] = newByte4
            stageBytes[key + 5: key + 8] = newOffsetHex[1:4]
            newBytes = stageBytes[key + 4: key + 8]

            if newOffset == stageOffset and newByte4 == oldByte4:
                if debug:
                    print(f"  SKIP (unchanged): stage pos 0x{key:06X}  offset {stageOffset} (0x{stageOffset:06X})")
                continue

            if debug:
                freq = struct.unpack('>H', stageBytes[key + 1: key + 3])[0]
                print(f"  REPLACE: stage pos 0x{key:06X}  freq {freq/100:.2f}  "
                      f"old: {stageOffset:>8d} (0x{stageOffset:06X}) byte4={oldByte4:02X} [{oldBytes.hex()}]  ->  "
                      f"new: {newOffset:>8d} (0x{newOffset:06X}) byte4={newByte4:02X} [{newBytes.hex()}]  "
                      f"sectors={sectorsNeeded}  "
                      f"(4 bytes written to stageBytes[0x{key+4:06X}:0x{key+8:06X}])")

def main(args=None):
    global subUseOriginalHex
    global stageBytes
    global debug
    global useDWidSaveB
    global USE_LONG
    global INTEGRAL
    global PAD
    global ROUND_TRIP
    global currentCallDict


    if args == None:
        args = parser.parse_args()

    if args.long:
        USE_LONG = True
        xmlFix.modded = True

    # Read new radio source
    if args.prepare:
        print(f'WARNING: -p/--prepare is deprecated. Text encoding and length recomputation is now automatic. This flag is ignored.')
        # -- deprecated prepare logic (kept for reference) --
        # print(f'Preparing XML by repairing lengths...')
        # ROUND_TRIP = False  # re-encode from text attr in translation mode
        # root = xmlFix.init(args.input)

    radioSource = ET.parse(args.input)
    root = radioSource.getroot()

    if args.output:
        outputFilename = args.output
    else:
        outputFilename = 'new-' + args.input.split("/")[-1].split(".")[0] + '.bin'

    if args.pad:
        PAD = True
    
    if args.roundtrip:
        ROUND_TRIP = True

    if args.integral:
        INTEGRAL = True
        # Integral requires padding (calls must be 0x800-aligned)
        if not PAD:
            print('WARNING: --integral implies --pad (0x800 alignment). Enabling automatically.')
        PAD = True

    if args.hex:
        subUseOriginalHex = True
        xmlFix.subUseOriginalHex = True

    if args.double:
        useDWidSaveB = True
        xmlFix.useDWSB = True

    if args.debug:
        debug = True
        xmlFix.debug = True
        RD.debug = True

    outputContent = b''

    for call in root:
        if PAD:
            # Align call start to 0x800 boundary
            remainder = len(outputContent) % 0x800
            if remainder != 0:
                outputContent += b'\x00' * (0x800 - remainder)

        # Record the new offset created for the call
        newCallOffset = len(outputContent)
        if PAD and newCallOffset % 0x800 != 0:
            print(f'BUG: call at offset {newCallOffset} is not 0x800-aligned after padding!')
        attrs = call.attrib
        currentCallDict = attrs.get('graphicsBytes', '')

        # Set hex passthrough mode before compiling elements so handleElement sees it.
        if args.hex:
            subUseOriginalHex = True
            xmlFix.subUseOriginalHex = True
        elif attrs.get('modified') == "True":
            subUseOriginalHex = False
            xmlFix.subUseOriginalHex = False
        else:
            subUseOriginalHex = True
            xmlFix.subUseOriginalHex = True

        # Compile all child elements first so the true payload size is known.
        elemContent = b''
        for subelem in call:
            elemContent += handleElement(subelem)
        elemContent += b'\x00'  # call payload terminator

        # Build call header from named attrs with recalculated size field.
        if not USE_LONG and (2 + len(elemContent)) > 0xFFFF:
            print(f'ERROR: Call at freq {attrs.get("freq")} (offset {attrs.get("offset")}) payload is {len(elemContent)} bytes — exceeds 65535 size field limit!')

        freq = getFreqbytes(attrs.get('freq'))
        unk1 = bytes.fromhex(attrs.get("unknownVal1"))
        unk2 = bytes.fromhex(attrs.get("unknownVal2"))
        unk3 = bytes.fromhex(attrs.get("unknownVal3"))
        callHeader = freq + unk1 + unk2 + unk3 + b'\x80' + createLength(len(elemContent))

        callData = callHeader + elemContent
        graphicsBytes = b''
        if attrs.get('graphicsBytes') is not None and subUseOriginalHex == True:
            graphicsBytes = bytes.fromhex(attrs.get('graphicsBytes'))

        callTotalSize = len(callData) + len(graphicsBytes)
        newOffsets.update({int(call.attrib.get("offset")): (newCallOffset, callTotalSize)})

        outputContent += callData + graphicsBytes

    # Final alignment pad at end of file if padded
    if PAD:
        remainder = len(outputContent) % 0x800
        if remainder != 0:
            outputContent += b'\x00' * (0x800 - remainder)

    with open(outputFilename, 'wb') as radioOut:
        radioOut.write(outputContent)

    if args.stageOut:
        stageOutFile = args.stageOut
    else:
        stageOutFile = 'new-STAGE.DIR'

    if args.stage:
        stageDirFilename = args.stage
        stageTools.debug = debug
        stageTools.INTEGRAL = INTEGRAL
        stageTools.init(stageDirFilename)
        stageBytes = bytearray(stageTools.stageData)
        fixStageDirOffsets()
        with open(stageOutFile, 'wb') as stageOut:
            stageOut.write(stageBytes)

    if debug:
        print(newOffsets)

if __name__ == '__main__':
    # Parse arguments here, then run main so that this can be called from another parent script.
    parser = argparse.ArgumentParser(description=f'recompile an XML exported from RadioDatTools.py. Usage: script.py <input.xml> [output.bin]')
    parser.add_argument('input', type=str, help="Input XML to be recompiled.")
    parser.add_argument('output', nargs="?", type=str, help="Output Filename (.bin). If not present, will re-use basename of input with -mod.bin")
    parser.add_argument('-s', '--stage', nargs="?", type=str, help="Toggles STAGE.DIR modification, requires filename. Use -S for output filename.")
    parser.add_argument('-p', '--prepare', action='store_true', help="[DEPRECATED] Text encoding and length recomputation is now automatic. This flag is ignored.")
    parser.add_argument('-x', '--hex', action='store_true', help="Outputs hex with original subtitle hex, rather than converting dialogue to hex.")
    parser.add_argument('-v', '--debug', action='store_true', help="Prints debug information for troubleshooting compilation.")
    parser.add_argument('-D', '--double', action='store_true', help="Save blocks use double-width encoding [original vers.]")
    parser.add_argument('-l', '--long', action='store_true', help="Write 4-byte size fields (USE_LONG=True) for use with the patched SLPM_862.47 executable.")
    parser.add_argument('-P', '--pad', action='store_true', help="Align each call to 0x800 boundaries (for Integral-format RADIO.DAT). Implied by --integral.")
    parser.add_argument('-I', '--integral', action='store_true', help="Integral disc mode: 0x800-aligned calls, 2-byte block index in STAGE.DIR. Implies --pad.")
    parser.add_argument('-S', '--stageOut', nargs="?", type=str, help="Output for new STAGE.DIR file. Optional.")
    parser.add_argument('-R', '--roundtrip', action='store_true', help="For comparing recompilation checksums. All original text hexes are used!")
    
    main()