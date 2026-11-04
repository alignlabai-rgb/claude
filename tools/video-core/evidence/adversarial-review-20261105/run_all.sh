#!/bin/bash
# Runs the adversarial-review suite on every available interpreter, in default and strict mode, strictly one run after another.
# Usage: run_all.sh <tools/video-core dir> <tmp root>
set -u
cd "$1/evidence/adversarial-review-20261105" || exit 2
export ADV_TMP_ROOT="$2" PYTHONDONTWRITEBYTECODE=1
mkdir -p logs
for py in python3.11 python3.12 python3.13; do
  for mode in default strict; do
    flag=""; [ "$mode" = strict ] && flag="--strict"
    log="logs/audit-$py-$mode.log"
    { echo "== $($py --version 2>&1)  mode=$mode"; echo "== cmd: $py run_audit.py $flag -v (cwd evidence/adversarial-review-20261105)"; echo "== start: $(date -u +%FT%TZ)"; } > "$log"
    $py run_audit.py $flag -v >> "$log" 2>&1
    echo "== exit: $?" >> "$log"
    echo "== end: $(date -u +%FT%TZ)" >> "$log"
  done
done
