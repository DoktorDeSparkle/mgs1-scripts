"""
DEMO.DAT / VOX.DAT / ZMOVIE.STR pipelines.

These use the toolkit's importable modules directly:
  - DemoTools.extractDemoVox    (extraction, offset scanning)
  - demoClasses                 (parse + patch-in-place serialization)
  - zmovieTools.extractZmovie   (zmovie extract + compile)

STAGE.DIR block-reference patching mirrors StageDirTools/demoOffsetAdjuster.py
(Ps pattern) and voxOffsetAdjuster.py (Pv pattern) but works on explicit paths
instead of the hardcoded ones in those scripts.
"""

import os
import json
import struct
import shutil
import contextlib
import io

from . import core, radio

# Toolkit imports (core put SCRIPTS_DIR on sys.path)
import demoClasses
from DemoTools import extractDemoVox as edv
from zmovieTools import extractZmovie as ezm

BLOCK = 0x800

SRC_KEY = {"demo": "demoDat", "vox": "voxDat", "zmovie": "zmovieStr"}
OUT_NAME = {"demo": "DEMO.DAT", "vox": "VOX.DAT", "zmovie": "ZMOVIE.STR"}


def paths(project, kind: str):
    base = os.path.dirname(project.ws(kind, ".keep"))
    return {
        "orig": os.path.join(base, f"{kind}Text.json"),
        "work": os.path.join(base, f"{kind}Text-work.json"),
    }


def is_extracted(project, kind: str) -> bool:
    return os.path.isfile(paths(project, kind)["orig"])


# ── extraction ───────────────────────────────────────────────────────────────

