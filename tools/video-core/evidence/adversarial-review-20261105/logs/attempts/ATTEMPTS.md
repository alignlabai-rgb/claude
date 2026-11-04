# Failed / superseded attempts (kept on purpose)

| path | what happened | why it was superseded |
|---|---|---|
| `first-matrix-runs/`, `audit-python3.1*-{default,strict}.log` | First full 3-interpreter x 2-mode matrix (86 tests). 5 of 6 runs ended with **1 ERROR**: the F-RUN-2 scenario did not run. | The suite had been started with `&` from a non-interactive shell, so child Python processes inherited SIGINT=SIG_IGN and never installed a KeyboardInterrupt handler; the in-scenario `os.kill(getpid(), SIGINT)` was ignored. The `HarnessError` guard (added after an earlier IndentationError was silently counted as an "expected failure") reported it instead of hiding it. Fixed by installing `signal.default_int_handler` inside the scenario and by synchronising the signal with the pipe-holder marker file (not a fixed 1.0 s timer). Verified with `trap "" INT`. |
| `matrix-2-86tests-superseded/` | Complete, green matrix (86 tests: 77 passed, 9 reproduced defects, 0 errors) | Superseded after 3 more tests (audio-receipt input lane) were added; the final matrix in `../` covers all tests. Kept as evidence that the harness fix, not a code change, removed the ERROR. |

Other harness defects found while building the suite are listed in `FINDINGS.json` -> `test_harness_defects_found_and_fixed` (H1-H7).

Size note: the per-case `cases/` evidence copies of these superseded runs (≈33 MB, thousands of small receipt copies) were deleted before commit; `results.json`, `observations.jsonl` and the full text logs of every attempt are kept. The final matrix keeps `runs/py3.13-default/cases/` (full evidence); the other five final runs keep `results.json` + `observations.jsonl` only.
