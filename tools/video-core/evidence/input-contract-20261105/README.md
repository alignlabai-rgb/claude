# Input / diagnostic-boundary fixes — before/after evidence (2026-11-05)

Implementation candidate for independent Windows verification. **Not production acceptance.** Contract, root causes, affected API and limits: `../../INPUT_CONTRACT.md`.
Base commit `f42c2db` (the `before.*` files were produced from that production code; the evidence directory itself was untracked then, hence `working_tree_dirty: true`).
Findings: F-CLI-1, F-AUD-1, F-REC-1, F-REC-2, F-ENC-1 (audit `47d9c061`). Environment: Linux x86_64, Python 3.11.x / 3.12.x / 3.13.18. All inputs synthetic and owned;
no real media rendered, mixed or muxed; no network, purchase, paid API or font install; child processes are interpreter-only (≤ 10 s each) or the repo's existing fake tools.

## Files
| file | what it is |
|---|---|
| `repro_contract.py` | stdlib + `video_core` reproducer. Records observations only (never fails on a defect), so the same file runs on the unfixed and the fixed tree. `summarize.py` prints a digest. |
| `before.python3.13.json` | reproducer on the unfixed production code: tracebacks, `event_zero_samples`, `AttributeError`/`RecursionError` leaks, weak receipts accepted, raw-byte root traceback. |
| `after.python3.{11,12,13}.json` | the same reproducer on the fixed tree: no traceback, no leak, nothing created, no marker `ffmpeg`/`ffprobe` launched in any case. |
| `new-tests.BEFORE-fix.python3.13.log` | the new `tests/test_input_contract.py` run against the unfixed production code: 39 tests, 17 failures + 26 errors (including sub-cases), the 15 controls (Japanese, baseline hash, strictness) pass — the tests discriminate. |
| `full-suite.AFTER-fix.python3.{11,12,13}.log` | unchanged-discovery full suite on the fixed tree. |
| `baseline-full-suite.f42c2db.python3.{11,12,13}.log` | the same suite on `f42c2db` before any change. |
| `audit-reproducers-against-fix.python3.{11,12,13}.log` | the audit's own lane-1/lane-4 reproducer files (copied unmodified from `47d9c061`) run with `ADV_STRICT=1` against this tree; see the note below. |
| `clean-export-validation.txt` | full suite on a clean `git archive` export of the exact staged tree on py3.11/3.12/3.13, including one pre-existing, load-dependent containment-handshake flake seen on py3.13 and reproduced on the unmodified base. |
| `unchanged-check.txt` | `git diff` proof that specs, schemas, examples, prior evidence/receipts and the other session's modules are byte-identical; baseline plan hash. |

## Counts (separate total / pass / skip / fail)
| run | total | pass | skip | fail |
|---|---|---|---|---|
| `f42c2db`, py3.11 / 3.12 / 3.13 (each, run one at a time) | 548 | 539 | 9 | 0 |
| fixed tree, py3.11 / 3.12 / 3.13 (each) | 587 | 578 | 9 | 0 |
| of which new: `tests/test_input_contract.py` (all three interpreters) | 39 | 39 | 0 | 0 |

The 9 skips are the pre-existing opt-in/platform skips (same count before and after; none of the new tests skipped on Linux). Running the three interpreters **at the same time** made
`tests.test_containment.NativePosix` fail (4 / 9 / 10 failures; it kills process trees and checks sentinels, so concurrent suites interfere) — an artefact of concurrency in this
environment, not of the change; run one suite at a time. The only existing test file touched is `tests/test_avmux_plan.py::AudioReceiptQualification`: its fixture was exactly the
three-field schema-less receipt that F-REC-2 says must not qualify, so it now mutates a receipt written by the real audio mixer (`tests/receipt_fixtures.py`); same 34 tests in that module.

## Headline before → after (py3.13; identical on 3.11/3.12)
* **F-CLI-1.** `validate` of the video / audiomix / avmux CLIs with a `"\ud800"` key: *before* traceback, empty stdout, exit 1 → *after* exit 1, `{"ok": false, "error": {"kind": "validation_failed", "issues": [{"code": "unknown_field", ...}]}}`, the issue path decodes to the exact key. A valid unknown Japanese key is still written literally; Japanese caption / decision_ref / selection.ref validate (exit 0); SRT bytes equal the library export; plan hash `4bc20b3f…9f21` exact.
* **F-AUD-1.** fps 60000/1: selected silence `[0,10)` + unselected `UNKNOWN [2,3)` or selected `OMIT [2,3)`: *before* `REJECTED event_zero_samples` → *after* `READY`. Unselected USE/silence/OMIT/MISSING/PROXY zero-sample events also pass; selected USE, selected INTENTIONAL_SILENCE (and, unchanged, selected unresolved) still `event_zero_samples`.
* **F-REC-1.** `[]`, `null`, `"x"`, `1`, `outputs:"x"`, `outputs:[1]`, `readback.wav:"x"`, 100000 nested arrays: *before* `AttributeError`/`RecursionError` → *after* typed `audio_receipt_shape` / `audio_receipt_unreadable`; CLI exit 1 with a structured error.
* **F-REC-2.** schema-less three-field receipt, `all_ok:false`, `partial:true`, `quality_evaluated:true`, duplicate `status` keys, float `sample_count`: *before* all `ACCEPTED` → *after* each rejected with its own `audio_receipt_*` code. The genuine receipt from the real audio mixer and the committed `stage-b-audio-stem` receipt still qualify. Pinned receipts remain **unauthenticated**.
* **F-ENC-1.** `--output-root b'out_caf\xe9'`: *before* `UnicodeEncodeError` traceback (and, in render/avmux, a stranded run) → *after* `root_invalid` (`unsafe_path`), exit 1, nothing created, no tool launched. Valid Japanese root: plan and audio mix unchanged. Linux-only evidence (Windows cannot create a raw 0xE9 name).

## Audit's own reproducers against the fix
Of the six audit tests run (`AudioReceiptInput`, `ParserDefects`, `NonUtf8Roots`), five now pass in strict mode (F-REC-2, F-CLI-1, F-AUD-1, F-ENC-1 and the genuine-receipt control). `test_malformed_receipt_content_is_a_typed_rejection` (F-REC-1) still reports a **harness error**, not a defect: its own
`harness_check` insists the *minimal three-field schema-less receipt must plan*, which contradicts the audit's F-REC-2 finding. Run without that control, every F-REC-1 input in that test is a typed rejection (`audio_receipt_shape` / `audio_receipt_schema` / `audio_receipt_unreadable`). The control needs a genuine receipt in the audit harness; the audit files were not edited.

## Reproduce
```bash
cd tools/video-core
python3 evidence/input-contract-20261105/repro_contract.py /tmp/after.json && python3 evidence/input-contract-20261105/summarize.py /tmp/after.json
python3 -m unittest tests.test_input_contract -v
python3 -m unittest discover -s tests -t .      # one interpreter at a time
```
