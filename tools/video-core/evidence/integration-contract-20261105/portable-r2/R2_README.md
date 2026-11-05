# portable-r2 — additive version 2 of the portable contract harness (H-1 fixed, deny-first admission)

Start with `R2_HANDOFF.md` (commands, exit codes, limits) and `R2_CHANGES.md` (every change, with `R2_DIFF_vs_portable.patch`). Everything here is new; `portable/`, its pinned-original profile and results, `portable-recovery/`, `portable-candidate-092b420/` and
`portable-candidate-recovery/` are byte-identical (`verify_scope_r2.py`). No library or production file changed. Nothing is adopted or reviewed. Linux only; no Windows claim.

| What | Where |
|---|---|
| comparison rule (tuple≡list; bool/int/float/None/str kept apart; non-finite and unsupported rejected) | `json_equiv.py`, `R2_CHANGES.md` §1 |
| harness copy with the changes (`child_runner.py` byte-identical: same deny hook) | `portable_*.py`, `child_runner.py`, `R2_DIFF_vs_portable.patch` |
| tests (32) | `tests/` — rule 14, profiles on disk 11, admission 7 |
| profiles (UNREVIEWED; byte copies of the earlier pin profiles + fresh r2 proposals) | `profiles/` |
| evidence | `results/`, `attempts/`, `REDACTIONS.json` |

## Results (Linux, Python 3.11.18 / 3.12.3 / 3.13.18)
| Evidence | Result |
|---|---|
| unit + admission tests (`results/unit-tests/`) | **32 tests, 0 skips, 0 failures on all three** (also with `-W error::ResourceWarning`) |
| r2 on the **pinned original** `b4ca4a3` (`r2-on-pinned-original/`) | admission MS01 PASS (18 children) → 19 cases PASS, 433 serial children, ≈48 s, `PINNED_EXACT`, 60 PASS · 16 OBSERVED_OPEN_GAP · 6 GUARD_OK · 48 CONTROL_OK, 7/7 mutations detected, exit 0; **identical case outcomes on 3.11/3.12/3.13**. `--skip-mutation`: 294 children, 32.5 s, exit 0 |
| r2 vs the closed harness on the same library (`results/EQUIVALENCE_*.json`) | 115 shared checks on the original, **0 differ** (r2 adds 13 mechanism checks, replaces 7 old MS05/MS07 ones); candidate as-is 111 shared / control 115 shared, **0 differ** — no oracle moved, no candidate result suppressed |
| `--only MS01_…` (`ms01-first-only/`) | accepted now; 18 children, 0.6 s, exit 0 (the closed harness refused it before any child) |
| disposable intentionally failing deny control (`admission-stop-demo-failing-control/`) | exit **5**, `STOP.txt`, **1 child** total, no contract case / mechanism test / mutation control ran; the control only reads and performs no prohibited action |
| r2 on **092b420**, as-is, strict, source-pinned (`r2-on-candidate-092b420/`) | `PINNED_EXACT`; 6 cases FAIL: 7 REVIEW_REQUIRED · 2 FAIL · 3 ERROR · 2 GUARD_MISMATCH; exit 1 — same counts as the closed harness, same on 3.11/3.13. Fixture artefacts are **visible**, not suppressed |
| same with the fixture control | 5 REVIEW_REQUIRED · 1 FAIL · 1 GUARD_MISMATCH; no ERROR; exit 1 |
| the UNREVIEWED proposal given as `--profile` (`unreviewed-proposal-is-inert/`) | outcomes identical to the unprofiled run: `proposed_expectations` is never applied |
| promoted NOT-A-REVIEW dry-runs (`profile-dryrun-NOT-A-REVIEW/`) | **with the unpatched r2 harness**: 9/9 (as-is) and 6/6 (control) proposals → `REVIEWED_CHANGE` (H-1 fixed; the closed harness resolved 5/9); residual ERROR ×3 + GUARD_MISMATCH ×2 / GUARD_MISMATCH ×1 stay; the run is flagged and exits 1 — a dry run is never green |

## Findings and limits in short
* H-1 is fixed inside r2 only. The closed `portable/` harness still has it (a reviewed profile cannot match tuple observations there).
* r2's stricter rule changed **no** shared outcome on either library; the harness's expectations were already type-consistent.
* Not covered, unchanged: F-CLI-1, F-AUD-1, F-ENC-1; guard demos are not profile-reviewable; ERROR leaves later checks of an aborted case uncovered.
* Failed attempts are preserved (`attempts/`), including the first full run whose MS07 used a mutation that also raises a case ERROR.
