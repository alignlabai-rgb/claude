#!/bin/sh
# Reproduce every log/result in this directory.  Usage:  sh run_all.sh SCRATCH_DIR   (needs python3.11, python3.12, python3.13 on PATH)
# - untouched default suite (tools/video-core/tests, 3 Python versions)  -> logs/default-suite-pyX.Y.log
# - contract suite (3 Python versions)                                   -> logs/contract-suite-pyX.Y.log  (+ observed JSON in SCRATCH)
# - EXPECTED_VS_OBSERVED.json, results/summary-*.json, rollback/ROLLBACK_EVIDENCE.json
# - mutation self-check (scratch copies only)                            -> results/MUTATION_SELF_CHECK.json
# Everything temporary lives under SCRATCH_DIR (VCC_TMP). Nothing outside this directory is written inside the repository.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
VC=$(cd "$HERE/../.." && pwd)
SCRATCH=${1:?scratch dir}
mkdir -p "$SCRATCH/tmp" "$SCRATCH/emit" "$HERE/logs" "$HERE/results"
export PYTHONDONTWRITEBYTECODE=1 VCC_TMP="$SCRATCH/tmp"
for v in 3.11 3.12 3.13; do
  ( cd "$VC" && { echo "# command: python$v -m unittest discover -s tests -t .   (cwd tools/video-core; untouched default suite)"; python$v --version; echo "# started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; python$v -m unittest discover -s tests -t . 2>&1; echo "# exit: $?"; } > "$HERE/logs/default-suite-py$v.log" 2>&1 ) &
done
wait
for v in 3.11 3.12 3.13; do
  ( cd "$HERE" && { echo "# command: python$v contract_suite.py --emit <scratch>   (cwd evidence/integration-contract-20261105)"; python$v --version; echo "# started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; python$v contract_suite.py --emit "$SCRATCH/emit" 2>&1; echo "# exit: $?"; } > "$HERE/logs/contract-suite-py$v.log" 2>&1 ) &
done
wait
python3 "$HERE/build_expected_vs_observed.py" "$SCRATCH/emit" --out-dir "$HERE" --default-logs "$HERE/logs"
if [ "${SKIP_MUTATION:-0}" != "1" ]; then
  python3 "$HERE/mutation_self_check.py" "$SCRATCH/mut" --out "$HERE/results/MUTATION_SELF_CHECK.json" > "$HERE/logs/mutation-self-check.log" 2>&1; echo "# exit: $?" >> "$HERE/logs/mutation-self-check.log"
fi
