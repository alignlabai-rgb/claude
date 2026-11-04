#!/bin/bash
# Runs the EXISTING default suite, unchanged, once per available interpreter, strictly one after another.
# Usage: run_baseline.sh <tools/video-core dir> <log dir>
set -u
cd "$1" || exit 2
export PYTHONDONTWRITEBYTECODE=1
for py in python3.11 python3.12 python3.13; do
  log="$2/baseline-default-suite-$py.log"
  { echo "== $($py --version 2>&1)"; echo "== cmd: $py -m unittest discover -s tests -t . (cwd tools/video-core)"; echo "== start: $(date -u +%FT%TZ)"; } > "$log"
  $py -m unittest discover -s tests -t . >> "$log" 2>&1
  echo "== exit: $?" >> "$log"
  echo "== end: $(date -u +%FT%TZ)" >> "$log"
done
