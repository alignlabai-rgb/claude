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

Clean-export validation: the commit was exported with `git archive` into a fresh empty directory (outside the repository) and `run_audit.py` was executed from that export on Python 3.13 (default mode). Result is appended below by the final step.
