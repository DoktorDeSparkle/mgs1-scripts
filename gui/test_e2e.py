"""End-to-end smoke test for the GUI backend using a synthetic DEMO.DAT.

Builds a fake disc folder, opens a project, extracts demo dialogue, edits it,
rebuilds DEMO.DAT, and verifies the round trip. Run from mgs1-scripts/:
    python3 gui/test_e2e.py
"""

import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import core, demovox  # noqa: E402
import translation.radioDict as RD  # noqa: E402
from DemoTools import extractDemoVox as edv  # noqa: E402

ROOT = "/tmp/mgs-gui-test"


class FakeJob:
    def log_line(self, s):
        print("  |", s)

    def run_cmd(self, argv, cwd=None):
        raise AssertionError("no subprocess expected in this test")


def sub_block(subs):
    out = b""
    for j, (text, start, dur) in enumerate(subs):
        tb = RD.encodeJapaneseHex(text, "", bank=3)[0]
        body = struct.pack("<II", start, dur) + bytes(4) + tb
        rem = len(body) % 4
        body += bytes(4 - rem) if rem else bytes(4)
        if j < len(subs) - 1:
            out += struct.pack("<I", len(body) + 4) + body
        else:
            out += bytes(4) + body
    return out


def caption_chunk(start, end, subs):
    sb = sub_block(subs)
    header_len = 14
    dialogue_len = header_len + len(sb)
    total = 4 + dialogue_len
    return (struct.pack("<BH", 3, total) + b"\x00"
            + struct.pack("<II", start, end) + b"\x10\x00"
            + struct.pack("<HH", header_len, dialogue_len) + sb)


def entry(subs, start=0, end=10000):
    data = b"\x10\x08\x00\x00" + bytes(4)          # file header chunk
    data += caption_chunk(start, end, subs)
    data += b"\xf0" + bytes(3)                      # end marker
    pad = (-len(data)) % 0x800
    return data + bytes(pad)


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(os.path.join(ROOT, "MGS"))

    demo = entry([("こちらスネーク。", 100, 250), ("どうぞ。", 400, 120)])
    demo += entry([("オタコンだ。", 50, 200)])
    demo_path = os.path.join(ROOT, "MGS", "DEMO.DAT")
    with open(demo_path, "wb") as f:
        f.write(demo)

    project = core.Project()
    cfg = project.open(ROOT)
    assert cfg["demoDat"] == demo_path, cfg
    print("✓ project open + scan found DEMO.DAT")

    job = FakeJob()
    demovox.extract(project, job, "demo")
    data = demovox.get_data(project, "demo")
    assert data["extracted"]
    orig = data["original"]
    assert orig["demo-01"]["100"]["text"] == "こちらスネーク。", orig
    assert orig["demo-01"]["100"]["duration"] == "250"
    assert orig["demo-02"]["50"]["text"] == "オタコンだ。"
    print("✓ extraction:", json.dumps(orig, ensure_ascii=False))

    # edit: translate one line, retime another
    work = json.loads(json.dumps(orig))
    work["demo-01"]["100"]["text"] = "This is Snake. Do you read me, Otacon?"
    work["demo-01"]["400"]["duration"] = "180"
    demovox.save_work(project, "demo", work)
    total, changed = demovox.counts(project, "demo")
    assert (total, changed) == (3, 2), (total, changed)
    print("✓ working copy saved, counts =", total, changed)

    demovox.build(project, job, "demo")
    out_path = project.out("DEMO.DAT")
    assert os.path.isfile(out_path)
    with open(out_path, "rb") as f:
        rebuilt = f.read()
    assert len(rebuilt) == len(demo), (len(rebuilt), len(demo))
    print("✓ rebuilt DEMO.DAT, size preserved:", len(rebuilt))

    # entry 2 untouched → byte-identical region
    assert rebuilt[0x800 * (len(demo) // 0x800 - demo[0x800:].count(b"") and 1):] is not None
    second_start = edv.findOffsets(demo)[1]
    assert rebuilt[second_start:] == demo[second_start:], "untouched entry changed!"
    print("✓ untouched entry byte-identical")

    re_extracted = edv.extractFromFile(out_path, "demo")
    assert re_extracted["demo-01"]["100"]["text"].replace("\x00", "") == \
        "This is Snake. Do you read me, Otacon?", re_extracted
    assert re_extracted["demo-01"]["400"]["duration"] == "180"
    assert re_extracted["demo-02"]["50"]["text"] == "オタコンだ。"
    print("✓ round trip: edited text + timing present, JP text intact")

    with open(os.path.join(project.out("demo-blockmap.json"))) as f:
        bm = json.load(f)
    assert all(int(k) == v for k, v in bm.items()), bm
    print("✓ block map is identity (no STAGE.DIR changes needed)")

    print("\nALL OK")


if __name__ == "__main__":
    main()
