# boundary-probe-r1 - read-only boundary probe for F-CLI-1, F-AUD-1 and F-ENC-1

Additive, supplemental. Nothing in `portable/`, `portable-r2/`, the two candidate directories, the two recovery transports, the six original specs or any library file was changed (`verify_scope_probe.py`).
It closes the three gaps that `portable-r2/R2_HANDOFF.md` and `portable-candidate-092b420/CANDIDATE_REVIEW.md` listed as "not covered", and attributes what it observes **per source and per check**.

## What it is

* One script, `boundary_probe.py`, that imports the CLOSED r2 mechanism unchanged (`--harness-dir`, default `../portable-r2`): isolated child interpreter `-I -S -B -X utf8` per call, DENYING audit hook
  (process / network / write / env), bounded capture (128 KiB per stream, 10 s per child, flood kill at 1 MiB), serial children, finite budget (default 420 s total, max 900), exclusive-create output directory.
* **Deny-first.** For every source the r2 admission `MS01` (16 forbidden actions + counterfactual + read control) runs before any probe case. If it fails the whole run STOPS with exit 5 and no probe case starts
  (`demo_failing_admission.py` and `tests/` prove this with a disposable control that does no prohibited action; evidence in `results/failing-admission-demo/`).
* Every probe is the real public CLI `main(argv)` (via `runpy`, i.e. `python -m video_core | video_core.audiomix | video_core.avmux`) on `validate`, `plan`, `srt`, or a **dry-run** `render|mix|mux`
  *without* `--render|--mix|--mux`. No ffmpeg/ffprobe, no media, no network, no write: the hook would deny it, and each rejection/plan-only call also asserts "deny hook silent" and "owned world byte-identical".
* The SAME 14 cases run against three source trees, each with its own pin profile (`profiles/pin_*.json`: source pin only, `review_status: NONE`, no reviewed expectations):
  original `b4ca4a3b031351a631c0fbe1eb149935f4db19ba`, candidate `092b4200b11346753686579e37af2ff2bc9bbca8`, input `91ec2d40f360e8019f357132a43c370ab66bb673` (exports of `git archive <sha> tools/video-core`).
  Source inventories are taken before and after every source (`all_digest_raw_before/after` in `run.json`).

## Where the expectations come from

From the written contract, not from observed behaviour: `INPUT_CONTRACT.md` of 092b420/91ec2d4 (F-CLI-1, F-AUD-1 table, F-ENC-1, "Remaining limits"), `AUDIO_SPEC.md` (HOLD = exit 5; unselected events never block; OMIT consumes nothing),
and independent oracles: the exact-rational frame->sample policy `floor(f*d*48000/n + 1/2)` (checked against the integer formula in `tests/`), an independently assembled Japanese SRT, and the schema rules read from the validators.
**Caveat:** the F-xxx expectations are the input session's own contract, so "the candidate meets them" is conformance to that contract, not an independent judgement of it.
`MET`/`NOT_MET` of an expectation are observations of a source, never an adoption, readiness or quality verdict. A producer's READY/HOLD/rejection is recorded as producer behaviour and is not promoted to a caller's readiness.

## Cases (14)

| case | finding | what |
|---|---|---|
| K1 `lone_surrogate_json_key` | F-CLI-1 | `"\ud800"` key (an ASCII escape in the document) in episode / audio manifest / avmux manifest x `validate` and `plan` |
| K2 `literal_escape_text_key` | control | the literal 6-character text `\ud800` (valid UTF-8): stays literal, never becomes a surrogate |
| K5 `lone_vs_literal_contrast` | F-CLI-1 | both inputs together: decoded diagnostic paths exist and differ (`/`+1 surrogate vs `/`+6 characters) |
| K3 `value_level_surrogates` | F-CLI-1 | surrogate in a caption text / `decision_ref` VALUE: typed `text_invalid_unicode` / `invalid_ref`, not echoed |
| K4 `surrogate_in_argv_diagnostics` | F-CLI-1 | surrogate in a missing manifest path and in `--output-name`: no traceback, typed or empty-by-design output |
| J1 `japanese_and_spaces` | control | Japanese + spaces in world, roots, file names, text: exact SRT bytes vs oracle, literal UTF-8 preserved, dry-runs create nothing; Japanese output NAME is a documented `output_name_invalid` rejection (ASCII-only rule) |
| Z1 `zero_sample_unselected_states` | F-AUD-1 | 60000/1, frame range [2,3) = 0 samples, NOT in scope, states UNKNOWN / MISSING / PROXY / OMIT / INTENTIONAL_SILENCE / USE |
| Z2 `zero_sample_selected_states` | F-AUD-1 | same range IN scope: USE / INTENTIONAL_SILENCE / MISSING / UNKNOWN / PROXY stay rejected `event_zero_samples` (MISSING/UNKNOWN/PROXY = the documented remaining limit) |
| Z3 `selected_omit_and_effective_scope` | F-AUD-1 | selected OMIT consumes nothing and adds no coverage; OMIT + a real hole = HOLD; only-OMIT scope and empty scope are never READY; qualified USE + unselected zero-sample UNKNOWN + selected zero-sample OMIT together |
| R1 `boundary_rates` | F-AUD-1 | 48000/1, 48001/1, 50000/1, 60000/1, 60000/1001: the oracle decides which frames are zero-sample (first zero frames 12 / 2; none for the others inside the 18000-frame cap); one-sample selected USE accepted |
| S1 `selection_normal_span` | control | 30000/1001: qualified USE + unselected UNKNOWN does not block; selected UNKNOWN = HOLD (exit 5) listing the event |
| E1 `non_utf8_root_bytes_posix` | F-ENC-1 | real directories `assets_caf\xe9` / `out_caf\xe9` (raw 0xE9 byte) as asset root and output root in all three CLIs (6 commands) |
| E2 `string_level_root_surrogate` | F-ENC-1 | the representable-everywhere boundary: a root ARGUMENT `...caf\udce9` (nonexistent) |
| E3 `symlink_to_raw_root` | F-ENC-1 | POSIX, unprivileged: ASCII-named symlink resolving into the raw-byte directory (post-realpath check) |

