# portable-r2 — handoff for the coordinator (Windows and Linux)

Additive version 2 candidate. Test-only. **Not executed on Windows by me**; all Linux numbers below are from Python 3.11.18 / 3.12.3 / 3.13.18. Read `R2_CHANGES.md` for what changed and why, `R2_README.md` for evidence.
Nothing outside `portable-r2/` changed. No library or production file changed. New profiles are **UNREVIEWED proposals**; nothing is adopted.

## Commands (PowerShell; finite; new `--out-dir` every time; short roots)
```powershell
cd <repo checkout>
$R = "tools\video-core\evidence\integration-contract-20261105\portable-r2"
New-Item -ItemType Directory -Force C:\vcp\scratch, C:\vcp\tmp | Out-Null
$env:VCC_TMP = "C:\vcp\tmp"        # this PowerShell session only (the unit tests' temp root); nothing global is changed

# 0. pure tests + admission tests (about 20 s on Linux; the rule, disk profiles, loader, admission/stop)
py -3.13 -B -m unittest discover -s $R\tests

# 1. DENY CONTROLS FIRST, alone (18 children, seconds). A selected mechanism test is now accepted; admission always runs first.
py -3.13 -B $R\portable_harness.py --library-root tools\video-core --scratch-root C:\vcp\scratch --out-dir C:\vcp\r2-ms01-001 --strict --only MS01_deny_controls_prove_the_hook_is_active
#    exit 0 expected. Exit 5 or CONTROL_FAILED = STOP and report: the deny hook is not demonstrated on that machine and nothing else will have run.

# 2. full strict run on the pinned original (expect exit 0). Admission runs first; then 11 contract cases, 7 mechanism tests, 7 mutation controls.
py -3.13 -B $R\portable_harness.py --library-root tools\video-core --scratch-root C:\vcp\scratch --out-dir C:\vcp\r2-full-001 --strict --budget-seconds 600
#    without the mutation controls: add --skip-mutation  (Linux: 294 children instead of 433)

# 3. input-contract candidate 092b420 (read-only export, a FILE not a pipeline)
New-Item -ItemType Directory -Force C:\vcp\cand092 | Out-Null
git archive -o C:\vcp\cand092.tar 092b4200b11346753686579e37af2ff2bc9bbca8 tools/video-core
tar -xf C:\vcp\cand092.tar -C C:\vcp\cand092
$L = "C:\vcp\cand092\tools\video-core"
py -3.13 -B $R\check_pin.py --library-root $L --profile $R\profiles\source_pin_092b420.json                       # must print PINNED_EXACT (exit 0)
py -3.13 -B $R\portable_harness.py --library-root $L --scratch-root C:\vcp\scratch --out-dir C:\vcp\r2-cand-asis-001 --profile $R\profiles\source_pin_092b420.json --strict --budget-seconds 600
py -3.13 -B $R\run_with_forgery_control.py --library-root $L --scratch-root C:\vcp\scratch --out-dir C:\vcp\r2-cand-ctl-001 --profile $R\profiles\source_pin_092b420.json --strict --budget-seconds 600
```
Expected on Linux (Windows may differ — that would be a finding): step 2 `19 cases PASS`, 433 serial children, `PINNED_EXACT`, 60 library PASS · 16 OBSERVED_OPEN_GAP · 6 GUARD_OK · 48 CONTROL_OK, 7/7 mutations detected, exit 0.
Step 3 as-is: exit **1** — 6 cases FAIL: 7 REVIEW_REQUIRED, 2 FAIL, 3 ERROR, 2 GUARD_MISMATCH (the stub receipt is rejected by the stricter candidate; **not hidden**); fixture-control: exit **1** — 5 REVIEW_REQUIRED, 1 FAIL, 1 GUARD_MISMATCH.
Send back each `SUMMARY.txt` and `run.json`.

## Exit codes
0 all enforced checks pass, every open gap exactly as pinned (and, with `--strict`, the source is pinned/reviewed) · 1 failure / ERROR / GUARD_MISMATCH / CONTROL_FAILED / budget / mutation undetected / library changed · 2 usage or refusal (output exists, bad or unsafe **profile**, unknown id) · 3 Python < 3.11 · 4 REVIEW_REQUIRED (changed open gap, `--strict` + unpinned source, or the profile applied a NOT-A-REVIEW entry) · **5 STOPPED: deny-control admission failed, nothing else ran**.

## Reviewing a candidate with r2 (never automatic)
1. Run the candidate `--strict` with its pin profile; read `REVIEW_REQUIRED`/`FAIL`. 2. A human decides per item (see `../portable-candidate-092b420/CANDIDATE_REVIEW.md` §6 and `profiles/proposal_*.r2.UNREVIEWED.json`, whose `proposed_expectations` the harness ignores).
3. Copy chosen entries into a NEW file's `reviewed_expectations` with a real `reviewed_by_ref`; the loader refuses entries without one and entries with unknown fields. 4. Re-run with `--profile`. With a matching source pin the entries become `REVIEWED_CHANGE` (tuples/lists compared by the JSON-compatible rule); ERROR and GUARD_MISMATCH are never rescued; a wrong pin rescues nothing.
`results/profile-dryrun-NOT-A-REVIEW/` shows the mechanics on the candidate with **automatic NOT-A-REVIEW promotion**: all 9 / 6 proposals resolve, the residual ERROR/GUARD items remain, the run is flagged and exits non-zero. It is not a review.

## Remaining limits
* Linux only; Windows untested. The `os.startfile` and registry deny categories are not exercised. The deny layer is a Python-level audit hook in a child interpreter, not an OS sandbox; reads are allowed.
* A real hook failure cannot be staged without allowing the action; the stop path is verified with a disposable control that fails without any prohibited action (its child only reads).
* Guard demos cannot be changed by a profile (by design): PC02's expected readings still describe the original behaviour, so a candidate that changes them stays at exit 1 until a human revises the harness guards.
* Aborted cases (ERROR) leave later checks of the same case NOT COVERED; r2 does not hide this (fixture-control run shows what they would have said).
* F-CLI-1, F-AUD-1, F-ENC-1 of the candidate are still **not covered** by any r2 case.
* The JSON rule treats object key order as insignificant and canonicalises `int` keys like JSON; `bool` keys, mixed-collision keys, bytes, sets, non-finite floats and lone surrogates are errors, not values.
