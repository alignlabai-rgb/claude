# `avmux reinspect --original-receipt` — before/after evidence (2026-11-05)

Follow-up commit on top of `03ad6928` (not an amendment). Contract and compatibility rule: `../../INPUT_CONTRACT.md`, "Follow-up 2". Linux x86_64, Python 3.11 / 3.12 / 3.13.
Inputs are tiny synthetic files. No real ffmpeg/ffprobe and no real media: the CLI runs use a marker `--ffprobe` that only records being started; Part B and the tests use the repo's owned
FAKE tools (Python/shell scripts). No network, purchase, provider, auth, privilege or runner/render/mix/mux change.

| status | item |
|---|---|
| **FIXED** | untyped `AttributeError`/`RecursionError` for wrong-typed or deeply nested original receipts; duplicate keys / NaN / Infinity / BOM / invalid UTF-8 / huge integer now structured rejections; stranded empty run directory after a late `AttributeError`; foreign schema; non-verified status with a final media output |
| **kept** | valid producer receipts (a receipt produced by the real `mux()` with fake tools, and the committed `evidence/avmux-synthetic/{mov,mp4}.receipt.json`), hand-written legacy receipts without `schema`, extra fields, the worst-case 1000-record producer summary |
| **OPEN / not claimed** | authenticity (hash = byte integrity only); the copied summary is bounded, not verified; symlinked parent directories of the receipt/media paths are not walked; real Windows not run |

## Files
| file | content |
|---|---|
| `repro_reinspect.py`, `summarize.py` | observation-only reproducer (Part A: real public CLI in an interpreter child with a marker ffprobe; Part B: owned fake ffprobe, library call) |
| `before.python3.13.json` | reproducer on `03ad6928` production code: tracebacks, accepted-and-continued bad receipts (ffprobe started), a stranded empty run directory |
| `after.python3.{11,12,13}.json` | same reproducer on the fixed tree: 0 tracebacks, 0 ffprobe starts for any rejection, controls still reach the tool probe, Part B rejects with 0 fake-ffprobe invocations and no run directory |
| `new-tests.BEFORE-fix.python3.13.log` | `tests/test_reinspect_original_receipt.py` against `03ad6928` (module stubbed because it does not exist there): 14 tests, 85 failures + 31 errors including sub-cases; only the pre-existing-behaviour controls pass |
| `full-suite.AFTER.python3.{11,12,13}.log` | unchanged-discovery full suite after the fix |
| `unchanged-check.txt` | specs, schemas, examples, prior evidence, and the session-owned runtime files are byte-identical; baseline plan hash |
| `clean-export-validation.txt` | suites on a clean `git archive` export of the exact staged tree |

## Headline before → after (public CLI, py3.13)
| receipt | before (`03ad6928`) | after |
|---|---|---|
| `[]`, `null`, `"x"`, `1`, `outputs:"x"\|[1]\|null`, `observations:"x"\|[1]` | traceback, exit 1, empty stdout | exit 1, `original_receipt_shape` |
| 100000 nested arrays | `RecursionError` traceback | `original_receipt_unreadable` |
| NaN, Infinity, duplicate keys, 5000-digit integer | accepted (then ffprobe started) or unreadable | `original_receipt_unreadable`, ffprobe never started |
| `status` list / number / missing; `route:5`; `plan_sha256:5`; `audio_packets:"x"`/`[1]`/850 levels | accepted, ffprobe started (and `audio_packets:"x"` later crashed after ffprobe ran and a run directory was created) | exit 1, `original_receipt_shape` |
| foreign `schema` | accepted | `original_receipt_schema` |
| final `media` output with status `IN_PROGRESS` / `INTERRUPTED` | accepted, ffprobe started | `original_receipt_inconsistent` |
| genuine-shaped receipt, hand-written legacy receipt, non-ASCII extra field | reaches the tool probe | unchanged: reaches the tool probe |
