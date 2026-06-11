"""
Core paths + project configuration for the Undub Studio GUI.

The GUI lives inside the mgs1-scripts repository (gui/), so the scripts
directory is always the parent of this package's parent.
"""

import os
import sys
import json

GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(GUI_DIR)
STATIC_DIR = os.path.join(GUI_DIR, "static")
PYTHON = sys.executable
LAST_PROJECT_FILE = os.path.join(GUI_DIR, ".last-project")

# Make the toolkit importable (translation.radioDict, demoClasses, etc.)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


DEFAULT_CONFIG = {
    "name": "",
    "root": "",            # disc folder the user opened
    "radioDat": "",
    "demoDat": "",
    "voxDat": "",
    "zmovieStr": "",
    "stageDir": "",
    "workspace": "",       # where extracted/edited/built files live
    "fps": 30,             # tick rate for time hints (~30 nominal, ~26-27 measured)
    "previewWidthRadio": 260,   # codec window wrap width in px
    "previewWidthDemo": 260,    # demo/vox/zmovie wrap width (unconfirmed; tunable)
    "mtProvider": "none",  # none | deepl | google | libre
    "mtApiKey": "",
    "mtUrl": "",           # libretranslate endpoint
    "mtSource": "JA",
    "mtTarget": "EN",
    "radioFlags": {        # RadioDatRecompiler toggles
        "integral": False,
        "pad": False,
        "long": False,
        "doubleWidth": False,
    },
}

# Filenames we look for when scanning a disc folder
SCAN_TARGETS = {
    "radioDat": ("RADIO.DAT",),
    "demoDat": ("DEMO.DAT",),
    "voxDat": ("VOX.DAT",),
    "zmovieStr": ("ZMOVIE.STR",),
}


class Project:
    """A project = a disc's game files + a workspace directory + config."""

    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self.loaded = False

    # ── persistence ─────────────────────────────────────────────────────────

    @property
    def config_path(self):
        ws = self.config.get("workspace")
        return os.path.join(ws, "project.json") if ws else None

    def save(self):
        if not self.config_path:
            return
        os.makedirs(self.config["workspace"], exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)
        with open(LAST_PROJECT_FILE, "w", encoding="utf-8") as f:
            f.write(self.config["root"])

    def open(self, root: str) -> dict:
        """Open (or create) a project rooted at a disc folder."""
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            raise FileNotFoundError(f"Not a directory: {root}")

        workspace = os.path.join(root, "undub-workspace")
        existing = os.path.join(workspace, "project.json")
        if os.path.isfile(existing):
            with open(existing, encoding="utf-8") as f:
                cfg = json.load(f)
            self.config = {**dict(DEFAULT_CONFIG), **cfg}
            self.config["root"] = root
            self.config["workspace"] = workspace
        else:
            self.config = dict(DEFAULT_CONFIG)
            self.config["root"] = root
            self.config["name"] = os.path.basename(root)
            self.config["workspace"] = workspace
            self.config.update(scan_disc(root))
        self.loaded = True
        self.save()
        return self.config

    def update(self, fields: dict) -> dict:
        for key, val in fields.items():
            if key in DEFAULT_CONFIG:
                self.config[key] = val
        if self.loaded:
            self.save()
        return self.config

    # ── workspace helpers ────────────────────────────────────────────────────

    def ws(self, *parts) -> str:
        """Path inside the workspace; parent dirs are created."""
        path = os.path.join(self.config["workspace"], *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def out(self, *parts) -> str:
        return self.ws("out", *parts)

    def require(self, key: str) -> str:
        path = self.config.get(key, "")
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(
                f"'{key}' is not set or missing — set it on the Project page.")
        return path


def scan_disc(root: str, max_depth: int = 5) -> dict:
    """Walk a disc folder looking for the game files. Returns config fields."""
    found = {}
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
            continue
        # Don't descend into our own workspace output
        dirnames[:] = [d for d in dirnames
                       if d not in ("undub-workspace", ".git", "__pycache__")]
        for fname in filenames:
            upper = fname.upper()
            for key, names in SCAN_TARGETS.items():
                if upper in names and key not in found:
                    found[key] = os.path.join(dirpath, fname)
            # STAGE.DIR variants: STAGE.DIR, STAGE-j1.DIR, STAGE-u2.DIR ...
            if "stageDir" not in found and upper.startswith("STAGE") and upper.endswith(".DIR"):
                found["stageDir"] = os.path.join(dirpath, fname)
    return found


def missing_deps() -> list:
    """Third-party packages the toolkit needs, that aren't installed."""
    missing = []
    try:
        import progressbar  # noqa: F401
    except ImportError:
        missing.append({
            "pip": "progressbar2",
            "neededFor": "Radio extraction and recompilation (RadioDatTools / xmlModifierTools)",
        })
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append({
            "pip": "Pillow",
            "neededFor": "Codec preview with the real game font (fontTools)",
        })
    return missing


MISSING_DEPS = missing_deps()   # checked once at startup


def last_project_root() -> str:
    try:
        with open(LAST_PROJECT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


PROJECT = Project()
