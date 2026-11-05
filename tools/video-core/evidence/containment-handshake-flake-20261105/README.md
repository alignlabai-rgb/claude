# `test_every_kind_of_bad_hello_is_refused_and_no_go_is_ever_sent` — cause, repair, evidence (2026-11-05)

Separate commit on top of `ddd405b`. Test-only: `tests/test_containment.py` (+ this evidence directory and a pointer in `../../INPUT_CONTRACT.md`). **No production file changed** (`runner.py`,
`containment/*` byte-identical; see `unchanged-check.txt`); no branch merged; no media, network, provider, auth, privilege or purchase. Linux x86_64 (4 CPUs), Python 3.11 / 3.12 / 3.13.

## Which variant, which response (from the retained failed traces)
`failed-attempts-as-recorded/` keeps all four failing clean-export runs, unedited except path scrubbing: tree `aae52b2` (commit `092b420`) py3.13 twice, tree `b688646` (commit `ddd405b`) py3.12 and
py3.11. All four are the same assertion in the `huge` mode, the variant that expects `hello_too_large`:

    ('CONTAINMENT_REFUSED', 'hello_unreadable', False, False) != ('CONTAINMENT_REFUSED', 'hello_too_large', False, False)

So the parent **did refuse**, `go_sent` was **False**, `target_may_have_started` was **False**: only the refusal *reason* differed. The failures were never a missing refusal or a GO.

## Cause (isolated, reproduced, not merely "load")
* Production: the real worker publishes `hello.json` atomically (`_worker.write_atomic` = temp file + `os.replace`). The parent polls `os.path.exists(hello.json)` every 10 ms and then does ONE
  `read(MAX_HELLO_BYTES + 1)` (64 KiB + 1). That is a sound protocol *given atomic publication*.
* Fixture defect: the stub's `garbage` and `huge` modes did `open(hello_path, "w").write(...)`. `open(...,"w")` makes the file visible **empty** before the write happens. If the parent's poll lands in that
  window it reads 0 bytes, which is invalid JSON → `hello_unreadable`. The test's assumption "a visible hello.json is complete" is the stub violating the protocol, i.e. a startup/ordering assumption in the fixture.
* Instrumented reproduction (`diagnose_huge_stub.py`, parent instrumented only inside the script; 300 runs per cell against the ORIGINAL stub of `ddd405b`): the parent's first read returned **0 bytes** in
  4 / 12 / 6 of 300 runs (py3.11 / 3.12 / 3.13) on an **idle** machine and 12 / 23 / 20 of 300 with 4 busy loops. Every one of those was `hello_unreadable`, `go_sent=False`, target not started; every other run was
  `hello_too_large`. Load raises the rate (it widens the window) but is not required: the cause is the non-atomic publish, and the same stub, atomic, never produced a 0-byte read
  (`diagnosis.REPAIRED-stub.*`: 1800/1800 `hello_too_large`).
* **Is the product defective? No.** For a worker that violates the atomic-publish protocol the parent fails closed (refusal, no GO, `abort` written, target never started). Treating a half-written hello as
  refusable is the safe policy; waiting for it to "become complete" would be a weaker one. Nothing here is OPEN in production.

## Repair (test-only; policy and expected refusal codes unchanged)
1. `garbage` and `huge` stub modes now publish with the same temp-file + `os.replace` protocol as the real worker, so a visible hello is complete and the matrix judges content: `hello_unreadable` for
   garbage, `hello_too_large` for huge, `limits_not_applied` for token/echo/weaker/missing/extra/unsupported/errors — **all expectations unchanged**, nothing loosened, nothing skipped.
2. The torn-publish behaviour that used to be an accident is now a deliberate, deterministic test: `test_a_torn_hello_is_refused_fail_closed_with_the_reason_of_what_was_visible_and_a_late_valid_one_gets_no_go`.
   Modes `torn_empty`, `torn_partial`, `torn_huge` make a hello.json that is atomically visible but incomplete (exactly 0 bytes / a 40-byte JSON prefix / 70001 bytes), **hold it there until the parent has
   acted** (test-owned event barrier: the stub waits for the parent's `abort`/`go` file, bounded at 5 s only to stop a broken run), then complete it with a hello that would have been valid. Expected: `hello_unreadable`,
   `hello_unreadable`, `hello_too_large`; `go_sent` False; target not run; refusal prompt (< 5 s). The real parent, handshake and refusal path run unmodified.
3. Negative controls (`test_the_matrix_assertions_have_teeth_...`): inside the test, production symbols are patched (`mock.patch`, restored) to model real losses of detection — `validate_hello` accepting
   everything (a wrong-token hello then earns a GO: `go_sent` True and the stub sees it; a weaker-limits hello likewise) and `MAX_HELLO_BYTES` made huge (the oversized hello is no longer reported
   `hello_too_large`). The same `assert_refused` the matrix uses must then raise `AssertionError`. `assert_refused` now also asserts first that a refusal was recorded at all (it previously died with a
   `TypeError` on `None` when refusal detection was lost).
4. Still covered, unchanged: silent worker (handshake timeout), early exit, token/identity/limit mismatches, extra/weaker/missing limits, unsupported, errors, private 0700 handshake directory, valid hello releases GO.

## Repeated matrix (finite, every iteration recorded, no retry-until-green; `repeat_matrix.py`, summaries in `matrix/`)
| tree | test | load | py3.11 | py3.12 | py3.13 |
|---|---|---|---|---|---|
| `ddd405b` (before) | `test_every_kind_of_bad_hello_...` ×60 | idle | 59 pass / **1 fail** | 60 / 0 | 60 / 0 |
| `ddd405b` (before) | same ×60 | 4 busy loops | 56 / **4** | 55 / **5** | 50 / **10** |
| repaired | whole `ParentHandshakeWithStubWorker` class (7 tests) ×100 | idle | 100 / 0 | 100 / 0 | 100 / 0 |
| repaired | same ×100 | 4 busy loops | 100 / 0 | 100 / 0 | 100 / 0 |

Before: 20 failures in 360 runs, **all 20** the identical `hello_unreadable != hello_too_large` assertion (`matrix/OLD-*.json` keep each trace). After: 0 in 600. The idle "before" rate is low (1/180) so the idle
comparison alone is weak; the evidence for the cause is the instrumented read-size histogram above, and the loaded comparison (20 vs 0) is statistically decisive.

## Full suite (unchanged discovery) and clean export — see `full-suite.AFTER.python*.log` and `clean-export-validation.txt`
Previous commit `ddd405b`: 620 total / 611 pass / 9 skip / 0 fail. Now: 622 total (+2: the torn test and the negative-control test) / 613 pass / 9 skip / 0 fail on each interpreter. The 9 skips are the pre-existing ones.

## Limits (kept distinct)
* The affected class is `skipUnless(POSIX and HAS_RESOURCE)`: it does not run on Windows (it is among the 160 skips of the native Windows full-suite run). **Nothing here is Windows evidence**, and no Windows job-object
  behaviour was exercised.
* These are STUB-worker tests of the parent's verification logic, not native containment evidence; native POSIX containment tests are unchanged and were not part of this repair.
* The torn modes use a 5 s outer bound for the hold; a parent starved for more than 5 s would let the stub complete the file first. Not observed in 1200 loaded runs; stated, not hidden.
* The idle failure rate before the repair was small (≈0.6% on the matrix, 1–4% in the 300-run probes); the repair is justified by the isolated mechanism, not by absence of recurrence.
* No real OS signal, resource-limit, or ffmpeg behaviour was involved or validated.
