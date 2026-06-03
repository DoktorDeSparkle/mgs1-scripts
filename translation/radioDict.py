#!/bin/python

""" Doktor De Sparkle's JPN Script decoder tool!

  This is the culmination of a massive effort by myself and Green_Goblin
  to create a text extraction tool for Metal Gear Solid (JPN) for the 
  Playstation. 

  Characters are still in the works, but this should translate the binary
  character data found in any file to normal Shift-JIS text.

  Running it as is runs a loop that will translate what is needed. 
  Use it as a library to translate things in code.

"""

import os, struct, re, sys
# sys.path.append(os.path.abspath('./myScripts'))
import translation.characters as characters
# import characters

# Disabling graphics output for now
# os.makedirs('graphicsExport', exist_ok=True)
# missingChars = open('graphicsExport/KanjiStillMissing.txt', 'w', encoding='utf8')
# missingGFX = open('graphicsExport/GraphicsStillMissing.txt', 'w', encoding='utf8') # Accessed from RadioDatTools in main()

# GLOBAL STUFF
radioData = b''
foundGraphics = []
unidentifiedGraphics = []

# context = open("graphicsExport/contextList.txt", 'w')
debug = False

# Optional .tbl encoder overrides (char -> hex code string).
# Set by the GUI when a .tbl file is loaded. Used as a fallback when
# tblOverrides is not passed directly to encodeJapaneseHex.
tblEncoderOverrides = None

class graphicSegment:

	def __init__(self, offset, callGraphicNum, graphicBytesHex) -> None:
		self.callOffset = offset
		self.indexNum = callGraphicNum
		self.bytesHex = graphicBytesHex
		pass

	def __str__(self):
		return f'{self.callOffset}, {self.indexNum}, {self.bytesHex}'

def openRadioFile(filename: str) -> None:
	global radioData
	radioFile = open(filename, 'rb')
	radioData = radioFile.read()

def countGraphics(message: str) -> None:
	count = 0
	for phrase in characters.graphicsData:
		if phrase in message:
			print(f'Phrase {phrase} found!')
			count += 1
	return count

def checkForGraphics(message: str) -> bool:
	for phrase in characters.graphicsData:
		if phrase in message:
			return True	
	return False
					  
def getRadioCharacter(hexString: str) -> str:
	return characters.radioChar[hexString]

def outputEmbeddedGraphics() -> None:
	i = 1
	for data in characters.graphicsData:
		outputGraphic(str(i), bytes.fromhex(data))
		i += 1

def outputManyGraphics(filename: str, data: bytes) -> None:
	count = 0
	if len(data) % 36 == 0:
		count = int(len(data) / 36)
		for x in range(count):
			segment = data[x * 36: (x + 1) * 36 ]
			outputGraphic(f'{filename}-{x}', segment)
			a = graphicSegment(filename, x, segment.hex())
			foundGraphics.append(a)

