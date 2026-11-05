# Source-pinned candidate evidence: input-contract commit 092b420 — a PROPOSAL for human review

**Status: unreviewed.** Nothing here is a review, an adoption, production acceptance, a quality or creative verdict. Strict result changes stay `REVIEW_REQUIRED`/`FAIL` until a human moves an entry into `reviewed_expectations`.
Linux x86_64 only (Python 3.11.18 / 3.12.3 / 3.13.18). **No Windows claim.** No FFmpeg, no media, no mix/render/mux, no provider/API/auth/billing/security change. Library and closed harness are untouched; the pinned-original profile and results are byte-identical (checked at the end, §9).

## 1. Candidate and source pin
| | |
|---|---|
| candidate | `092b4200b11346753686579e37af2ff2bc9bbca8` (`codex/video-core-input-contract-20261105`, "harden input/diagnostic boundaries (F-CLI-1, F-AUD-1, F-REC-1, F-REC-2, F-ENC-1)"), base `f42c2db15e89d332a25cd2b305d9291539bb65e0` |
| read from | `git archive 092b420 tools/video-core` into scratch — never checked out, merged or edited |
| inventory | 103 files (41 under `video_core/`); excluded and listed: the top-level `evidence/` directory (and `__pycache__`/`.git`) |
| `package_digest_raw` (video_core/) | `2de4d9f2ec7a52cf95ff2a5d3d0b3e53debc00ac7a89f0d0c7e7696a099f1ba9` |
| `all_digest_raw` (all 103) | `d113fb5e836e94335b56066ce67eaec82330696579191f741f67f93b504b5081` |
| line-ending-normalised digests | equal to the raw ones (no CRLF in the tree); a CRLF-only checkout would be LABELLED `PINNED_MODULO_LINE_ENDINGS`, never silently equated |
| profile | `profiles/source_pin_092b420.json` (pin only; `reviewed_expectations: {}`) |

**Attribution (kept separate from the lifecycle branch).** Relative to the pinned original `b4ca4a3`, 17 package files differ; relative to `f42c2db` only **five**: `audiomix/plan.py`, `avmux/plan.py`, `avmux/receipt_check.py` (new), `paths.py`, `receipt.py`.
The same harness on `f42c2db` (committed earlier, `portable/results/candidate-f42c2db-strict-py3.13/`) showed **no** observable difference from the original (60 enforced pass, 16 gaps unchanged). So every change in this document is attributable to the five input-contract files. Lifecycle code (`runner`, `containment`, `render`, `mux`, `readback`) is **not exercised** by this read-only harness.

