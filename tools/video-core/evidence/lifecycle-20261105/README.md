# Lifecycle fixes: before/after reproducers (F-RUN-1, F-RUN-2, F-INT-1, F-RCPT-1)

Scope: `runner.py`, `tools.py`, `readback.py`, `render.py`, `audiomix/mix.py`, `avmux/mux.py` (+ `avmux/reinspect.py` one line), new `finalize.py`, tests, additive docs.
Not touched: the six input specs, historical evidence, CLI files, `receipt.py`, `paths.py`, manifests, audiomix/avmux plan modules (owned by `codex/video-core-input-contract-20261105`).

* `repro_lifecycle.py` — one script, run against two trees: `before.json` (an export of `f42c2db`, the parent) and `after.json` (this commit).
  Bounds: each scenario <= 10 s outer, children are tiny `python -c` programs or the suite's FAKE ffmpeg/ffprobe (never a real tool, never media), a pipe flood is <= 2 MB
  and discarded, no allocation beyond a few MB. Linux only; nothing here is Windows evidence. Signals go only to this process or to groups the runner owns.

| Finding | before (`f42c2db`) | after |
|---|---|---|
| F-RUN-1 exit-first flood | 5/5 `EXITED_ZERO`, `ok: true`, 2,000,000 bytes accepted under a 1000-byte bound | 5/5 `EXITED_ZERO` (exit 0 kept), `ok: false`, `drain_limit_exceeded: true`, `kill_attempted: false`; lingering control still `FLOOD_KILLED` with `kill_attempted/kill_delivered: true` |
| F-RUN-2 interrupt in the final drain wait | `KeyboardInterrupt` escaped, grandchild still alive | returns `INTERRUPTED` (exit 0 preserved), grandchild dead after 0.67 s |
| F-INT-1 cancel at `ffmpeg -version` | returned `SKIPPED_TOOL_UNAVAILABLE`, 1 more child launched | `KeyboardInterrupt` (CLI: 130), 0 more children, nothing written |
| F-INT-1 cancel at preflight ffprobe | `ValidationFailed preflight_probe_failed`, 1 more child | `KeyboardInterrupt`, 0 more children |
| F-INT-1 cancel in a readback decode | `INSPECTION_FAILED` (in the on-disk receipt too), 1 more child | `INTERRUPTED` in the returned and on-disk receipt, 0 more children |
| F-RCPT-1 exception after the mux | `outputs: []`, no `sha256_after`, no `tmp_cleanup`, `tmp/` left | `outputs` lists `output.mov.partial`, `plan.canonical.json`, `fake_observed.json` (unrecognised), `sha256_after` present, `tmp_cleanup: removed` |

The audit's own reproducer for F-RUN-2 asserted that `KeyboardInterrupt` escapes `run_bounded`; the specified API converts an interrupt into an `INTERRUPTED` result (unchanged), so the
regression test (`tests/test_runner_lifecycle.py::InterruptCleanup`) asserts that outcome plus "no member of the owned group survives" and "an unrelated sentinel process survives".

Tests added: `tests/test_runner_lifecycle.py` (runner), `tests/test_lifecycle_orchestrators.py` (orchestrators, CLI exit 130, finalization), support `tests/lifecycle_support.py`.
Suite logs for this commit: `default-suite.lifecycle.log`.

## Follow-up: Windows scheduler-sensitive tests and termination provenance

Kept unchanged: the 59db31e results above. Windows reported (by the requester, 43 targeted tests: 10 pass, 31 skip, 2 fail): (1) `test_lingering_child_is_killed_and_both_facts_are_recorded` assumed POSIX
`exit_code None` (Windows: TerminateProcess ⇒ exit code 1, no signal); (2) the fast-exit-flood test assumed `EXITED_ZERO`/no kill on every real scheduling (Windows saw actual exit 0 with `FLOOD_KILLED`),
and a real provenance defect (`_signal_group` True after a no-op `terminate()`). Both tests are now platform-aware / invariant-based (not skipped), and the provenance is fixed in `runner._request`
(see LIMITATIONS.md "Termination provenance"). Linux evidence only from this session; the Windows branch is covered with fakes (logic only).

## Portable lifecycle harness (test-only)

`tests/portable_lifecycle.py`, `tests/test_lifecycle_portable.py`: 29 tests, 1 explicit skip (UNVERIFIED real signal delivery), meant to run on Windows and Linux. See LIMITATIONS.md "Portable lifecycle harness" for the real / mapped / injected / unverified split.