def outputGraphic(filename: str, file_data: bytes) -> None: 
	"""
	Partially generated from chatGPT but it had the endianness of the width/12 wrong.
	Stolen from https://github.com/metalgeardev/MGS1/blob/master/unRadio_v2.rb and converted by gpt
	"""
	# Convert binary data to bit string
	bit_string = ''.join(format(byte, '08b') for byte in file_data)
	width = len(bit_string) // 2

	with open(f"graphicsExport/{filename}.tga", "wb") as data:
		# Create and write TGA header
		header = "0000020000000000000000000c00" + struct.pack('<H', width // 12).hex() + "2020"
		data.write(bytes.fromhex(header))
		for byte in range(width):
			bits = bit_string[byte * 2 : byte * 2 + 2]
			if bits == "00":
				data.write(bytes.fromhex("000000ff"))
			elif bits == "01":
				data.write(bytes.fromhex("555555ff"))
			elif bits == "10":
				data.write(bytes.fromhex("aaaaaaff"))
			else:
				data.write(bytes.fromhex("ffffffff"))

def isGraphicsTile(tile: bytes) -> bool:
	"""Return True if the 36-byte tile is a known character tile in characters.graphicsData."""
	return tile.hex() in characters.graphicsData

def makeCallDictionary(offset, graphicsBytes: bytes): # remove type on offset, may revert later
	"""
	Returns a DICT specific to the call where we sent the data. 
	"""
	
	# We need to ensure this is evenly divisible by 36 bytes:
	if len(graphicsBytes) % 36 != 0:
		print(f'Error! LENGTH is not even number of graphics! Assuming nulls after...')

	count = 0 # May need to start at 1 instead
	callDictionary = {}
	# Output a dictionary file for each dictionary we create
	# dictFile = open(f'graphicsExport/Dict-{offset}.txt', 'w', encoding='utf8')

	# print(len(graphicsBytes))
	count = int(len(graphicsBytes) / 36)
	for x in range(count):
		segment = graphicsBytes[x * 36: (x + 1) * 36 ]
		if segment == bytes(36):
			break

		# This section only necessary if outputtting graphics we haven't identified.
		global foundGraphics
		global unidentifiedGraphics
		if segment.hex() not in characters.graphicsData:
			foundGraphics.append(segment.hex())
			result = f'[{segment.hex()}]'
		elif characters.graphicsData.get(segment.hex()) == "?":
			unidentifiedGraphics.append(segment.hex())
			result = f'[{segment.hex()}]'
		else:
			result = characters.graphicsData.get(segment.hex())

		callDictionary.update({x + 1: result})
		# dictFile.write(f'{x + 1}: {result}\n')

	return callDictionary

# Kinsoku flag sentinels emitted into the decoded text so the encoder can
# reconstruct the original byte-level flag bits on the high byte of a 2-byte
# code:  0x2000 = BACK_KINSOKU ("don't end a line here"),
#        0x4000 = TOP_KINSOKU  ("don't start a line here").
SENT_BK = '‹BK›'  # ‹BK›
SENT_TK = '‹TK›'  # ‹TK›
KINSOKU_SENTINELS = {SENT_BK: 0x2000, SENT_TK: 0x4000}

def translateJapaneseHex(bytestring: bytes, callDict: dict[str, str] = None ) -> str:
	i = 0
	messageString = ''
	customCharacter = False
	while i < len(bytestring) - 1:
		b0 = bytestring[i]
		# Custom-char escape: 2 bytes, second is index into per-call dict.
		# Flag bits are not meaningful here (these aren't font glyph codes).
		if b0 in (0x96, 0x97, 0x98, 0x9c):
			customCharNum = int(bytestring[i + 1])
			if b0 == 0x97:
				customCharNum += 255
			elif b0 == 0x98:
				customCharNum += 510
			result = callDict.get(customCharNum) if callDict else None
			if result is not None:
				messageString += result
			else:
				messageString += f'[{bytestring[i:i+2].hex()}]'
				customCharacter = True
			i += 2
			continue
		# Spanish 2-byte prefixes (translation insertion codes)
		if b0 in (0x1f, 0x12):
			charcheck = bytestring[i:i+2].hex()
			messageString += characters.spanishChars.get(charcheck, f'[{charcheck}]')
			i += 2
			continue
		# Literal "\r\n" escape sequence (4 source bytes, 4 char output)
		if bytestring[i:i+4] == b'\x5c\x72\x5c\x6e':
			messageString += '\\r\\n'
			i += 4
			continue
		# 1-byte ASCII (internally lifted by the font to 0x8000|byte)
		if b0 < 0x80:
			messageString += chr(b0)
			i += 1
			continue
		# 2-byte code: split out kinsoku flags from the canonical glyph code
		raw   = (b0 << 8) | bytestring[i+1]
		flags = raw & 0x6000
		glyph = raw & 0x9FFF
		hi = glyph >> 8
		lo_hex = f'{glyph & 0xFF:02x}'
		if hi == 0x80:
			ch = characters.radioChar.get(lo_hex)
		elif hi == 0x81:
			ch = characters.hiragana.get(lo_hex)
		elif hi == 0x82:
			ch = characters.katakana.get(lo_hex)
		else:
			ch = characters.kanji.get(f'{glyph:04x}')
		if ch is None:
			messageString += f'[{raw:04x}]'
			customCharacter = True
		else:
			messageString += ch
			if flags & 0x2000:
				messageString += SENT_BK
			if flags & 0x4000:
				messageString += SENT_TK
		i += 2

	return messageString

def encodeJapaneseHex(dialogue: str, callDict="", useDoubleLength=False, tblOverrides: dict = None) -> tuple[bytes, str]: # Needs fixins, maybe move to separate file?
	"""
	WORK IN PROGRESS! Re-encodes japanese characters.

	tblOverrides: optional dict mapping character -> hex code string.
	If provided, characters found in this dict are encoded using the
	override bytes instead of the default characters.py lookups.
	Falls back to the module-level tblEncoderOverrides if not passed.

	Kinsoku sentinels (‹BK› / ‹TK›) trailing a character set the
	BACK_KINSOKU / TOP_KINSOKU bits on the high byte of that character's
	2-byte code so round-tripped flags survive re-encoding.
	"""
	overrides = tblOverrides if tblOverrides is not None else tblEncoderOverrides
	unknownChars = True # For now we still dont know some characters

	newBytestring = b''
	def addCharToDict(customHex: str):
		if debug:
			print(f'Character {character} was not found in custom call dict. Adding...')
		index = int(len(callDict) / 72)
		if index > 508:
			index -= 508
			newBytestring += b'\x98'
		elif index > 254:
			index -= 254
			newBytestring += b'\x97'
		else:
			newBytestring += b'\x96'
		callDict += customHex

	# THIS IS ONLY UNTIL WE IDENTIFY ALL MISSING CHARACTERS!
	if unknownChars:
		if "[" in dialogue:
			# print(f'Replace DIALOGUE: {dialogue}') # Used for debugging what dialogue was looking like
			dialogue = re.sub(r"\[[0-9A-Fa-f]{72}\]", "?", dialogue)
			# print(f'New Dialogue: {dialogue}')

	i = 0
	while i < len(dialogue):
		# Drop any orphan kinsoku sentinel (no preceding char to attach to)
		if dialogue[i:i+4] in KINSOKU_SENTINELS:
			i += 4
			continue
		character = dialogue[i]
		i += 1
		# Collect any kinsoku sentinels trailing this character
		mask = 0
		while dialogue[i:i+4] in KINSOKU_SENTINELS:
			mask |= KINSOKU_SENTINELS[dialogue[i:i+4]]
			i += 4
		prev_len = len(newBytestring)
		if ord(character) == 65294:
				newBytestring += b'\x80\x2e'
		elif overrides and character in overrides:
			newBytestring += bytes.fromhex(overrides[character])
		elif ord(character) < 128:
			if useDoubleLength:
				newBytestring += b'\x80'
			newBytestring += character.encode('ascii')
		elif character in characters.revSpanish:
			newBytestring += bytes.fromhex(characters.revSpanish.get(character))
		elif character in characters.revRadio:
			newBytestring += b'\x80' + bytes.fromhex(characters.revRadio.get(character))
		elif character in characters.revHiragana:
			newBytestring += b'\x81' + bytes.fromhex(characters.revHiragana.get(character))
		elif character in characters.revKatakana:
			newBytestring += b'\x82' + bytes.fromhex(characters.revKatakana.get(character))
		elif character in characters.revKanji:
			newBytestring += bytes.fromhex(characters.revKanji.get(character))
		elif character in characters.revPunct:
			# Punctuation (0x90?? glyph slots) — emit unflagged so kinsoku
			# sentinels can apply the TOP/BACK bits via the mask path below.
			newBytestring += b'\x90' + bytes.fromhex(characters.revPunct.get(character))
		elif character in characters.revCustomChar:
			# This means we add it to the custom dict. 
			customHex = characters.revCustomChar.get(character)
			if customHex == None:
				print(f'ERROR! Unable to encode character {character}! We found no match in logic.')
				continue
			if callDict == None:
				# addCharToDict(customHex)
				if debug:
					print(f'Character {character} was not found in custom call dict. Adding...')
				newBytestring += bytes.fromhex('9601')
				callDict = customHex
			elif customHex in callDict:
				index = int(callDict.find(customHex) / 72) + 1
				if index > 508:
					index -= 508
					newBytestring += b'\x98'
				elif index > 254:
					index -= 254
					newBytestring += b'\x97'
				else:
					newBytestring += b'\x96'
				newBytestring += index.to_bytes()
			else:
				# addCharToDict(customHex)
				if debug:
					print(f'Character {character} was not found in custom call dict. Adding...')
				index = int(len(callDict) / 72) + 1
				if index > 508:
					index -= 508
					newBytestring += b'\x98'
				elif index > 254:
					index -= 254
					newBytestring += b'\x97'
				else:
					newBytestring += b'\x96'
				callDict += customHex
				newBytestring += index.to_bytes()
		# Apply trailing kinsoku flags to the high byte of the 2-byte code
		# emitted for this character (no-op for 1-byte ASCII output).
		if mask and len(newBytestring) - prev_len >= 2:
			tail = bytearray(newBytestring)
			tail[prev_len] |= (mask >> 8)  # 0x2000->0x20, 0x4000->0x40
			newBytestring = bytes(tail)

	return newBytestring, callDict

"""
This only used for working with the graphics data found in jpn-d1, it was combined with the bash script to ocr the characters
"""

def debugExportUniqueGFX():
	uniqueGraphics = open("translation/unique graphics", 'r')
	uniqueGraphics2 = open("translation/unique graphics 2.csv", 'w')
	lineNum = 1
	for line in uniqueGraphics:
		f = open(f'graphicsExport/output/{lineNum}.txt', 'r')
		content = f.read().replace('\n', ' ').strip()
		uniqueGraphics2.write(f'{lineNum}, \"{line.strip()}\", \"{content}\"\n')
		lineNum += 1
		f.close()
	uniqueGraphics2.close()

"""
TESTING AREA! Anything below this is meant for testing functionality or debug. 

This is testing for recompiling byte data. 
"""
if __name__ == "__main__":
	print(f'METAL GEAR SOLID (PSX) JAPANESE TEXT DECRYPTION!\n')
	while True:
		from deep_translator import GoogleTranslator
		print(f'Please give hex to translate: \n')
		stuff = bytes.fromhex(input("> "))
		result = translateJapaneseHex(stuff, None)
		print(f'Japanese: {result}')
		print(f'English: {GoogleTranslator(source='ja', dest='en').translate(result)}')

	
	# stuff = bytes.fromhex("B0 14 90 B0 90 B1 D0 15 80 7C 82 1D 82 4A C2 23 82 29 C0 7F 82 19 82 2D D0 06 82 0F 90 1F 90 49 81 2E 90 B0 90 B1 D0 03 90 B2 81 04 90 B3 81 0E 81 2B 81 2F 90 B4 90 B5 81 17 81 3E 81 17 C1 47 81 06 D0 03")
	# print(translateJapaneseHex(stuff, None))


# if __name__ == "__main__":
# 	import xml.etree.ElementTree as ET
# 	from xml.dom.minidom import parseString
# 	import time

# 	radioXMLFile = 'recompiledCallBins/RADIO-goblin.xml'
# 	root = ET.parse(radioXMLFile)

# 	for call in root.getroot():
# 		print(call.get('offset'))
# 		graphicData = call.get("graphicsBytes")
# 		for subs in call.findall('.//SUBTITLE'):
# 			newBytes, newDict = encodeJapaneseHex(subs.get("text"), graphicData)
# 			if newBytes.decode('utf8', errors='backslashreplace') != subs.get("text"):
# 				print(str(newBytes))
# 				print(f'Original: {subs.get("text")}')
# 				print(newBytes.hex())
# 				time.sleep(3)
# 			if newDict == graphicData:
# 				time.sleep(0)
# 			else:
# 				print(f'Call Dicts don\'t match!')