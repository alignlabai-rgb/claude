# Integration contract for the existing public video / audio / mux library — 2026-11-05

Branch `codex/video-core-integration-20261105`, base `b4ca4a3b031351a631c0fbe1eb149935f4db19ba`. Everything in this directory is new; every other path in the repository is byte-identical to the base (`SOURCE_UNCHANGED.json`).

**What this is:** a tested integration contract — what the existing owners must supply before `video_core` / `audiomix` / `avmux` can be called safely, what each stage actually verifies, and how missing qualification, failure and rollback propagate.
**What this is not:** another app, binder, exporter, board, ledger, provider runner or scheduler; an adapter; adoption; operational readiness; a creative-quality judgement. Real story, voice and timing are unresolved and are not inferable from the synthetic fixtures. No purchase, paid API, credential, production source/asset, provider generation, PR, merge or deployment was involved.
**Nothing media was executed:** no ffmpeg/ffprobe, render, mix, mux or download. The harness refuses `--render/--mix/--mux` (and argparse abbreviations) and `inspect`.

## Read first
| File | What |
|---|---|
| `CONTRACT_EN.md` / `CONTRACT_JA.md` | the owner-facing contract: six separated concerns, per-stage verification table, caller responsibility table, propagation rules, design-only pseudocode |
| `DIAGRAMS.md` | lifecycle state diagram, cross-stage sequence diagram, ASCII propagation sketch |
| `GAPS_AND_TASKS.md` | 14 concrete gaps (with observed behaviour and test ids) and suggested tasks per existing owner |
| `EXPECTED_VS_OBSERVED.json` | every check (expected, observed, ok, layer), every CLI invocation, source API references with file sha256, run summaries, cross-version digest |
| `rollback/ROLLBACK_EVIDENCE.json` | step-by-step rollback on isolated copies with tree hashes |
| `HARNESS_CORRECTIONS.md` | harness failures and corrections during development |

## Results (author's runs, Linux x86_64, FFmpeg never launched)
| Run | total | passed | skipped | failed | errors |
|---|---|---|---|---|---|
| **Untouched default suite** `python3.11 / 3.12 / 3.13 -m unittest discover -s tests -t .` (`logs/default-suite-py*.log`) | 454 | 451 | 3 (opt-in real-ffmpeg integration tests, off by default) | 0 | 0 |
| **Contract suite** on 3.11 / 3.12 / 3.13 (`logs/contract-suite-py*.log`) | 40 | 40 | 0 | 0 | 0 |

Contract suite: 157 checks (LIBRARY_ENFORCED 83 · LIBRARY_PERMISSIVE 23 · CALLER_GUARD_DEMO 21 · INSTRUMENT 9 · FIXTURE 10 · ROLLBACK 11), all ok; 259 library CLI runs (255 recorded per case + 4 in the temp-path-length test) + 1 instrument positive control; ≈17 s per run (limit: 10 s per case, 270 s per run, enforced); the `cases_digest` of observed behaviour is **identical on all three Python versions and from scratch directories of different path length** (an earlier pushed commit, `9f19846`, lacked the path-length normalisation; see `HARNESS_CORRECTIONS.md` #10).
The default suite itself launches its own *fake* ffmpeg/ffprobe Python stubs (that is how it was written); its 3 skips are the real-ffmpeg opt-ins. It was run unmodified.

Process accounting (`EXPECTED_VS_OBSERVED.json → process_accounting`): 260 harness-owned Python launches (259 CLI runs + 1 positive control, all through `harness_runner.py`); **0** external process launches, **0** network events, **0** filesystem mutations and **0** reads outside world/library/stdlib inside the library CLIs; media-tool sentinel hit by library CLIs: 0 (the positive control hits it once, on purpose); children saw only a PATH of harness-owned directories.

## Three layers, never mixed
`LIBRARY_ENFORCED` (the library itself refused/held/computed) · `LIBRARY_PERMISSIVE` (the library accepted what an owner must guard — pinned, so closing the gap fails the test and forces a contract update) · `CALLER_GUARD_DEMO` (an illustrative owner-side guard in `reference_guards.py`; never the reason a test passes; not a validator). A test passes only on what the library itself did.
`mutation_self_check.py` proves the suite is not vacuous: six one-line changes applied to scratch *copies* were all detected (`results/MUTATION_SELF_CHECK.json`).

## Reproduce
```sh
cd tools/video-core/evidence/integration-contract-20261105
VCC_TMP=/some/scratch python3 contract_suite.py                 # prints the summary, writes nothing into the repo
sh run_all.sh /some/scratch                                     # default suites x3, contract suite x3, builds EXPECTED_VS_OBSERVED.json, mutation check (SKIP_MUTATION=1 to skip)
python3 verify_unchanged.py                                      # nothing outside this directory differs from base
sh clean_export_check.sh <commit> /some/scratch                 # git archive export + suite from the export
```
Python 3.11+ standard library only; POSIX only (the suite exits 3 elsewhere). Temp worlds are created under `VCC_TMP` (default: system temp) and deleted.

## Inventory
`contract_suite.py` (40 cases) · `contract_lib.py` (fixtures, isolated world, the only launch point, source references) · `harness_runner.py` (audit-hook runner executing one real CLI in-process) · `harness_positive_control.py` · `reference_guards.py` ·
`fixtures/` (7 JSON fixtures + `FIXTURES_INDEX.json`; media-like bytes are regenerated deterministically and pinned by sha256 — **no media file is committed**) · `build_expected_vs_observed.py` · `mutation_self_check.py` · `verify_unchanged.py` · `clean_export_check.sh` · `run_all.sh` · `logs/` · `results/` · `rollback/` · `SOURCE_UNCHANGED.json`.

## Honest limits
Author checks only — no independent review. Linux/Python 3.11–3.13 only. The “videos” are non-media bytes, so everything that needs a real video (the `--mux` preflight, real receipts, real readback, re-hash-after-run, TOCTOU) is *documented, not demonstrated*; the audio receipt used with AV mux is harness-fabricated, which is itself the demonstration of GAP-04.
