"""
DEMO.DAT dialogue round-trip checker.

For each .dmo bin in an extracted DEMO.DAT, locate every dialogue text area,
decode each line to a string using the per-call custom-char dictionary
(derived in-place from the .dmo's graphics tiles), re-encode it, and compare
the new bytes to the original dialogue bytes.

Per-call dict is NOT persisted in JSON anywhere, but it lives inside the
.dmo file (the graphics blob at the tail of each text area), so we just
rebuild it on the fly during the encode step.

Usage (from project root):
    python3 myScripts/testing/demoDialogueRoundTrip.py <bins_dir> <report_path>
"""

import os
import sys
import glob
import struct
import argparse
import json

sys.path.append(os.path.abspath('./myScripts'))

import translation.radioDict as RD
import DemoTools.demoTextExtractor as DTE


# Silence the very chatty per-offset prints inside the extractor module.
DTE.debug = False
RD.debug = False


parser = argparse.ArgumentParser(description='DEMO.DAT dialogue round-trip checker.')
parser.add_argument('input', type=str, help='Directory containing extracted .dmo files.')
parser.add_argument('report', type=str, help='Path to write the per-line report.')
parser.add_argument('--summary-json', type=str, default=None,
                    help='Optional path to also dump a JSON summary.')


def check_dmo(path: str):
    """Returns (lines_total, lines_pass, mismatches[]).

    mismatches[] entries are dicts: {file, area, idx, orig_hex, new_hex, text}.
    """
    with open(path, 'rb') as f:
        data = f.read()

    fname = os.path.basename(path)
    text_offsets = DTE.getTextAreaOffsets(data)

    total = 0
    passed = 0
    mismatches = []

    for area_idx, offset in enumerate(text_offsets):
        subset = DTE.getTextAreaBytes(offset, data)
        hexes, graphics, _coords = DTE.getTextHexes(subset)

        # graphics is the raw 36-byte-tile blob for this call; build dict for
        # decode and pass .hex() to the encoder as its `callDict` (string form).
        DTE.filename = fname  # makeCallDictionary signature requires a name
        call_dict = RD.makeCallDictionary(fname, graphics) if graphics else {}
        graphics_hex = graphics.hex() if graphics else ''

        for line_idx, orig in enumerate(hexes):
            total += 1
            try:
                text = RD.translateJapaneseHex(orig, call_dict)
                text = text.replace('\x00', '')
                new_bytes, _ = RD.encodeJapaneseHex(text, callDict=graphics_hex, bank=3)
            except Exception as e:
                mismatches.append({
                    'file': fname,
                    'area': area_idx,
                    'idx': line_idx,
                    'orig_hex': orig.hex(),
                    'new_hex': f'EXCEPTION: {e}',
                    'text': '',
                })
                continue

            # orig is sliced [offset+16: offset+textSize] in getTextHexes(),
            # so it carries the 4-byte alignment padding (trailing 0x00) that
            # belongs to the block layout, not the dialogue stream. Strip it
            # before comparing so the report surfaces only real round-trip
            # divergences.
            orig_trimmed = orig.rstrip(b'\x00')
            if new_bytes == orig_trimmed:
                passed += 1
            else:
                mismatches.append({
                    'file': fname,
                    'area': area_idx,
                    'idx': line_idx,
                    'orig_hex': orig_trimmed.hex(),
                    'new_hex': new_bytes.hex(),
                    'text': text,
                })

    return total, passed, mismatches


def main(args=None):
    if args is None:
        args = parser.parse_args()

    bin_files = glob.glob(os.path.join(args.input, '*.dmo'))
    bin_files.sort(key=lambda f: int(f.split('-')[-1].split('.')[0]))

    if not bin_files:
        print(f'No .dmo files found in {args.input}')
        return 1

    os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)

    grand_total = 0
    grand_pass = 0
    per_file = []
    all_mismatches = []

    for path in bin_files:
        total, passed, mismatches = check_dmo(path)
        grand_total += total
        grand_pass += passed
        per_file.append({
            'file': os.path.basename(path),
            'lines': total,
            'pass': passed,
            'fail': total - passed,
        })
        all_mismatches.extend(mismatches)

    pct = (grand_pass / grand_total * 100) if grand_total else 0.0

    with open(args.report, 'w', encoding='utf-8') as out:
        out.write(f'DEMO.DAT dialogue round-trip: {args.input}\n')
        out.write(f'Total lines: {grand_total}   Pass: {grand_pass}   '
                  f'Fail: {grand_total - grand_pass}   ({pct:.2f}%)\n\n')

        out.write('Per-file:\n')
        for row in per_file:
            marker = 'OK  ' if row['fail'] == 0 else 'FAIL'
            out.write(f'  [{marker}] {row["file"]:>10}  '
                      f'lines={row["lines"]:>3}  pass={row["pass"]:>3}  '
                      f'fail={row["fail"]:>3}\n')

        if all_mismatches:
            out.write('\nMismatches:\n')
            for m in all_mismatches:
                out.write(f'\n  {m["file"]}  area={m["area"]} idx={m["idx"]}\n')
                out.write(f'    text: {m["text"]!r}\n')
                out.write(f'    orig: {m["orig_hex"]}\n')
                out.write(f'    new : {m["new_hex"]}\n')

    print(f'{args.input}: {grand_pass}/{grand_total} ({pct:.2f}%) '
          f'-> {args.report}')

    if args.summary_json:
        with open(args.summary_json, 'w') as f:
            json.dump({
                'input': args.input,
                'total': grand_total,
                'pass': grand_pass,
                'fail': grand_total - grand_pass,
                'pct': pct,
                'per_file': per_file,
                'mismatch_count': len(all_mismatches),
            }, f, indent=2)

    return 0 if grand_pass == grand_total else 2


if __name__ == '__main__':
    sys.exit(main())