## 2. What was run (all Linux; each child ≤ 10 s, ≤ 256 KiB diagnostics, serial, deny hook active; `results/`)
| Run | Command shape | Result |
|---|---|---|
| **A. Existing harness, unchanged, strict** (3.11/3.12/3.13 identical) | `portable_harness.py --library-root <092b420 export> --profile source_pin_092b420.json --strict` | pin `PINNED_EXACT`; 12 cases PASS, 6 FAIL; checks: PASS 50 · OBSERVED_OPEN_GAP 9 · **REVIEW_REQUIRED 7 · FAIL 2 · ERROR 3 · GUARD_MISMATCH 2** · GUARD_OK 4 · CONTROL_OK 39 · SKIPPED 1; exit **1**; 7 mutations: 6 DETECTED, MU7 NOT_APPLICABLE |
| **B. Same harness + fixture control** (3.13) | `run_with_forgery_control.py …` (one fixture substituted from outside, see §4) | 16 PASS, 2 FAIL; PASS 59 · OBSERVED_OPEN_GAP 11 · **REVIEW_REQUIRED 5 · FAIL 1 · GUARD_MISMATCH 1** · no ERROR; exit **1** |
| C. B on the **pinned original** (equivalence control) | same wrapper, `b4ca4a3` export, default profile | exit 0; every outcome identical to the unmodified original run; only two world-snapshot observations differ (a receipt file's bytes), both PASS |
| D. Receipt-class probe (supplementary, §5) | `probe_receipt_classes.py --role original|candidate` | original: 22 OBSERVED_OPEN_GAP + 1 PASS; candidate: 21 PASS + 2 OBSERVED_OPEN_GAP; deny controls PASS; 3.11/3.12/3.13 identical on the candidate |
| E. Unreviewed proposal used as `--profile` | strict, as-is | **identical to A** (7 REVIEW_REQUIRED, 2 FAIL): the proposal is inert |
| F. Mechanical dry-run with the proposals promoted | `profile-dryrun-NOT-A-REVIEW/` | see §7; **not a review** |

The deny positive controls (`MS01`) passed in every run (`CONTROL_OK`); the probe re-runs them first and would stop with exit 5 on any failure. No denied attempt occurred in any candidate invocation.

## 3. Coverage of the five findings by the EXISTING harness
| Finding (INPUT_CONTRACT.md) | Covered? | Evidence / reason |
|---|---|---|
| F-REC-1 receipt parsing: no `AttributeError`, strict loader | **yes** | PC02/PC08 (GAP-14 open-gap checks) + probe M1–M8, D1, N1 |
| F-REC-2 receipt qualification (was three fields) | **yes** | PC08, PC02, PC07 (via the stub) + probe S1–S3, A1–A5, G1–G2, F1–F2 |
| F-CLI-1 lone-surrogate JSON key crashed the error report | **NOT COVERED** | no existing case sends a lone-surrogate key; not widened |
| F-AUD-1 zero-sample range on unselected/OMIT event (e.g. 60000/1) | **NOT COVERED** | the PC05 rates use selected non-empty ranges only; not widened |
| F-ENC-1 root name with a raw non-UTF-8 byte | **NOT COVERED** | the harness never creates such a directory (impossible on Windows); not widened |
Each uncovered finding is described in `INPUT_CONTRACT.md` with its own before/after evidence in the candidate branch; this harness neither confirms nor refutes it.

## 4. Why run A is partly blind, and what run B does (no oracle was altered)
The closed harness uses its shape-faithful **FABRICATED_STUB** receipt as the *valid control* of every avmux plan. `092b420` correctly rejects that stub (F-REC-2), so in run A:
* `PC01` aborts at its last step (1 check not evaluated: the `mux` dry-run), `PC05` aborts at its avmux step (3 checks not evaluated: avmux agreement, ±1 declared counts, **video caption/coverage boundaries**), `PC09` aborts at its avmux step (3 not evaluated, including **the Windows path traps**), `MS07` SKIPPED (its mutation pattern is absent). These are **NOT COVERED** in run A, not passes.
* Run B substitutes the stub by a **SELF_CONSISTENT_FORGERY** for the same stem (`portable_cases.receipt_fabricated` replaced from outside; no file under `portable/` edited; expected values untouched; marker `FIXTURE_OVERRIDE.json` in the output). All cases then run to the end: PC01, PC05, PC07, PC09 PASS completely. Run C shows that this substitution changes nothing on the original.
The forgery is a *control input*, not a genuine receipt: its acceptance proves nothing about provenance (§5).

## 5. Three receipt classes, kept apart (probe D; `results/receipt-probe/`)
| Class | What it is | Original `b4ca4a3` | Candidate `092b420` |
|---|---|---|---|
| **GENUINE** | the committed receipt written by the real mixer (`evidence/stage-b-audio-stem/receipt.stem-demo.json`, sha256-pinned `f87030ea…`) + a stem **proven byte-identical** (sha256 `68f8180e…`, 9644 bytes) to the genuine one, reproduced by independent integer arithmetic — `audiomix mix` was **never run** | accepted (G1) | **accepted** with `mechanical_checks: PASSED` and an `authentication` note (G1); the same receipt for a different stem → `audio_receipt_stem_mismatch` (G2, both) |
| **FABRICATED_STUB** | three-field receipt; the harness's shape-faithful stub; a self-contradicting edit | all 3 accepted | all 3 **rejected**: `audio_receipt_schema`, `audio_receipt_plan_hash`, `audio_receipt_inconsistent` (S1–S3) |
| **TAMPERED_GENUINE** | the genuine receipt with one field edited and the manifest re-pinned: `all_ok:false`, `partial:true`, float sample count, `inputs_unchanged:false`, `quality_evaluated:true`, duplicate key, `NaN` | all 7 accepted | all 7 **rejected** (`audio_receipt_inconsistent` ×4, `audio_receipt_sample_mismatch`, `audio_receipt_unreadable` ×2) |
| **SELF_CONSISTENT_FORGERY** | hand-made, built to satisfy every mechanical check for a given stem | accepted | **accepted** (F1) — and F2 shows the plan now **keeps** its two warnings and one unresolved event (`warnings_in_plan`, `unresolved_events: 1`) |
| MALFORMED | valid JSON of 8 wrong shapes | 8 uncaught crashes (exit 1, empty stdout) | 8 structured rejections (`audio_receipt_shape` ×4, `audio_receipt_schema` ×4) |
| NO_RECEIPT | `audio.receipt` omitted | accepted, `receipt: null` | accepted, `receipt: null` — **unchanged, still an open gap** |

**What this does and does not establish.** The candidate accepts the genuine receipt and rejects shallow stubs and tampered variants — but it also accepts a forgery built to be self-consistent. `INPUT_CONTRACT.md` says so itself: *"Anyone who can write a stem can also write a self-consistent receipt for it. No signature, credential or approval authority exists in this component."* Acceptance is mechanical consistency, **not** cryptographic trust, authorship or approval. Qualification limit of the GENUINE class: the stem bytes are the genuine bytes by hash identity, but were not emitted by the producer in this run.

## 6. Every changed check, explained (run A proposals; run B subset marked ‡)
Findings are cited from `INPUT_CONTRACT.md` (“F-REC-1/2”); expected = the pinned-original observation the harness asserts; “observed” = the candidate.
| Check | Expected (original) | Observed on 092b420 | Explanation |
|---|---|---|---|
| PC02 *avmux plan exits 0 and says UNVERIFIED* | exit 0 + readiness `UNVERIFIED…` | no plan (stub rejected) | **fixture artefact**: the stub no longer qualifies (F-REC-2). The UNVERIFIED rule is not shown to have changed; the control was invalid |
| PC02/PC08 ‡ *mis-shaped receipt: exit 1, empty stdout, uncaught AttributeError* | crash | exit 1, stdout non-empty, no AttributeError | F-REC-1: "typed, never AttributeError" |
| PC08 ‡ *eight mis-shaped receipts* | 8 × (1, empty, AttributeError, no code) | 8 × structured: `audio_receipt_shape` (array/string/null/number), `audio_receipt_schema` (the four objects lacking `schema`) | F-REC-1; `schema` is checked right after `status` |
| PC08 ‡ *three-field receipt accepted* | exit 0, `MIXED_VERIFIED` | rejected (observation recorded as `KeyError: 'plan'`; the real code is `audio_receipt_schema`, probe S2) | F-REC-2 "the old tests' three-field receipt no longer qualifies (that *is* F-REC-2)" |
| PC08 ‡ *receipt contradicting its own status accepted* | exit 0 | rejected (`audio_receipt_inconsistent`, probe S3) | F-REC-2: `written`/`stage`/flags are checked |
| PC08 ‡ *plan keeps only path/plan_sha256/sha256/status* | 4 keys | not observable here (the contradicting receipt is rejected) | F-REC-1/2 "Warnings are retained": observed instead by probe F2 |
| PC08 ‡ *structurally wrong receipts → specific codes* (FAIL) | `sample_count_wrong`→`audio_receipt_sample_mismatch`; `readback_absent`→`…sample_mismatch` | B: → `audio_receipt_inconsistent` / `audio_receipt_shape` (A: both `audio_receipt_plan_hash`, stub) | F-REC-2: more checks in a fixed order (flags before counters; `readback` must be an object); rejected either way, an earlier check now fires |
| PC07 *output namespace matrix* (FAIL, run A only) | avmux rows: fresh 0, overlaps/exists 1 | all avmux rows `audio_receipt_plan_hash` | **fixture artefact**: the stub is rejected before the output rules run; audio/video rows unchanged; with the control (B) the whole matrix PASSES |
| PC08 *any selection.ref accepted* (run A only) | accepted | no plan (stub) | **fixture artefact**; in B it is `OBSERVED_OPEN_GAP` unchanged — selection.ref is still arbitrary (GAP-07 open) |
These are copied as **proposals** to `profiles/proposal_asis_092b420.UNREVIEWED.json` (9 entries) and `profiles/proposal_forgery_control_092b420.UNREVIEWED.json` (6 entries): `proposed_expectations` is ignored by the harness; `reviewed_expectations` is empty.

## 7. What a reviewed profile can and cannot resolve (run F, `profile-dryrun-NOT-A-REVIEW/`)
* **Finding H-1 of the closed harness (preserved failed attempt `attempts/attempt2-…`)**: a reviewed expectation read from a JSON file is compared with `==` against the live observation; JSON turns tuples into lists, so observations containing tuples can never match. With the harness unchanged, promoting all 9 proposals resolved only **5** (`REVIEWED_CHANGE`), leaving 2 FAIL and 2 REVIEW_REQUIRED. The in-memory self-test MS07 could not see this. **Not fixed here** (the harness is closed); `run_profile_dryrun.py` applies a JSON-normalised comparison from outside, changing nothing else.
* With that mechanism-only patch: run A promoted → **9 × REVIEWED_CHANGE**, residual exit **1** because of 3 ERROR (aborted cases) + 2 GUARD_MISMATCH + 1 SKIPPED; run B promoted → **6 × REVIEWED_CHANGE**, residual exit **1** because of 1 GUARD_MISMATCH.
* **Not resolvable by any profile, by design:** `ERROR` (case aborted — checks after it are not covered), `GUARD_MISMATCH` (caller-guard demos are never rescued by reviewed expectations; PC02's expected readings `UNVERIFIED` / `REJECTED_UNSTRUCTURED` describe the original behaviour and need a human-reviewed harness revision), `SKIPPED`. The promoted dry-run profiles carry `reviewed_by_ref: "NOT A REVIEW …"`.

## 8. Still open on the candidate (OBSERVED_OPEN_GAP in run B)
GAP-01 (video-only accepts a proxy file and a colour stand-in), GAP-02 (video plan pins no source hash), GAP-04 *partly* (omitting the receipt is still accepted; self-consistent forgery accepted), GAP-07 (`selection.ref` unconstrained), GAP-08 ×3 (exit 0 with HOLD dry-run / `hold_if_planned` / UNVERIFIED), GAP-09 ×3 (`ALLOW_ZERO_FILL`). Changed: GAP-14 (crash → structured), GAP-04 shallow qualification, GAP-05 warnings retained (probe F2). GAP-03 (no video-receipt slot) and GAP-11 (no permission/approval carrier) are `unknown_field` facts that the PC08 checks still pass unchanged. GAP-06 (timing qualification) and GAP-12/13 are not exercised by the portable harness at all.

## 9. Integrity
`portable/` (harness, pinned-original profile, results) and everything outside `portable-candidate-092b420/` are byte-identical to the previous commit (`verify_scope_candidate.py`). The recovery transport (`portable-recovery/`) packs `8cab6e4` and is unaffected.

## 10. Windows commands (finite; NOT run on Windows by me)
```powershell
cd <checkout of the integration branch>
$H = "tools\video-core\evidence\integration-contract-20261105\portable"
$C = "tools\video-core\evidence\integration-contract-20261105\portable-candidate-092b420"
New-Item -ItemType Directory -Force C:\vcp\scratch, C:\vcp\cand092 | Out-Null
git archive -o C:\vcp\cand092.tar 092b4200b11346753686579e37af2ff2bc9bbca8 tools/video-core      # a file, not a PowerShell pipeline
tar -xf C:\vcp\cand092.tar -C C:\vcp\cand092
$L = "C:\vcp\cand092\tools\video-core"
# 1. source pin: must print PINNED_EXACT (exit 0); a CRLF-only checkout prints PINNED_MODULO_LINE_ENDINGS (exit 3)
py -3.13 -B $C\check_pin.py --library-root $L --profile $C\profiles\source_pin_092b420.json
# 2. existing harness, unchanged. Expect exit 1 and the Linux outcome table of section 2 (run A). If MS01 reports CONTROL_FAILED: STOP and report.
py -3.13 -B $H\portable_harness.py --library-root $L --scratch-root C:\vcp\scratch --out-dir C:\vcp\out-092-asis-001 --profile $C\profiles\source_pin_092b420.json --strict --budget-seconds 600
# 3. same with the fixture control (expect exit 1; run B)
py -3.13 -B $C\run_with_forgery_control.py --library-root $L --scratch-root C:\vcp\scratch --out-dir C:\vcp\out-092-ctl-001 --profile $C\profiles\source_pin_092b420.json --strict --budget-seconds 600
# 4. receipt classes (expect exit 0: 21 PASS + 2 OBSERVED_OPEN_GAP; exit 5 = deny control failed: STOP)
py -3.13 -B $C\probe_receipt_classes.py --library-root $L --scratch-root C:\vcp\scratch --out-dir C:\vcp\out-092-probe-001 --role candidate
```
Send back each `SUMMARY.txt` and `run.json` (probe: `receipt_classes.json`). A Windows difference in PC09 (Japanese+space paths and path traps — **only covered in run B**), PC06 (CRLF in SRT) or the probe would be a real finding. Use a new `--out-dir` for every run.

## 11. Preserved failed attempts (`attempts/`)
1. First run of the existing harness on the candidate used the *original's* profile: pin `UNPINNED`, same outcomes as run A (kept; it motivated the candidate pin profile).
2. Promoted dry-run with the unpatched harness: only 5/9 resolved — finding H-1 (§7).
