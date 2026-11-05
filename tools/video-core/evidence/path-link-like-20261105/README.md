# F-PATH-1 and receipt-qualifier review — follow-up evidence (2026-11-05)

Follow-up commit on top of `092b420` (not an amendment). Contract text: `../../INPUT_CONTRACT.md`, section "Follow-up". Linux x86_64, Python 3.11 / 3.12 / 3.13. All inputs synthetic,
in temporary directories; no real media, no process launched (the tests run under a no-subprocess guard), no network, no purchase, no privileged operation, no link or junction created.

## Status table
| item | status | evidence level |
|---|---|---|
| F-PATH-1 `check_media_for_inspect` accepted a stat result with the reparse attribute | **FIXED at code level** (`is_link_like`, same code `symlink_rejected` as `resolve_asset`) | stat-like **stub** only; **real Windows junction / reparse behaviour UNVERIFIED** |
| inspect does not walk symlinked parent directories | **OPEN, unchanged** (only the named file is examined; `resolve_asset` walks every component) | by reading the code |
| qualifier: `outputs[plan].partial/bytes`, `readback.wav.data_bytes/data_offset`, `mix.first_clipped_sample` unchecked | **FIXED** (each demonstrated by the sweep before the fix) | in-process, producer-generated receipt |
| receipt hash = authenticity | **NOT CLAIMED**: byte integrity / consistency only; forged self-consistent receipt passes by design | tests state it |
| `avmux reinspect --original-receipt` with `[]` → `AttributeError` | **OPEN** (nothing is created; outside this task's modules) | reproduced once in-process |

## Files
| file | content |
|---|---|
| `new-tests.BEFORE-fix.python3.13.log` | `tests/test_inspect_link_like.py` against the unfixed `paths.py`: 4 failures + 1 error (the reparse stub is accepted; `video_core inspect` and `avmux reinspect` proceed to a process launch, which the guard turns into an error); the 4 controls pass. |
| `receipt-review.BEFORE-tightening.python3.13.log` | `AudioReceiptContract` with the previous `receipt_check.py`: the new sweep fails with 53 accepted contract-bearing mutations and the extended inconsistency table fails. |
| `full-suite.AFTER-followup.python3.{11,12,13}.log` | unchanged-discovery full suite after the follow-up. |
| `targeted.AFTER-followup.log` | `test_input_contract`, `test_audio_plan`, `test_avmux_plan`, `test_static_guards`, `test_inspect_link_like`, `test_paths`, `test_cli`, `test_avmux_cli`, `test_audio_cli` on each interpreter. |
| `unchanged-check.txt` | specs, schemas, examples, earlier evidence, the previous commit's evidence and the other session's modules are byte-identical; baseline plan hash. |
| `clean-export-validation.txt` | suites on a clean `git archive` export of the exact staged tree. |

## Counts (separate total / pass / skip / fail)
| run | total | pass | skip | fail |
|---|---|---|---|---|
| `f42c2db` baseline | 548 | 539 | 9 | 0 |
| `092b420` (previous commit), py3.11/3.12/3.13 | 587 | 578 | 9 | 0 |
| after this follow-up, py3.11/3.12/3.13 (working tree, each) | 598 | 589 | 9 | 0 |
| targeted set above, py3.11/3.12/3.13 (each) | 182 | 182 | 0 | 0 |

New in this follow-up: 9 tests in `tests/test_inspect_link_like.py`, 2 in `tests/test_input_contract.py` (single-field mutation sweep, hash-is-not-authenticity); the inconsistency table gained 8 cases.
Suites are run one interpreter at a time (concurrent suites make `test_containment.NativePosix` fail; see the previous evidence README).

## Not done / not claimed
No Windows run; no real junction, mount point, OneDrive placeholder or privileged symlink; no developer-mode change. A coordinator-side Windows run of the new tests would show only that the stub tests pass there, plus the skips noted in the test files.
