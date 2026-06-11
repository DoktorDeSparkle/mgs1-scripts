"""
Game-font data for the codec preview, character-set browsing, and text
validation (encodability + byte length).

The font itself is parsed out of STAGE.DIR with fontTools.mgsFontTools (pure
struct math — no PIL needed for what we use). Glyphs are shipped to the
frontend as raw 2bpp hex strings; the browser decodes and draws them on a
canvas, so previews are instant and entirely client-side.
"""

import os

from . import core  # ensures SCRIPTS_DIR is importable

from translation import characters
from translation import radioDict

try:
    # mgsFontTools imports PIL at module level; only needed for PNG helpers
    # we don't use, but the import can still fail if Pillow is absent.
    from fontTools import mgsFontTools as mft
    from fontTools import tblTools as tbl
    _FONT_TOOLS = True
    _FONT_TOOLS_ERROR = None
except Exception as exc:  # pragma: no cover
    _FONT_TOOLS = False
    _FONT_TOOLS_ERROR = str(exc)


# ── character → glyph mapping ────────────────────────────────────────────────

def _char_code(ch: str) -> str | None:
    """Full 4-hex in-game code for a character, or None."""
    if ch in characters.revHiragana:
        return "81" + characters.revHiragana[ch]
    if ch in characters.revKatakana:
        return "82" + characters.revKatakana[ch]
    if ch in characters.revKanji:
        return characters.revKanji[ch]
    return None


def build_char_mapping() -> dict:
    """char → glyph reference for the frontend renderer.

    {"type": "ascii", "idx": n}    → variable-width ASCII glyph n (0x20+n)
    {"type": "kana", "slot": n}    → fixed 12x12 font slot n
    {"type": "tile", "hex": "..."} → standalone 36-byte 2bpp tile
    """
    mapping = {}
    # The 0x80 bank indexes straight into the ASCII strip of the game font,
    # so both plain and fullwidth variants map to ascii glyph (low byte - 0x20).
    for code2, ch in characters.radioChar.items():
        idx = int(code2, 16) - 0x20
        if 0 <= idx < 96:
            mapping[ch] = {"type": "ascii", "idx": idx}
    if _FONT_TOOLS:
        for table, prefix in ((characters.hiragana, "81"),
                              (characters.katakana, "82")):
            for code2, ch in table.items():
                slot = tbl.hexCodeToSlot(prefix + code2)
                if slot >= 0:
                    mapping.setdefault(ch, {"type": "kana", "slot": slot})
        for code4, ch in characters.kanji.items():
            slot = tbl.hexCodeToSlot(code4)
            if slot >= 0:
                mapping.setdefault(ch, {"type": "kana", "slot": slot})
    # global custom-character tiles identified so far (radio call glyphs)
    for ch, tile_hex in characters.revCustomChar.items():
        mapping.setdefault(ch, {"type": "tile", "hex": tile_hex})
    return mapping


_font_cache = {}


def font_payload(project) -> dict:
    """Everything the frontend needs to render text in the real game font."""
    stage = project.config.get("stageDir", "")
    key = None
    if _FONT_TOOLS and stage and os.path.isfile(stage):
        key = (stage, os.path.getmtime(stage))
        cached = _font_cache.get("payload")
        if cached and cached[0] == key:
            return cached[1]

    payload = {
        "available": False,
        "reason": None,
        "mapping": build_char_mapping(),
    }
    if not _FONT_TOOLS:
        payload["reason"] = f"fontTools unavailable: {_FONT_TOOLS_ERROR}"
        return payload
    if not stage or not os.path.isfile(stage):
        payload["reason"] = "STAGE.DIR not set — codec preview falls back to system font."
        return payload

    try:
        font = mft.loadFont(stage)
    except Exception as exc:
        payload["reason"] = f"could not parse font from STAGE.DIR: {exc}"
        return payload

    payload["available"] = True
    payload["ascii"] = [
        {"hex": g.hex(), "width": font.asciiPixelWidth(i)}
        for i, g in enumerate(font.asciiGlyphs)
    ]
    payload["kana"] = [g.hex() for g in font.kanaGlyphs]
    _font_cache["payload"] = (key, payload)
    return payload


# ── character set browser ────────────────────────────────────────────────────

def charset_payload() -> dict:
    sections = []
    sections.append({
        "name": "Radio ASCII (0x80--)",
        "entries": [{"code": "80" + k, "char": v}
                    for k, v in characters.radioChar.items()],
    })
    sections.append({
        "name": "Hiragana (0x81--)",
        "entries": [{"code": "81" + k, "char": v}
                    for k, v in characters.hiragana.items()],
    })
    sections.append({
        "name": "Katakana (0x82--)",
        "entries": [{"code": "82" + k, "char": v}
                    for k, v in characters.katakana.items()],
    })
    sections.append({
        "name": "Kanji / punctuation",
        "entries": [{"code": k, "char": v}
                    for k, v in characters.kanji.items()],
    })
    if hasattr(characters, "spanishChars"):
        sections.append({
            "name": "Accented (Integral insertion codes)",
            "entries": [{"code": k, "char": v}
                        for k, v in characters.spanishChars.items()],
        })
    custom = [{"char": ch, "hex": tile}
              for tile, ch in characters.graphicsData.items()]
    return {"sections": sections, "customTiles": custom}


# ── text validation ──────────────────────────────────────────────────────────

NEWLINE_LITERAL = "\\r\\n"      # radio texts carry a literal \r\n escape
NEWLINE_BYTES = 4               # which encodes to 0x80 0x23 0x80 0x4e (＃Ｎ)
NEWLINE_PIPE = "｜"             # demo/vox/zmovie use the fullwidth pipe


def check_text(text: str, bank: int = 1) -> dict:
    """Validate a line: does it encode, and how many bytes does it take?"""
    result = {"ok": True, "bytes": 0, "customChars": 0, "error": None,
              "unknown": []}

    extra = 0
    if bank == 1:
        # Codec: \r\n literal encodes to 4 bytes; strip and count separately.
        work = text.replace("\r\n", NEWLINE_LITERAL).replace("\n", NEWLINE_LITERAL)
        newlines = work.count(NEWLINE_LITERAL)
        work = work.replace(NEWLINE_LITERAL, "")
        extra = newlines * NEWLINE_BYTES
    else:
        # Demo/vox/zmovie: line break is the fullwidth pipe (a normal table
        # character, 2 bytes) — just normalize and let the encoder count it.
        work = text.replace("\r\n", NEWLINE_PIPE).replace("\n", NEWLINE_PIPE)

    try:
        encoded, call_dict = radioDict.encodeJapaneseHex(work, "", bank=bank)
        result["bytes"] = len(encoded) + extra
        if call_dict:
            # encoder had to allocate custom glyph slots for these characters
            result["customChars"] = len(call_dict) // 72  # 36 bytes = 72 hex
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result
