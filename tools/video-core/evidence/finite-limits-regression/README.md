# Finite-limits regression (base `8f1e4c5b0dc26035b493dcf08c8aa24ec2cb707a`; author's Linux checks)

Reproduced first at `8f1e4c5` with constructor-only calls (no process launched): `Limits(max_runtime_seconds=nan)`, `=inf`, `Limits(probe_timeout_seconds=nan)`,
`Limits(inspect_timeout_seconds=inf)`, `Limits(max_drain_bytes=nan)`, `Limits(max_frames=100.0)`, `Limits(max_runtime_seconds=1e308)`, `AudioLimits(max_runtime_seconds=nan|inf)`,
`AudioLimits(max_mix_work=5.5)` were all **accepted**. After the fix all are rejected with `ValueError`; `0.25`, `0.5`, integer seconds and the documented ceilings still work.

* New: `video_core/numeric.py`; used by `Limits`, `AudioLimits` and `runner.run_bounded`.
* New tests: `tests/test_numeric_limits.py` — 22 tests: helper rules, every time limit and every integer limit against bad values, `.replace()`, schema ceilings preserved, runner arguments
  rejected with `subprocess.Popen` patched to raise (so no child, bounded or unbounded, is ever started), a short fractional timeout still bounding a sleeping owned child, video and audio CLI
  flags (`nan`, `inf`, `-inf`, `1e999`, `0`, negative, above the ceiling) giving exit 2 with nothing written, a render with `--max-runtime-seconds nan` launching no fake tool.
* Meaningfulness check: against the pre-fix `limits.py`, `audiomix/limits.py` and `runner.py` from `8f1e4c5`, 12 of these 22 tests fail (the rest assert that legitimate values and ceilings still work).
* Default suite: **342 tests, 0 failures, 0 errors, 3 opt-in skipped** on Python 3.11.18, 3.12.3, 3.13.18 (`default-suite.log`).

No render, baseline re-render, Windows run, or Stage B evidence change was part of this commit.
