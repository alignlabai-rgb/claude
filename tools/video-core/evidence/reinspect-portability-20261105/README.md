# Reinspect test portability and media-claim consistency — evidence (2026-11-05)

Follow-up commit on top of `91ec2d4` (not an amendment). Contract text: `../../INPUT_CONTRACT.md`, "Follow-up 3". Linux x86_64, Python 3.11 / 3.12 / 3.13. No real media, no real ffmpeg/ffprobe, no
network, purchase, provider, auth, privilege or runtime change (`runner.py`, `containment/`, `render.py`, `audiomix/mix.py`, `avmux/mux.py` untouched; the core branch c7da5ac9 was only read as a
test-helper reference, nothing merged).

## What was reported and what changed
Independent native-Windows run of `91ec2d4`, `tests.test_reinspect_original_receipt`: 14 total, 2 pass, 11 skip, 1 fail — `test_a_receipt_over_the_input_limit_is_refused_and_the_limit_itself_is_accepted`
got `SKIPPED_TOOL_UNAVAILABLE` because the fake ffprobe is a POSIX script. Cause: test fixture, not production validation.

| item | status |
|---|---|
| exact-byte input limit now asserted at parser level (no tool); reinspect over-limit rejection needs no tool | **FIXED (tests)** |
| malformed-receipt rejections (library + public `main(argv)`) portable: no-Popen guard, no POSIX executable, not skipped on any platform; interpreter-child check without a POSIX marker | **FIXED (tests)** |
| completion cases use `tests/portable_tools.py` (owned Python child through the real `run_bounded`); a tool problem fails, never skips; canary proves a child ran | **FIXED (tests)**, scope label: orchestration/parsing only |
| `media.partial=true`, `media.bytes=float(correct)`, `media.bytes=-1` accepted | **FIXED (consistency)**: neither ignored nor required by the contract — an oversight in "present-and-wrong is rejected"; absent still allowed |
| native Windows run of the repaired tests / of `mux()` through the adapter | **UNVERIFIED** (not run by this session) |
| real ffprobe, Windows executable-path handling, signals | **NOT COVERED** by any test here |
| self-consistent forged receipt; packet summary correctness | **OPEN by design** (accepted; summary recorded as `CLAIMED_UNVERIFIED`) |

## Files
| file | content |
|---|---|
| `windows_like_exec_simulation.py` | Linux **simulation** of the Windows symptom (fake tools non-executable, `os.access(X_OK)` true). Not a Windows run. |
| `old-test-91ec2d4.windows-like-simulation.python3.13.log` | the OLD test file under the simulation: reproduces `'SKIPPED_TOOL_UNAVAILABLE' != 'REINSPECTED'` (2 failures, 5 errors — on real Windows the other tests were POSIX-skipped). |
| `new-test.windows-like-simulation.python3.13.log` | the NEW test file under the same simulation: 22 tests OK, 0 skipped. |
| `new-test.native-posix.python3.13.log` | the new file on the native POSIX interpreter. |
| `media_claims_probe.py`, `media-claims.BEFORE-91ec2d4.json`, `media-claims.AFTER.json` | the media-claim observations (below) on `91ec2d4` and now. |
| `full-suite.AFTER.python3.{11,12,13}.log` | unchanged-discovery full suite, working tree. |
| `targeted.AFTER.log` | reinspect, avmux CLI/plan, input contract, inspect link-like, static guards on each interpreter. |
| `unchanged-check.txt`, `clean-export-validation.txt` | byte-identity of specs/schemas/examples/prior evidence/runtime files; suites on a clean `git archive` export of the staged tree. |

## Media-claim behaviour, before → after (`read_original_receipt`, media size 25 bytes passed)
| claim | `91ec2d4` | now |
|---|---|---|
| exact claims (control) / no `partial`,`bytes`,`name` (legacy) | accepted | accepted |
| `media.partial = true` | accepted | `original_receipt_inconsistent` |
| `media.bytes = 25.0` (float of the right size) | accepted | `original_receipt_shape` |
| `media.bytes = -1` / `true` | accepted | `original_receipt_shape` |
| `media.bytes = 26` (another size) | accepted | `original_receipt_media_mismatch` |

## Counts (separate total / pass / skip / fail)
| run | total | pass | skip | fail |
|---|---|---|---|---|
| `91ec2d4`, py3.11/3.12/3.13 | 612 | 603 | 9 | 0 |
| working tree, py3.11/3.12/3.13 (each) | 620 | 611 | 9 | 0 |
| `tests.test_reinspect_original_receipt` alone, native POSIX (each interpreter) | 22 | 22 | 0 | 0 |
| targeted set (`targeted.AFTER.log`), each interpreter | see log | all | 0 | 0 |

The 9 suite skips are the pre-existing ones (same count as before); none belong to the repaired module.
