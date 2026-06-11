# MGS1 Undub Studio (gui/)

A portable, local graphical front-end for the mgs1-scripts translation
toolkit. The interface is served to your browser by a Python standard-library
web server.

**Easiest:** from the repo root, run the launcher — it creates a `.venv`,
installs [gui/requirements.txt](requirements.txt) (only when it changes), and
starts the app:

```bash
./launch.sh                   # Linux / macOS
launch.bat                    # Windows (double-click works too)
./launch.sh --host 0.0.0.0    # arguments pass through
```

**Manual:** Python 3.10+ with `progressbar2` (radio pipeline) and `Pillow`
(game-font preview, optional):

```bash
python3 gui/app.py            # opens http://127.0.0.1:8470
python3 gui/app.py --port 9000 --no-browser
python3 gui/app.py --host 0.0.0.0   # listen on the LAN (no auth — trusted networks only)
```

Missing dependencies are detected at startup and shown both in the terminal
and on the Project page with the exact install command.

## What it does

| Area | How |
|---|---|
| **Extract** | RADIO.DAT via `RadioDatTools.py -x -z` (subprocess); DEMO/VOX via `DemoTools/extractDemoVox.py`; ZMOVIE via `zmovieTools/extractZmovie.py` (imported) |
| **Edit** | Side-by-side original/translation editors for codec calls, codec contact names, prompts, save slots, and demo/vox/zmovie subtitles incl. timings (start frame + duration, with ~seconds hints) |
| **Validate** | Every line is round-tripped through `radioDict.encodeJapaneseHex` (bank 1 for radio, bank 3 for demo/vox/zmovie): byte size vs. original, un-encodable characters, dynamically-allocated custom glyphs, per-call 65,535-byte budget, zmovie subtitle-block capacity |
| **Preview** | The real game font is parsed out of STAGE.DIR (`fontTools/mgsFontTools.py`) and rendered client-side: codec-window preview with game-accurate pixel widths and wrapping (260 px / 4 lines) |
| **Recompile** | `xmlModifierTools.py inject` + `RadioDatRecompiler.py` (subprocess, with `-s/-S` STAGE.DIR offset fixing and the `-I/-P/-l/-D` toggles); DEMO/VOX rebuilt in-process with `demoClasses` patch-in-place serialization; ZMOVIE via `compileToFile` |
| **STAGE.DIR** | Demo (`Ps`) and vox (`Pv`) block references are re-pointed automatically when entries move (same patterns as `StageDirTools/*OffsetAdjuster.py`, but never destructive — output goes to the workspace); vox `voxCode` updates are applied to a *copy* of RADIO.xml before injection |
| **Translate** | Optional machine translation (DeepL / Google / LibreTranslate, bring your own key), per line or per call/entry; CSV export/import for spreadsheet or CAT-tool workflows |
| **Charset** | Browser for every known table in `translation/characters.py`, the identified custom glyph tiles, and the raw STAGE.DIR font |

## Workspace layout

Opening a disc folder (e.g. `build-src/jpn-d1`) auto-detects the game files
and creates `undub-workspace/` inside it:

```
undub-workspace/
├── project.json               # paths + settings
├── radio/
│   ├── RADIO.xml              # extraction (pristine — never modified)
│   ├── RADIO-Iseeva.json      # original dialogue (reference)
│   ├── RADIO-work.json        # YOUR translations (Iseeva format)
│   └── RADIO*-merged.xml      # build artifact
├── demo|vox|zmovie/
│   ├── <kind>Text.json        # original
│   └── <kind>Text-work.json   # YOUR translations
└── out/
    ├── RADIO.DAT  DEMO.DAT  VOX.DAT  ZMOVIE.STR  STAGE.DIR
    └── demo-blockmap.json  vox-blockmap.json
```

The `-work.json` files are plain toolkit-compatible formats — you can keep
using the CLI scripts on them, or edit them by hand; the GUI picks up changes.

## Build flow

`Build everything` runs: **DEMO → VOX → RADIO → ZMOVIE**.

STAGE.DIR is assembled from the pristine source each time: demo/vox block
relocations are applied first, then the radio recompiler fixes call offsets on
top (`-s` template → final `out/STAGE.DIR`). If you build only demo/vox after
having built radio, re-run the radio build so STAGE.DIR carries both — the log
reminds you.

Copy `out/*` over the files in your `build/` tree and run mkpsxiso as usual
(`testing/runJpnBuildTest.sh` style).

## Notes / gotchas surfaced in the UI

- Line breaks: radio (codec) text carries the literal `\r\n` escape (＃Ｎ in
  game bytes); demo/vox/zmovie use the fullwidth pipe `｜`. The editor shows
  real line breaks for both and converts back on save.
- Display limits: codec window is 4 lines; demo/vox/zmovie subtitles are 2
  lines. Wrap width defaults to 260 px everywhere (demo width is unconfirmed —
  tunable in Project settings).
- Calls over 65,535 bytes are allowed but flagged: they need the `-l`
  (4-byte length) build flag **and** the patched game executable.
- Kinsoku markers `‹BK›`/`‹TK›` are preserved as text — keep them with the
  character they follow.
- A line whose characters aren't in the tables will be encoded as dynamically
  allocated custom glyphs; the byte meter warns when that happens.
- Editing the start frame stores a `start` override key next to
  `duration`/`text`; the GUI's builder honors it (the legacy CLI injectors
  ignore it).
