# Integrity and clean-export validation

Checked at the end of the review (commands run from the repository root).

| check | command | result |
|---|---|---|
| branch/SHA at start | `git rev-parse HEAD` before any change | `b4ca4a3b031351a631c0fbe1eb149935f4db19ba` on `codex/video-core-adversarial-20261105` |
| nothing outside this directory changed | `git diff --stat b4ca4a3 -- . ':!tools/video-core/evidence/adversarial-review-20261105'` | empty |
| all 131 pre-existing tracked files of `tools/video-core` (source, six original specs, schemas, examples, tests, every prior receipt/evidence file) | SHA-256 list taken before the first run (`logs/pre-run-tracked-file-sha256.txt`) compared with a fresh list at the end | identical (diff empty) |
| only additions in the audit commits | `git diff --name-status b4ca4a3 HEAD -- tools/video-core` | `A` only; no `M`/`D`/`R` |
| no bytecode/media left in the tree | `find tools/video-core -name __pycache__`; `*.wav`, `*.mp4`, `*.partial` are git-ignored | removed / none retained |
| `codex/video-core-cloud-20261105` untouched | only `git push origin codex/video-core-adversarial-20261105` was issued | yes |

Clean-export validation (final commit, see git log for the exact SHA): `git archive <sha> tools/video-core` into a fresh empty directory outside the repository, then
1. the exported file list equals `git ls-tree -r <sha> tools/video-core` (651 files; four evidence file names that contained a raw non-UTF-8 byte from the F-ENC-1 fixture were renamed with a backslash-escaped form before the final commit so the tree is safe for any tool);
2. every file that existed at `b4ca4a3` is byte-identical in the export (`cmp`, no output);
3. `python3.13 run_audit.py` run from the export: 89 tests, 78 passed, 0 skipped, 0 failures, 0 errors, 11 expected failures (reproduced defects), 0 unexpected successes;
4. `FINDINGS.json` parses (10 validated defects: 2 MEDIUM, 8 LOW; 1 stub-only static concern; 11 reproducer tests).