## Result (this Linux sandbox; identical on Python 3.11.18, 3.12.3, 3.13.18)

| source | pin | admission | cases | expectation checks |
|---|---|---|---|---|
| original b4ca4a3 | PINNED_EXACT | PASS (18 children) | 6 PASS, 8 CONTRACT_NOT_MET | 316 MET, **99 NOT_MET** |
| candidate 092b420 | PINNED_EXACT | PASS | 14 PASS | 415 MET, 0 NOT_MET |
| input 91ec2d4 | PINNED_EXACT | PASS | 14 PASS | 415 MET, 0 NOT_MET |

Each run: 85 children per source (18 admission), ~23-25 s wall for all three sources, probe exit 0 (NOT_MET is reported, not a failure). `CROSS_VERSION.json`: 1659 items compared across the three interpreters, **0 disagreements**
(outcome, observed value, exit code and normalised stdout hash of every invocation).

**Attribution** (`ATTRIBUTION.json`; role order original -> candidate -> input):

* 99 checks `CHANGED_NOT_MET_TO_MET`, **all at 092b420**: F-CLI-1 37, F-ENC-1 39, F-AUD-1 23. None at 91ec2d4. 316 `UNCHANGED_MET`. 0 `UNCHANGED_NOT_MET`, 0 `REGRESSED`. 092b420 and 91ec2d4 give the same exit code and the same normalised stdout for every one of the probe's invocations.
* Observed original behaviour: K1 - exit 1, **empty stdout, traceback** `UnicodeEncodeError` (the structured rejection is never written); E1 - dry-run `render|mix|mux` with a raw-byte output root: exit 1, empty stdout, traceback `UnicodeEncodeError '\udce9'` in all three CLIs, and with a raw-byte asset root the plan is **accepted** (exit 0) for all three CLIs; E2 - `root_missing` instead of `root_invalid`; Z1/Z3 - `event_zero_samples` rejects the whole manifest for unselected and OMIT events.
* Candidate/input: K1 typed `unknown_field` with the exact key as JSON escape and no replacement character; E1/E2/E3 typed `unsafe_path`/`root_invalid` before anything is created, text not echoed; Z1 READY with the zero-sample event listed `selected:false, samples:0`; Z3 selected OMIT `disposition OMITTED`, READY when covered, HOLD (exit 5, one gap) when not; only-OMIT scope HOLD (never READY); empty scope `scope_empty` in all three sources.
* Not a blanket pass: Z2's selected MISSING/UNKNOWN/PROXY zero-sample rows are *expected rejected* because that is the documented remaining limit; the observation rows say so ("REMAINING LIMIT ... rejected instead of yielding HOLD").

**Mechanism attribution by disposable mutants** (`run_mutation_attribution.py`, `results/mutation/`): in a COPY of 092b420, reverting exactly one line - `receipt.dumps` back to strict UTF-8 (M-CLI), `paths._utf8_encodable` always true (M-ENC),
`plan.py` zero-sample rule back to "every event" (M-AUD) - flips **only** that finding's checks: 37 / 39 / 23, the same counts as the original->candidate change, and 0 checks of the other findings. Each mutant is labelled `UNPINNED` (probe exit 4) and is a measurement of detection power, nothing else.

Unit tests (`tests/`, 11 tests: oracle, classification, platform branches, failing-admission STOP, refusals, one end-to-end case) pass on 3.11, 3.12, 3.13 (`results/unit-tests.txt`).

