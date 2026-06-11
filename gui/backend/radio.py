"""
RADIO.DAT pipeline.

Extraction, injection and recompilation are delegated to the proven CLI
scripts (RadioDatTools.py, xmlModifierTools.py, RadioDatRecompiler.py) via
subprocess — they own the offset/length math. This module manages the
workspace files around them and the editable "working" dialogue JSON
(Iseeva format).
"""

import os
import json
import shutil
import xml.etree.ElementTree as ET

from . import core

RADIO_BASE = "RADIO"   # workspace/radio/RADIO.xml, RADIO-Iseeva.json


def paths(project):
    radio_dir = project.ws("radio", ".keep")
    radio_dir = os.path.dirname(radio_dir)
    base = os.path.join(radio_dir, RADIO_BASE)
    return {
        "xml": base + ".xml",
        "orig": base + "-Iseeva.json",
        "work": base + "-work.json",
        "voxPatchedXml": base + "-voxpatched.xml",
    }


def is_extracted(project) -> bool:
    p = paths(project)
    return os.path.isfile(p["xml"]) and os.path.isfile(p["orig"])


# ── extraction ───────────────────────────────────────────────────────────────

def _require_progressbar():
    try:
        import progressbar  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "the 'progressbar2' package is required for the radio pipeline — "
            "install the toolkit dependencies with:  pip install -r requirements.txt")


def extract(project, job):
    _require_progressbar()
    radio_dat = project.require("radioDat")
    p = paths(project)
    job.log_line(f"Extracting {radio_dat} → XML + dialogue JSON ...")
    job.run_cmd([
        core.PYTHON,
        os.path.join(core.SCRIPTS_DIR, "RadioDatTools.py"),
        radio_dat,
        os.path.splitext(p["xml"])[0],
        "-x", "-z",
    ])
    if not os.path.isfile(p["xml"]) or not os.path.isfile(p["orig"]):
        raise RuntimeError("extraction finished but expected output files are missing")
    if not os.path.isfile(p["work"]):
        shutil.copyfile(p["orig"], p["work"])
        job.log_line("Created working copy for translations.")
    _meta_cache.pop(p["xml"], None)
    job.log_line("Radio extraction complete.")


# ── call metadata from the XML (freq, length budget) ─────────────────────────

_meta_cache = {}


def meta(project) -> dict:
    """{callOffset(str): {freq, length, subtitles}} parsed from RADIO.xml."""
    p = paths(project)
    xml_path = p["xml"]
    if not os.path.isfile(xml_path):
        return {}
    mtime = os.path.getmtime(xml_path)
    cached = _meta_cache.get(xml_path)
    if cached and cached[0] == mtime:
        return cached[1]

    calls = {}
    try:
        root = ET.parse(xml_path).getroot()
        for call in root.findall("Call"):
            offset = call.get("offset")
            calls[offset] = {
                "freq": call.get("freq"),
                "length": int(call.get("length") or 0),
                "subtitles": len(call.findall(".//SUBTITLE")),
            }
    except ET.ParseError as exc:
        return {"_error": f"RADIO.xml parse error: {exc}"}
    _meta_cache[xml_path] = (mtime, calls)
    return calls


# ── working data ─────────────────────────────────────────────────────────────

def get_data(project) -> dict:
    p = paths(project)
    if not is_extracted(project):
        return {"extracted": False}
    with open(p["orig"], encoding="utf-8") as f:
        original = json.load(f)
    if os.path.isfile(p["work"]):
        with open(p["work"], encoding="utf-8") as f:
            work = json.load(f)
    else:
        work = original
    return {"extracted": True, "original": original, "work": work,
            "meta": meta(project)}


def save_work(project, work: dict):
    p = paths(project)
    with open(p["work"], "w", encoding="utf-8") as f:
        json.dump(work, f, ensure_ascii=False, indent=2)


