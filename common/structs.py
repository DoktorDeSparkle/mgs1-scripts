"""
Commonly used structures for MGS dialogue lines will go here.

demoSub is one of the big ones as the similar thing is used in demo, vox, zmovie.
"""
import sys, struct, os
sys.path.append(os.path.abspath('./myScripts'))

import translation.radioDict as RD


class subtitle:
    text: str
    startFrame: int
    duration: int

    def __init__(self, dialogue_or_bytes, b = None, c = None) -> None:
        if type(dialogue_or_bytes) == bytes:
            length, start, duration = struct.unpack("III", rawBytes[0:12])
            self.text = dialogue_or_bytes[16:].strip(bytes.fromhex("00"))
            self.startFrame = int(start)
            self.duration = int(duration)
        elif type(dialogue_or_bytes) == str:
            self.text = dialogue_or_bytes
            self.startFrame = int(b)
            self.duration = int(c)

        return
    
    # def __init__(self, rawBytes: bytes) -> None:
    #     length, start, duration = struct.unpack("III", rawBytes[0:12])
    #     self.text = rawBytes[16:].strip(bytes.fromhex("00"))
    #     self.startFrame = int(start)
    #     self.duration = int(duration)

    #     return
    
    def __str__(self) -> str:
        a = f'Subtitle contents: Start: {self.startFrame} Duration: {self.duration} Text: {self.text}'
        return a
    
    def __bytes__(self) -> bytes:
        """
        Simple. Encodes the dialogue as bytes. 
        Adds the buffer we need to be divisible by 4...
        Return the new bytes.
        """
        subtitleBytes: bytes = struct.pack("III", self.startFrame, self.duration, 0)
        # demo / vox / zmovie subtitles all render through font bank 3
        # (font_set_font_addr(3, ...) in game/jimctrl.c and movie.c) so
        # custom-char escapes must use the 0x9c / 0x9d prefix family.
        subtitleBytes += RD.encodeJapaneseHex(self.text, bank=3)[0]
        bufferNeeded = 4 - (len(subtitleBytes) % 4)
        subtitleBytes += bytes(bufferNeeded)
        
        return subtitleBytes