## Reading the outcomes

`MET` / `NOT_MET` an input-contract expectation | `OBSERVED` a recorded observation without an expectation | `CONTROL_OK` / `CONTROL_FAILED` a harness control | `NOT_APPLICABLE` cannot exist on this platform, no OS evidence invented |
`SKIPPED` could not run here, with the reason | `NOTE`. Case status: `PASS` (all expectations met) | `CONTRACT_NOT_MET` (at least one NOT_MET: an observed limitation) | `FAIL` (harness-level: error, failed control, budget, policy).
Exit codes: 0 completed | 1 hard failure (FAIL/ERROR/CONTROL_FAILED/budget/source changed) | 2 usage or refusal | 3 Python < 3.11 | 4 a source does not match its pin | 5 STOPPED, deny admission failed.
Attribution classes: `UNCHANGED_MET`, `CHANGED_NOT_MET_TO_MET` (+ `changed_at`), `UNCHANGED_NOT_MET`, `REGRESSED_MET_TO_NOT_MET`, `MIXED`, `OBSERVATION_SAME/DIFFERS`, `NOT_COMPARABLE`.

## Run it

Linux / macOS (from a checkout of this branch; scratch and output directories must be outside the repository):

```bash
python3 tools/video-core/evidence/integration-contract-20261105/boundary-probe-r1/export_sources.py --repo . --dest /tmp/bp-src
python3 tools/video-core/evidence/integration-contract-20261105/boundary-probe-r1/boundary_probe.py \
  --source b4ca4a3=/tmp/bp-src/b4ca4a3/tools/video-core --source 092b420=/tmp/bp-src/092b420/tools/video-core --source 91ec2d4=/tmp/bp-src/91ec2d4/tools/video-core \
  --scratch-root /tmp/bp-scratch --out-dir /tmp/bp-out --normalize-stored-paths
```

Native Windows: see `WINDOWS_RUN.md` (same commands, one receipt). Other tools: `make_receipt.py`, `merge_versions.py`, `run_mutation_attribution.py`, `package_results.py`, `make_pin_profile.py`, `verify_scope_probe.py`.

## Uncovered limits (stated, not hidden)

1. **No Windows or macOS execution.** Raw non-UTF-8 directory names cannot be created on Windows (UTF-16 names): E1/E3 report `NOT_APPLICABLE` there. E2 and K1-K5/Z/R/J run on Windows but nothing about Windows is claimed until a Windows receipt exists; NTFS lone-surrogate names are not tested. A filesystem that refuses the raw name (e.g. APFS) yields `SKIPPED`, tested only through a simulated refusal in `tests/`.
2. **The stranded-run symptom of F-ENC-1 is not reproduced.** It needs `--render|--mix|--mux` (media execution), which this probe never passes; only the early root refusal and the original's dry-run behaviour are observed.
3. Remaining limit kept: a selected MISSING/UNKNOWN/PROXY zero-sample row is rejected, not HOLD (no convention exists to test).
4. K covers unknown-key paths, two value fields and two argv places; other places that could echo text, a high surrogate directly followed by a low surrogate as separate code points, and non-Python JSON readers are not covered.
5. Zero-sample frames: the first oracle-found zero frame per rate (and its neighbours), five rates, not every frame up to 18000; the schema ceilings (n <= 60000, d <= 1001) are not probed.
6. The avmux receipt is a **SELF_CONSISTENT_FORGERY** mechanical fixture (`portable-r2/candidate_fixtures.py`, marked in its `_fixture_note`) used only so a plan reaches the root checks; it says nothing about provenance. 91ec2d4's receipt/`reinspect` changes are outside this probe.
7. `os.startfile` and registry deny categories exist in the inherited hook but are not exercised (no dangerous action was triggered for coverage). A deny control other than MS01 is not re-run here.
8. Stored tracebacks of the original show the sandbox checkout path (`/home/user/claude/...`); stored scratch/world paths are placeholders (`--normalize-stored-paths`, raw SHA-256 in each `status.json`).
9. Results for 3.11/3.12 keep `invocations_index.json` (per-invocation raw and normalised SHA-256) instead of the per-invocation byte files, which are stored once in `results/py3.13/`; the three interpreters' hashes are equal (`CROSS_VERSION.json`).
10. Profiles: only source pins (`review_status: NONE`). No reviewed expectation, no proposal, no adoption, no quality or readiness statement. No recovery transport was made (none is needed: every file is a plain committed file).

## Attempts kept

`attempts/ATTEMPT-01-fixture-errors/`: the first draft (21 NOT_MET on the candidate, all fixture/reading errors of mine, each traced to a library message or AUDIO_SPEC) and the reason for each correction.
