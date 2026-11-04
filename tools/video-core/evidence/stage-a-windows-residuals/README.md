# Stage A evidence — Windows residuals (base `6206b1da46f8a46d210c32a898ed17cbe7769e5e`)

## Author checks (this Linux cloud session; not Windows)

Default suite, Linux x86_64: **200 tests, 0 failures, 0 errors, 3 skipped (opt-in real-FFmpeg tests) on Python 3.11.18, 3.12.3 and 3.13.18**
(`default-suite.log`). Baseline plan hash `4bc20b3f…9f21` is pinned by a test and unchanged. The six-second baseline and the Windows smoke were **not**
rendered again; no render of any kind was run for this stage.

## Requester-reported Windows facts this stage responds to (not reproduced here)

* Windows default suite at `6206b1d`: 194 run, 147 passed, 45 skipped, 1 failure + 1 error — both in `tests/test_argv_budget.py`:
  an absolute `< 40000` estimate assertion that observed 41486 under a long sandbox TEMP prefix, and a test that created three 100-character directories and
  failed with WinError 3 (long paths off).
* Owned-Python Windows launch with one argument of 17000 non-BMP characters: Python length 17106, UTF-16 units 34106, UTF-8 bytes 68106, WinError 206.
* The original MP4 downloaded through the Desktop media menu matched 7665 B / SHA-256 `25d80ca4…5ecf`; Windows ffprobe: 640x360, 30/1, 180 frames, 6 s, full decode exit 0.
* The separate 320x180 / 24/1 / 12-frame Windows smoke rendered once with `READBACK_MATCH` on FFmpeg 8.1.2.

## What changed and how it is tested here

| item | change | Linux tests |
|---|---|---|
| command-line unit | `limits.cmdline_utf16_units` counts UTF-16 code units; lone surrogate = 1 unit, never raises; ill-formed text rejected on Windows only | `tests/test_argv_budget.py::Units`, `::Thresholds` (BMP CJK, non-BMP, quotes, backslashes, mixed; exact accept at the limit and reject at limit+1; the reported 17000-emoji shape with a shorter executable) |
| estimate test 1 | asserts the plan estimate equals the really materialized argv (so no per-file slack) and that a limit equal to it passes while one byte less rejects — independent of TEMP length | `PlanBudget::test_estimate_equals_the_real_argv_so_there_is_no_per_file_slack` |
| estimate test 2 | long-root arithmetic with pure strings (`estimate_argv`), no directory deeper than the temp dir is created; Windows long paths untouched | `PlanBudget::test_estimate_scales_exactly_with_root_length_without_creating_long_paths` |
| unchanged | timeout classification, bounded runtime, no-retry, unrelated-bystander survival, baseline plan hash | `tests/test_runner.py`, `tests/test_argv_budget.py::PlanBudget::test_baseline_plan_hash_is_unchanged` |

Not verified by the author: any Windows execution. The requester's Windows retest of this commit is the verification of the Windows behaviour.