def extract(project, job, kind: str):
    src = project.require(SRC_KEY[kind])
    p = paths(project, kind)
    job.log_line(f"Extracting dialogue from {src} ...")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if kind == "zmovie":
            data = ezm.extractFromFile(src)
        else:
            data = edv.extractFromFile(src, kind)
    for line in buf.getvalue().splitlines():
        job.log_line(line)

    with open(p["orig"], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if not os.path.isfile(p["work"]):
        shutil.copyfile(p["orig"], p["work"])
        job.log_line("Created working copy for translations.")
    total = sum(len(v) for v in data.values())
    job.log_line(f"{kind} extraction complete: {len(data)} entries, {total} lines.")


# ── working data ─────────────────────────────────────────────────────────────

def get_data(project, kind: str) -> dict:
    p = paths(project, kind)
    if not is_extracted(project, kind):
        return {"extracted": False}
    with open(p["orig"], encoding="utf-8") as f:
        original = json.load(f)
    work = original
    if os.path.isfile(p["work"]):
        with open(p["work"], encoding="utf-8") as f:
            work = json.load(f)
    return {"extracted": True, "original": original, "work": work}


def save_work(project, kind: str, work: dict):
    with open(paths(project, kind)["work"], "w", encoding="utf-8") as f:
        json.dump(work, f, ensure_ascii=False, indent=2)


def counts(project, kind: str):
    if not is_extracted(project, kind):
        return 0, 0
    data = get_data(project, kind)
    orig, work = data["original"], data["work"]
    total = changed = 0
    for entry, subs in orig.items():
        for key, sub in subs.items():
            total += 1
            wsub = work.get(entry, {}).get(key)
            if wsub and _sub_differs(sub, wsub):
                changed += 1
    return total, changed


def _sub_differs(orig_sub: dict, work_sub: dict) -> bool:
    if work_sub.get("text") != orig_sub.get("text"):
        return True
    if work_sub.get("duration") != orig_sub.get("duration"):
        return True
    if work_sub.get("start") not in (None, ""):
        return True
    return False


# ── demo / vox build ────────────────────────────────────────────────────────

def _entry_name(kind: str, index: int) -> str:
    return f"demo-{index + 1:02}" if kind == "demo" else f"vox-{index + 1:04}"


def _apply_edits(parsed_demo, edits: dict, job, key: str) -> int:
    """Mutate a parsed demo's caption subtitles from the working JSON."""
    applied = 0
    for seg in parsed_demo.segments:
        if not isinstance(seg, demoClasses.captionChunk):
            continue
        for sub in seg.subtitles:
            edit = edits.get(str(sub.startFrame))
            if not edit:
                continue
            sub.text = edit.get("text", sub.text)
            try:
                sub.displayFrames = int(edit.get("duration", sub.displayFrames))
            except (TypeError, ValueError):
                job.log_line(f"  WARNING {key}: bad duration "
                             f"'{edit.get('duration')}' at frame {sub.startFrame}, kept original")
            new_start = edit.get("start")
            if new_start not in (None, ""):
                try:
                    sub.startFrame = int(new_start)
                except (TypeError, ValueError):
                    job.log_line(f"  WARNING {key}: bad start '{new_start}', kept original")
            applied += 1
    return applied


def build(project, job, kind: str):
    """Rebuild DEMO.DAT/VOX.DAT with edited captions, patch-in-place style.

    Writes out/<KIND>.DAT and a block map (old block → new block) consumed by
    the STAGE.DIR assembly step. Entries normally keep their original size
    (the toolkit pads/trims), so the map is usually an identity map.
    """
    src = project.require(SRC_KEY[kind])
    data_set = get_data(project, kind)
    if not data_set.get("extracted"):
        raise RuntimeError(f"{kind} is not extracted yet — run extraction first.")
    orig_json, work = data_set["original"], data_set["work"]

    with open(src, "rb") as f:
        data = f.read()
    offsets = edv.findOffsets(data)
    if not offsets:
        raise RuntimeError(f"no entries found in {src} — is this a {OUT_NAME[kind]}?")
    job.log_line(f"{len(offsets)} entries in source {OUT_NAME[kind]}.")

    out = bytearray()
    block_map = {}
    modified = grown = 0

    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(data)
        entry = data[start:end]
        key = _entry_name(kind, i)
        block_map[start // BLOCK] = len(out) // BLOCK

        edits = work.get(key)
        needs_edit = bool(edits) and any(
            _sub_differs(orig_json.get(key, {}).get(k, {}), v)
            for k, v in edits.items())

        if needs_edit:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                parsed = demoClasses.demo(0, entry)
                applied = _apply_edits(parsed, edits, job, key)
                new_entry = parsed.getModifiedBytes(entry)
            for line in buf.getvalue().splitlines():
                if line.strip():
                    job.log_line(f"  [{key}] {line}")
            job.log_line(f"  {key}: {applied} lines injected "
                         f"({len(entry)} → {len(new_entry)} bytes)")
            if len(new_entry) != len(entry):
                grown += 1
            modified += 1
            out += new_entry
        else:
            out += entry

    out_path = project.out(OUT_NAME[kind])
    with open(out_path, "wb") as f:
        f.write(bytes(out))
    job.log_line(f"{OUT_NAME[kind]} written: {out_path} ({len(out):,} bytes, "
                 f"{modified} entries modified)")

    with open(radio.blockmap_path(project, kind), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in block_map.items()}, f, indent=2)

    moved = sum(1 for k, v in block_map.items() if k != v)
    if moved:
        job.log_line(f"NOTE: {moved} entries moved to new blocks "
                     f"({grown} entries changed size). STAGE.DIR references "
                     "will be re-pointed when STAGE.DIR is assembled.")
    else:
        job.log_line("All entries kept their original positions — no STAGE.DIR "
                     "changes needed for this file.")


# ── STAGE.DIR patching ───────────────────────────────────────────────────────

def patch_stage_demo(stage: bytearray, block_map: dict) -> int:
    """Re-point Ps (demo) references. Mirrors demoOffsetAdjuster.py:
    pattern 'Ps\\x06\\x0a' with 'Pp\\x04\\x08' eight bytes later; the 4 bytes
    between are a big-endian DEMO.DAT block index."""
    replaced = 0
    i = stage.find(b"Ps\x06\x0a")
    while i != -1:
        if stage[i + 8:i + 12] == b"Pp\x04\x08":
            old = struct.unpack(">I", stage[i + 4:i + 8])[0]
            new = block_map.get(old)
            if new is not None and new != old:
                stage[i + 4:i + 8] = struct.pack(">I", new)
                replaced += 1
        i = stage.find(b"Ps\x06\x0a", i + 1)
    return replaced


def patch_stage_vox(stage: bytearray, block_map: dict) -> int:
    """Re-point Pv (vox) references. Mirrors voxOffsetAdjuster.py:
    'Pv' + payload size byte; payload holds 0x0a-prefixed 4-byte big-endian
    VOX.DAT block indices. 0xFFFFFFFF is a sentinel and is skipped."""
    replaced = 0
    offset = 0
    while offset < len(stage) - 2:
        if stage[offset:offset + 2] != b"Pv":
            offset += 1
            continue
        payload_size = stage[offset + 2]
        pos = offset + 3
        payload_end = pos + payload_size
        if payload_end > len(stage):
            offset += 1
            continue
        while pos < payload_end:
            if stage[pos] == 0x0a and pos + 5 <= payload_end:
                old = struct.unpack(">I", stage[pos + 1:pos + 5])[0]
                if old != 0xFFFFFFFF:
                    new = block_map.get(old)
                    if new is not None and new != old:
                        stage[pos + 1:pos + 5] = struct.pack(">I", new)
                        replaced += 1
                pos += 5
            else:
                pos += 1
        offset = payload_end
    return replaced


def assemble_stage(project, job) -> str | None:
    """Build a STAGE.DIR template: pristine source + demo/vox block maps.

    Returns the path of the patched template (out/_stage-template.DIR), or
    None when no STAGE.DIR is configured. The radio recompiler then uses it
    as its '-s' input and writes the final out/STAGE.DIR with radio call
    offsets fixed on top.
    """
    stage_src = project.config.get("stageDir", "")
    if not stage_src or not os.path.isfile(stage_src):
        return None

    with open(stage_src, "rb") as f:
        stage = bytearray(f.read())

    demo_map = radio.load_blockmap(project, "demo")
    vox_map = radio.load_blockmap(project, "vox")
    if demo_map:
        n = patch_stage_demo(stage, demo_map)
        job.log_line(f"STAGE.DIR: re-pointed {n} demo references.")
    if vox_map:
        n = patch_stage_vox(stage, vox_map)
        job.log_line(f"STAGE.DIR: re-pointed {n} vox references.")
    if not demo_map and not vox_map:
        job.log_line("STAGE.DIR: no demo/vox relocations to apply.")

    template = project.out("_stage-template.DIR")
    with open(template, "wb") as f:
        f.write(bytes(stage))
    return template


def finalize_stage_without_radio(project, job):
    """If the user builds only demo/vox, still emit a usable out/STAGE.DIR."""
    template = assemble_stage(project, job)
    if template:
        final = project.out("STAGE.DIR")
        shutil.copyfile(template, final)
        job.log_line(f"STAGE.DIR written: {final} "
                     "(no radio offset changes — rebuild Radio if you also "
                     "edited codec calls)")


# ── zmovie ───────────────────────────────────────────────────────────────────

def build_zmovie(project, job):
    src = project.require("zmovieStr")
    data_set = get_data(project, "zmovie")
    if not data_set.get("extracted"):
        raise RuntimeError("zmovie is not extracted yet — run extraction first.")
    work = data_set["work"]

    # compileToFile expects {entry: {startFrame: {duration, text}}}; apply the
    # optional 'start' override by re-keying.
    dialogue = {}
    for entry, subs in work.items():
        fixed = {}
        for key, sub in subs.items():
            start = sub.get("start") or key
            fixed[str(start)] = {"duration": str(sub.get("duration", "0")),
                                 "text": sub.get("text", "")}
        dialogue[entry] = fixed

    with open(src, "rb") as f:
        original = f.read()
    out_path = project.out("ZMOVIE.STR")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ezm.compileToFile(out_path, original, dialogue)
    except ValueError as exc:
        raise RuntimeError(f"zmovie subtitle block too large: {exc}") from exc
    finally:
        for line in buf.getvalue().splitlines():
            job.log_line(line)
    job.log_line(f"ZMOVIE.STR written: {out_path} "
                 f"({os.path.getsize(out_path):,} bytes)")


def zmovie_capacity(project, entry_key: str, subs: dict) -> dict:
    """Bytes the entry's subtitle block would use vs. the entry's capacity."""
    src = project.require("zmovieStr")
    with open(src, "rb") as f:
        original = f.read()
    offsets = ezm.getEntryOffsets(original)
    try:
        index = int(entry_key.split("-")[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"bad zmovie entry key: {entry_key}")
    if index >= len(offsets):
        raise RuntimeError(f"entry {entry_key} not present in {src}")

    start = offsets[index]
    chunk_count = struct.unpack("<H", original[start + 0x0E:start + 0x10])[0] or 1
    capacity = 0x7D0 + (0x7E0 if chunk_count >= 2 else 0)

    fixed = {}
    for key, sub in subs.items():
        start_frame = sub.get("start") or key
        fixed[str(start_frame)] = {"duration": str(sub.get("duration", "0")),
                                   "text": sub.get("text", "")}
    try:
        used = len(ezm._buildSubBlock(fixed))
        error = None
    except Exception as exc:
        used = None
        error = str(exc)
    return {"used": used, "capacity": capacity, "chunks": chunk_count,
            "error": error}