def counts(project):
    """(total lines, translated lines) across all four Iseeva sections."""
    p = paths(project)
    if not is_extracted(project):
        return 0, 0
    try:
        with open(p["orig"], encoding="utf-8") as f:
            orig = json.load(f)
        work = orig
        if os.path.isfile(p["work"]):
            with open(p["work"], encoding="utf-8") as f:
                work = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0, 0

    total = changed = 0
    for call_off, voxes in orig.get("calls", {}).items():
        for vox_off, subs in voxes.items():
            for sub_off, text in subs.items():
                total += 1
                new = work.get("calls", {}).get(call_off, {}).get(vox_off, {}).get(sub_off)
                if new is not None and new != text:
                    changed += 1
    for section in ("freqAdd",):
        for key, text in orig.get(section, {}).items():
            total += 1
            if work.get(section, {}).get(key, text) != text:
                changed += 1
    for section in ("saves", "prompts"):
        for key, entries in orig.get(section, {}).items():
            for idx, text in entries.items():
                total += 1
                if work.get(section, {}).get(key, {}).get(idx, text) != text:
                    changed += 1
    return total, changed


# ── build ────────────────────────────────────────────────────────────────────

def apply_vox_blockmap(xml_in: str, xml_out: str, block_map: dict, job) -> int:
    """Rewrite VOX_CUES voxCode attributes for a relocated VOX.DAT.

    Same logic as StageDirTools/voxOffsetAdjuster.adjustRadioXml, but always
    writes to a copy so the pristine extraction XML is never modified.
    """
    tree = ET.parse(xml_in)
    replaced = 0
    for vox in tree.getroot().iter("VOX_CUES"):
        code = vox.get("voxCode")
        if not code:
            continue
        old = int(code, 16)
        if old == 0:               # other disc — leave alone
            continue
        new = block_map.get(old)
        if new is not None and new != old:
            vox.set("voxCode", f"{new:08x}")
            replaced += 1
    tree.write(xml_out, encoding="unicode", xml_declaration=True)
    job.log_line(f"voxCode update: {replaced} VOX references re-pointed.")
    return replaced


def build(project, job, stage_template: str | None):
    """inject work JSON → XML, recompile to out/RADIO.DAT (+ STAGE.DIR)."""
    _require_progressbar()
    p = paths(project)
    if not is_extracted(project):
        raise RuntimeError("Radio is not extracted yet — run extraction first.")
    if not os.path.isfile(p["work"]):
        shutil.copyfile(p["orig"], p["work"])

    # If VOX.DAT was rebuilt with moved entries, re-point voxCodes on a copy.
    xml_input = p["xml"]
    vox_map = load_blockmap(project, "vox")
    if vox_map:
        job.log_line("Applying VOX block map to a copy of RADIO.xml ...")
        apply_vox_blockmap(p["xml"], p["voxPatchedXml"], vox_map, job)
        xml_input = p["voxPatchedXml"]

    job.log_line("Injecting working dialogue into XML ...")
    job.run_cmd([
        core.PYTHON,
        os.path.join(core.SCRIPTS_DIR, "xmlModifierTools.py"),
        "inject", p["work"], xml_input,
    ])
    merged = xml_input.replace(".xml", "-merged.xml")
    if not os.path.isfile(merged):
        raise RuntimeError("inject step did not produce a -merged.xml file")

    out_dat = project.out("RADIO.DAT")
    argv = [
        core.PYTHON,
        os.path.join(core.SCRIPTS_DIR, "RadioDatRecompiler.py"),
        merged, out_dat,
    ]
    flags = project.config.get("radioFlags", {})
    if flags.get("integral"):
        argv.append("-I")
    if flags.get("pad"):
        argv.append("-P")
    if flags.get("long"):
        argv.append("-l")
    if flags.get("doubleWidth"):
        argv.append("-D")

    if stage_template and os.path.isfile(stage_template):
        argv += ["-s", stage_template, "-S", project.out("STAGE.DIR")]
    else:
        job.log_line("WARNING: no STAGE.DIR set — call offsets in STAGE.DIR "
                     "will NOT be updated. The game will likely play wrong "
                     "calls if call sizes changed.")

    job.log_line("Recompiling RADIO.DAT (lengths are recalculated automatically) ...")
    job.run_cmd(argv)
    if not os.path.isfile(out_dat):
        raise RuntimeError("recompiler did not produce RADIO.DAT")
    job.log_line(f"RADIO.DAT written: {out_dat} "
                 f"({os.path.getsize(out_dat):,} bytes)")


# ── block maps written by the demo/vox builders ──────────────────────────────

def blockmap_path(project, kind: str) -> str:
    return project.out(f"{kind}-blockmap.json")


def load_blockmap(project, kind: str) -> dict:
    """{old_block(int): new_block(int)} or {} — only differing entries."""
    path = blockmap_path(project, kind)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): int(v) for k, v in raw.items() if int(k) != int(v)}
