# Colour-conversion study (2026-11-05) — baseline `color` → BT.709 yuv420p drift

Bounded, synthetic, single-frame study of why the original synthetic colour `#30506a` (RGB 48,80,106) decoded near 44,78,102, and of an **opt-in** correction candidate.
Everything here is **new**; no production module, original spec, old evidence or the once-rendered 6-second case was modified or re-rendered
(`scope-verification.json`). **Nothing is adopted or merged. The candidate is a proposal, not a production default.**

Start with [`HANDOFF.md`](HANDOFF.md) (EN / 日本語). Machine-readable results: [`measurements.json`](measurements.json), [`conclusions.json`](conclusions.json).

## What is in this directory

| path | what |
|---|---|
| `preregistration.json`, `preregistration.amendment-1.json` | hypotheses, arithmetic predictions, tolerances, pass/fail criteria — committed **before** the numbered experiments (commit `130aa55`); amendment 1 (E12 diagnostic tap) committed after E01–E11 and before E12 (`8b2f55d`) |
| `make_fixtures.py`, `study_lib.py`, `fixtures/` | original stdlib fixture generator (32 flat 32×32 patches in a 256×128 chart, PNG, exact BT.709 yuv420p source), exact-rational oracle |
| `run_study.py` | bounded driver: ≤12 experiments, each id once (`records/Exx.started.json` is written before launch), ≤10 s/process, 2 CPUs, 2 threads, rlimits |
| `records/Exx.json`, `raw/` | per-experiment record (argv, tool, rc, signal, wall time, hashes, negotiated scaler formats) and the **original output bytes**: `Exx.yuv` (raw yuv420p), `Exx.mp4`, `Exx.mp4.rgb24` (ffmpeg read-back), logs, ffprobe JSON |
| `analyze.py` | offline analysis (no ffmpeg): interior / boundary regions, YUV and RGB errors vs the exact oracle, criteria → `measurements.json`, `conclusions.json` (`*.run1-*` = first analysis output, kept; the later one only adds comparisons) |
| `make_contact_sheets.py`, `contact_sheets/` | PNG views (A raw decode, B raw error ×40, C mp4 decode, D mp4 error ×40, E flat reported colour) — views only; the bytes in `raw/` are the evidence |
| `candidate.patch` | opt-in `color_policy="bt709-tv-gbrp-v1"` (plan / render / CLI) + `tests/test_color_policy.py`; applies to base `b4ca4a3` |
| `run_patch_tests.py`, `patch-test-results.json`, `regression/` | applies the patch to **fresh temporary exports** and runs the suites on Python 3.11/3.12/3.13 |
| `verify_scope.py`, `scope-verification.json` | proves only this directory changed, base hash intact, records intact, limits respected |
| `docs/` | `ffmpeg -version` and the installed binary's own `-h filter=…` text (ffmpeg.org was **not reachable**: proxy 403; see HANDOFF) |

## Reproduce (bounded)

```bash
cd tools/video-core/evidence/colorstudy-20261105
python3 make_fixtures.py                      # fixtures are byte-deterministic (hashes in fixtures/fixtures.sha256)
python3 analyze.py                            # re-derives measurements.json / conclusions.json from the stored raw bytes (no ffmpeg)
python3 make_contact_sheets.py
python3 run_patch_tests.py /tmp/some-new-dir  # fresh export + patch + suites (dir must not exist)
python3 verify_scope.py
```

`python3 run_study.py run Exx` **refuses** every id already started — the 12 experiments were each run once. To measure again, use a different machine/FFmpeg and a new
directory; do not overwrite these records.
