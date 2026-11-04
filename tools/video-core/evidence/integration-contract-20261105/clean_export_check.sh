#!/bin/sh
# Clean-export check: export a commit with `git archive` into a fresh directory, prove the export equals the commit's tree for this directory,
# prove nothing outside this directory differs from the base, and run the contract suite FROM THE EXPORT on every available Python.
# Usage:  sh clean_export_check.sh COMMIT SCRATCH_DIR   (run from anywhere inside the repository)
set -eu
COMMIT=${1:?commit}; SCRATCH=${2:?scratch dir}
BASE=b4ca4a3b031351a631c0fbe1eb149935f4db19ba
NEW=tools/video-core/evidence/integration-contract-20261105
ROOT=$(git rev-parse --show-toplevel)
EXP="$SCRATCH/export"; rm -rf "$EXP"; mkdir -p "$EXP" "$SCRATCH/tmp"
git -C "$ROOT" archive "$COMMIT" | tar -x -C "$EXP"
echo "export of $COMMIT -> $EXP"
echo "files in export: $(find "$EXP" -type f | wc -l)   files in commit tree: $(git -C "$ROOT" ls-tree -r --name-only "$COMMIT" | wc -l)"
echo "--- changed paths base..commit (must all be under $NEW/):"
git -C "$ROOT" diff --name-status "$BASE" "$COMMIT" | awk '{print $1, $2}' | sed -n '1,200p'
OUT=$(git -C "$ROOT" diff --name-only "$BASE" "$COMMIT" | grep -v "^$NEW/" || true)
[ -z "$OUT" ] && echo "OK: no path outside $NEW/ differs from base" || { echo "FAIL: outside paths:"; echo "$OUT"; exit 1; }
echo "--- stray files in export (pycache, tmp, logs outside logs/):"
find "$EXP/$NEW" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.tmp' -o -name '.DS_Store' \) | head
echo "--- verify_unchanged against the commit (blob ids):"
python3 "$EXP/$NEW/verify_unchanged.py" --repo "$ROOT" --ref "$COMMIT" | sed -n '1,14p'
for v in 3.11 3.12 3.13; do
  echo "--- contract suite from the export, Python $v"
  ( cd "$EXP/$NEW" && PYTHONDONTWRITEBYTECODE=1 VCC_TMP="$SCRATCH/tmp" python$v contract_suite.py 2>&1 | grep -E '^contract suite|^cases_digest|^FAIL|^ERROR' | cut -c1-200 )
done
echo "--- export left clean (no files written into it by the runs):"
find "$EXP" -newer "$EXP/$NEW/clean_export_check.sh" -type f | head -5
