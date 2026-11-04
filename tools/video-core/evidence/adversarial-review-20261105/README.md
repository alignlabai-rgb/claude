# adversarial-review-20261105

Separate automated adversarial review of `tools/video-core` at `b4ca4a3` (branch `codex/video-core-adversarial-20261105`). Start with **HANDOFF.md** (EN/JA), then **REPORT.md**.

| file | purpose |
|---|---|
| `HANDOFF.md` | concise English / Japanese handoff |
| `REPORT.md` | results table, defects with source lines, patch proposals, unverified list |
| `FINDINGS.json` | machine-readable findings (reproducibility, scope, fixtures, commands, fixes) |
| `REQUIREMENTS_COVERAGE.md` | which claims were really tested, partly, statically, or not |
| `INTEGRITY.md` | proof that source/specs/prior evidence are unchanged + clean-export check |
| `adv_oracle.py` | independent arithmetic/format oracle (imports nothing from `video_core`) |
| `adv_support.py`, `adv_fake_tools.py` | sandbox, audit-hook harness, known-defect marker, owned fake ffmpeg/ffprobe (canned text; not mux evidence) |
| `test_adv_lane{1,2,3,4}_*.py` | the executable tests (lanes: parsers/numeric · paths/outputs · timeline invariants · execution/receipts) |
| `run_audit.py`, `run_all.sh`, `run_baseline.sh` | runners (single run / 3 interpreters × 2 modes / existing suite unchanged) |
| `logs/` | exact logs: baseline, final matrix, `attempts/` (failed and superseded runs) |
| `runs/<label>/` | per-run `results.json`, `observations.jsonl` (seeds, counts, observed values), `cases/` evidence |
| `probes/` | exploratory scripts that preceded the tests, with outputs |

Everything here is new; nothing outside this directory was changed. `*.wav`/`*.mp4`/`*.partial` are git-ignored and none is retained.
