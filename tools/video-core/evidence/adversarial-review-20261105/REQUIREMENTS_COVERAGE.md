# Requirements coverage map

Legend: **T** = truly exercised by an executable test in this directory · **P** = partly exercised (what is missing is stated) · **S** = static reading only · **U** = unverified / out of reach.
Test names are `test_adv_laneN_*.py` classes; `F-…`/`O-…`/`R-…` refer to `FINDINGS.json`.

| # | Requirement in the task brief | Status | Where / what was actually done |
|---|---|---|---|
| 0 | Verify branch and source SHA first | T | `git rev-parse` = `b4ca4a3b…` on `codex/video-core-adversarial-20261105` before any change; pre-run SHA-256 of all 131 tracked files in `tools/video-core` saved in `logs/pre-run-tracked-file-sha256.txt` |
| 0 | Never push to `codex/video-core-cloud-20261105`; no PR/merge/deploy/force push | T | Only the audit branch was pushed (fast-forward) |
| 0 | New artifacts only under `evidence/adversarial-review-20261105/`; sources/specs/prior evidence byte-identical | T | `INTEGRITY` section of `REPORT.md` (git diff against `b4ca4a3` is empty outside this directory) |
| 0 | Existing default suite run unchanged, 3.11/3.12/3.13 | T | `logs/baseline-default-suite-python3.1{1,2,3}.log`: 454 / 451 pass / 3 skip / 0 fail each |
| 0 | Audit tests on 3.11/3.12/3.13, total/pass/skip/fail separately | T | `logs/audit-python3.1X-{default,strict}.log`, `runs/*/results.json` |
| 0 | Seeds + case counts recorded; per-child ≤ 10 s; documented suite wall limit | T | seeds in `results.json`/`observations.jsonl`; `adv_support.child()` hard 10 s; `run_audit.py --wall-limit 300` (faulthandler exit) |
| 0 | Separate temp dir per bounded synthetic test, outside source; run evidence preserved | T | `Sandbox` (system temp), evidence copied to `runs/<label>/cases/<test>/` |
| 0 | No rerender, no new stem/mux/media, no external API/download | **P** | No real ffmpeg/ffprobe run, no download, no network. **Disclosure:** the audio mixer *API* was run on tiny synthetic tones in temp dirs (stems < 100 KB, on the order of 100 per interpreter run) to compare against the oracle; none is retained or committed (`*.wav` is git-ignored and the temp dirs are deleted). The mux and render orchestration used owned fake tools that emit canned text. |
| 1 | Lane 1: manifest/JSON/WAV/SRT parsers and numeric algebra | | |
| 1a | duplicate / non-finite JSON values | T | `JsonStrictness` (75 mutations × 3 loaders) |
| 1b | integer bit length, denominator growth | T/P | INT64 boundary tables (`ManifestBounds`), gain num/den ≤ 262144/65536, 4300-digit literal (`JsonStrictness`, O-4); no unbounded Fraction growth path exists (denominators ≤ 1001·65536) – S |
| 1c | near boundaries | T | `NumericAlgebra` (4,775 frame-boundary oracle cases incl. 318 exact-half samples; 13,565 gain/average cases), `ManifestBounds` |
| 1d | inconsistent sizes/counts, truncated/trailing chunks, stereo rules | T | `WavParser` (457 cases vs independent oracle), `PlannerMixerProperty.test_channel_rule_matrix_against_oracle` |
| 1e | offset / clip / caption / event endpoints | T | `ManifestBounds.test_event_and_gain_boundaries`, `EpisodeDifferential` (900), `SampleSpans` |
| 1f | malformed / untrusted receipt content | T | `AudioReceiptInput` (F-REC-1, F-REC-2) |
| 1g | do rejected values create files or launch targets? | T | `RejectedInputsAreInert` (27 requests under an audit hook) |
| 2 | Lane 2: local path / source integrity / output protection | | |
| 2a | relative paths, hostile names | T | `PathSyntax` (66 reject / 8 accept) |
| 2b | symlinks, FIFOs, dirs, hard links, symlinked roots | T (POSIX) | `AssetResolution` |
| 2c | reparse-point handling code | **S + stub only** | F-PATH-1 (stubbed `lstat`); no Windows run |
| 2d | extension/codec assumptions | T/P | decoy files, `.mp4` that is not video accepted at plan time but labelled `CALLER_STATED_UNVERIFIED`; real codec mismatch is only checked in the (fake-tool) preflight |
| 2e | existing outputs, overlap with assets | T | `OutputProtection` |
| 2f | hash changes across stages, source aliasing | T | `HashAcrossStages` (persistent swap, transient swap R-1, deletion F-RCPT-1) |
| 2g | known TOCTOU vs new defect distinguished | T | R-1 is *documented class*, new information stated; no new TOCTOU defect claimed |
| 3 | Lane 3: timeline invariants vs independent oracle | | |
| 3a | rational fps, 48 kHz rounding, no accumulation | T | `SampleSpans`, `NumericAlgebra` (oracle imports nothing from `video_core`) |
| 3b | scopes, intentional silence vs unresolved, HOLD propagation | T | `SelectionAndHold`, `PlannerMixerProperty` (700 scenarios) |
| 3c | overlap / clipping | T | same + exact clip counts/first clipped sample |
| 3d | video-only default and canonical hash compatibility | T | `VideoOnlyDefault` (hash `4bc20b3f…9f21`, archived plan byte-equal, import isolation) |
| 4 | Lane 4: execution / receipt failure semantics | | |
| 4a | nonzero vs timeout vs incomplete drain | T | `RunnerSemantics` |
| 4b | cancellation | T (API) / **P** | audio mixer cancel/timeout/KeyboardInterrupt via hooks (`AudioMixerStops`); SIGTERM on avmux CLI (`InterruptSemantics`, F-INT-1); audiomix CLI SIGTERM wiring not exercised – U |
| 4c | diagnostics truncation | T | `RunnerSemantics`, `AvmuxOrchestration` |
| 4d | partial outputs, IN_PROGRESS / UNKNOWN | T | `AvmuxOrchestration`, `InterruptSemantics` (SIGKILL), F-RCPT-1, F-ENC-1 |
| 4e | no-launch plan purity, truthful technical statuses | T | `PlanPurity`, `_truthful()` checks on every avmux receipt |
| 4f | do not implement the optional resource envelope | T | nothing implemented; CPU/memory caps untested by design |
| 5 | Distinguish validated / non-reproduced / declared / platform-untested / harness defects | T | `FINDINGS.json` sections |
| 6 | Each finding: lines, fixture, command, expected/actual, consequence, severity, minimal fix | T | `FINDINGS.json`, `REPORT.md` §3 |
| 7 | Machine-readable FINDINGS.json, EN/JA handoff, exact logs, this mapping | T | this directory |
| 8 | Clean-export validation | T | `REPORT.md` §8 |
| — | Windows / macOS behaviour | **U** | not run |
| — | Real FFmpeg mux, AAC priming, ceiling-sized inputs, perceptual sync, quality | **U** | out of scope / explicitly not evaluated |
