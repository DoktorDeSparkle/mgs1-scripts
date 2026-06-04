#!/bin/bash
# Drive demoDialogueRoundTrip.py across all 3 versions x 2 discs.
# Splits each DEMO.DAT into kinsoku-demo/<disk>/bins/, then checks
# decode -> re-encode -> byte compare on every dialogue line.

set -u

source .venv/bin/activate

ROOT="kinsoku-demo"
mkdir -p "$ROOT"

DISKS=("jpn-d1" "jpn-d2" "usa-d1" "usa-d2" "integral-d1" "integral-d2")

declare -a SUMMARY

for DISK in "${DISKS[@]}"; do
    SRC="build-src/$DISK/MGS/DEMO.DAT"
    OUT_BINS="$ROOT/$DISK/bins"
    OUT_REPORT="$ROOT/$DISK/report.txt"
    OUT_JSON="$ROOT/$DISK/summary.json"

    echo ""
    echo "=============================="
    echo "  $DISK"
    echo "=============================="

    if [ ! -f "$SRC" ]; then
        echo "  SKIP (no $SRC)"
        SUMMARY+=("$DISK: SKIP (no source)")
        continue
    fi

    mkdir -p "$OUT_BINS"

    echo "[split] $SRC -> $OUT_BINS"
    python3 myScripts/DemoTools/demoSplitter.py "$SRC" "$OUT_BINS" >/dev/null

    echo "[check] $OUT_BINS"
    python3 myScripts/testing/demoDialogueRoundTrip.py \
        "$OUT_BINS" "$OUT_REPORT" --summary-json "$OUT_JSON"

    if [ -f "$OUT_JSON" ]; then
        LINE=$(python3 -c "import json; d=json.load(open('$OUT_JSON')); print(f\"{d['pass']}/{d['total']} ({d['pct']:.2f}%)\")")
        SUMMARY+=("$DISK: $LINE")
    fi
done

echo ""
echo "=============================="
echo "  Summary"
echo "=============================="
for line in "${SUMMARY[@]}"; do
    echo "  $line"
done
