# Adversarial review of `tools/video-core` @ `b4ca4a3` — report

Separate automated review. **Not** production acceptance, **not** a security certification, **not** a visual/audio/creative quality verdict.
Branch `codex/video-core-adversarial-20261105`, source `b4ca4a3b031351a631c0fbe1eb149935f4db19ba` (verified before any change). Date 2026-11-04. Linux x86_64, CPython 3.11.18 / 3.12.3 / 3.13.18, FFmpeg 6.1.1 present on the host but **never executed on media** by this review.

## 1. Verdict in six lines

1. The numeric/format core is sound under independent checking: WAV parsing, frame→sample/ms rounding (incl. exact-half cases), gain/channel/clip math, planner coverage/HOLD logic, mixer output bytes, episode validation and SRT all matched a from-scratch oracle in ≈21,800 seeded/exhaustive comparisons with **0 disagreements** (§4).
2. Input hygiene held: 0 path-escape, overwrite, or "rejected-input-had-a-side-effect" cases in 27 audited rejections and 11 audited dry-runs.
3. **10 defects validated** (2 MEDIUM, 8 LOW; 11 reproducer tests because F-RCPT-1 has an audio and an avmux test) plus 1 stub-only static concern (F-PATH-1) — all in the *failure/receipt/qualification* layer, none in the arithmetic: §3. Most important for adoption: **F-INT-1** (a cancel is reported as tool-unavailable / rejection / inspection-failed and more children are launched) and **F-REC-2** (an audio receipt "qualifies" a stem on three self-asserted fields).
4. Every reproducer asserts the *correct* behaviour. Default mode shows the 11 reproducer tests as expected failures; `--strict` shows them as 11 hard failures. None was weakened (§5).
5. Documented limitations were confirmed, not rediscovered as defects; one (transient swap) has a wider window than the docs imply (R-1).
6. Unverified: Windows/macOS, real FFmpeg/AAC behaviour, ceiling-sized inputs, quality of any kind (§7).

## 2. Test results (exact logs in `logs/`)

| run | total | passed | skipped | failures | errors | expected-failures (reproduced defects) | unexpected successes | wall |
|---|---|---|---|---|---|---|---|---|
| **Existing default suite, unchanged**, py3.11 | 454 | 451 | 3 | 0 | 0 | – | – | 62.8 s |
| existing suite, py3.12 | 454 | 451 | 3 | 0 | 0 | – | – | 64.6 s |
| existing suite, py3.13 | 454 | 451 | 3 | 0 | 0 | – | – | 64.6 s |
| **Audit suite, default mode**, py3.11 / 3.12 / 3.13 | 89 | 78 | 0 | 0 | 0 | 11 | 0 | ≈30 s each |
| **Audit suite, `--strict`**, py3.11 / 3.12 / 3.13 | 89 | 78 | 0 | 11 | 0 | 0 | 0 | ≈30 s each |

The audit suite has no skips on Linux; the 3 skips of the existing suite are its opt-in integration tests (unchanged, as designed). Overall wall limit enforced by `run_audit.py` (`faulthandler` exit): 300 s; actual ≈30 s. Every external child is bounded to ≤ 10 s by `adv_support.child()`; the longest fixtures sleep 8 s and are killed only through their own process group / recorded pid.
Seeds and case counts: `runs/<label>/results.json` → `seeds_and_case_counts` (WAV 20261105 · 457 cases; episodes 20261106 · 900; gain/average 20261107 · 13,565; frame boundaries 20261108 · 4,775; fonts 20261204 · 1,380; planner/mixer 20261110 · 700; spans 20261111). The canonical plan hash `4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21` is identical on all three interpreters.

## 3. Validated defects (details, exact lines, fixtures, fixes: `FINDINGS.json`)

| id | sev | one line | key source lines |
|---|---|---|---|
| **F-INT-1** | MED | SIGTERM/SIGINT outside the mux/render step is swallowed by `run_bounded`; run ends `SKIPPED_TOOL_UNAVAILABLE` (rc 3) / `preflight_probe_failed` (rc 1) / `INSPECTION_FAILED` (rc 4), and one more child is launched after the signal in each stage | `runner.py:268-270`; `tools.py:98-100`; `avmux/mux.py:108-112, 249-253`; `render.py:74-77` |
| **F-REC-2** | MED | avmux "receipt qualification" = `status`, one `outputs[stem].sha256`, `readback.wav.sample_count`. A 3-field hand-written receipt, `all_ok:false`, `partial:true`, `quality_evaluated:true`, duplicate `status` keys and a float count are all accepted | `avmux/plan.py:105-112` |
| F-REC-1 | LOW | malformed receipt JSON (`[]`, `null`, wrong types, deep nesting) → `AttributeError`/`RecursionError` instead of a typed rejection | `avmux/plan.py:100-113` |
| F-ENC-1 | LOW | non-UTF-8 byte in a caller root: video `render` promotes `output.mp4` then dies writing the final receipt (stays `IN_PROGRESS`); avmux dies after creating an empty run dir | `receipt.py:47-48`, `paths.py:113-123`, `render.py:113-114`, `avmux/mux.py:123-137` |
| F-RCPT-1 | LOW | after an internal exception the receipt lists `outputs: []`, no `sha256_after`, no `tmp_cleanup` although partials exist | `audiomix/mix.py:181-183`, `avmux/mux.py:140-142`, `render.py:110-112` |
| F-RUN-1 | LOW | drain-limit breach is `FLOOD_KILLED` or `EXITED_ZERO/ok` depending on a 50 ms poll race | `runner.py:254-267` |
| F-RUN-2 | LOW | Ctrl-C during pipe drain escapes `run_bounded` and leaves an owned-group member alive | `runner.py:272-282` |
| F-NUM-1 | LOW | `finite_seconds(10**400)` → `OverflowError`, not the documented `ValueError` (**already independently reproduced and assigned to the implementation owner; no fix here**) | `numeric.py:54` |
| F-CLI-1 | LOW | lone-surrogate JSON key in a rejected input: all 3 CLIs print a traceback, empty stdout (exit 1) | `episode.py:161`, `audiomix/manifest.py:88`, `receipt.py:47-48`, `*/cli.py _emit` |
| F-AUD-1 | LOW | zero-sample frame range on an *unselected* or *OMIT* event rejects the whole audio manifest (fps > 48000) | `audiomix/plan.py:109-113` |
| F-PATH-1 | LOW, stub only | `check_media_for_inspect` checks `S_ISLNK` only (Windows reparse point unverified) | `paths.py:214` |

Severity rationale: MEDIUM = misleading status or qualification on an advertised guarantee without data loss; LOW = contract slip / narrow domain / conservative failure mode. Nothing found corrupts audio or video bytes, mislabels a hash, or lets a rejected request write anything.
The known AAC zero-vs-absent summary defect and the CPU/memory-cap gap (other session) were neither searched for nor fixed.

### Minimal patch proposals (for the implementation owner; none applied here)

```text
F-INT-1   runner.py  +def raise_if_interrupted(res): raise KeyboardInterrupt if res.outcome == INTERRUPTED
          call it after every run_bounded in tools.py (probe_version,_listing), readback.run_probe, avmux/mux.py (_probe,_hash_text,_run), render.py
          render()/mux(): add `except KeyboardInterrupt: receipt["status"] = STATUS_INTERRUPTED` next to `except Exception` (model: audiomix/mix.py:179-180)
F-REC-2   avmux/plan.py _check_audio_receipt: parse with parse_json_bytes (dup keys, NaN, depth); require schema=="video_core.audio_receipt/1", written is True,
          stage=="complete", readback.all_ok is True, stem.partial is False, quality_evaluated is False, type(sample_count) is int, plan_sha256 present;
          surface receipt.warnings + coverage.gap_count in the avmux plan; document that the receipt is pinned but unauthenticated
F-REC-1   same function: isinstance(rec, dict) / outputs list-of-dict / readback.wav dict checks -> audio_receipt_unreadable; catch RecursionError via the strict loader
F-ENC-1   paths.real_directory: reject roots that cannot be encoded as UTF-8 (UnsafePath root_invalid); receipt.dumps(...).encode("utf-8","backslashreplace")
F-RCPT-1  factor the inventory/sha256_after/tmp-cleanup block into finalize(run_dir, receipt) and call it best-effort from the except handlers
F-RUN-1   runner.py after the wait loop: if state["flood"].is_set() and result.kill_reason is None: result.kill_reason = "flood"
F-RUN-2   wrap runner.py:272-282 in the KeyboardInterrupt handling (or try/finally that SIGKILLs the group when the drain is incomplete)
F-NUM-1   numeric.py:54  "... (got {_describe(value)})"            (owner already assigned)
F-CLI-1   CLI _emit: ensure_ascii=True for stdout, or ascii(k) in the unknown_field issue path
F-AUD-1   audiomix/plan.py:112  add `and sel and e.state in ("USE","INTENTIONAL_SILENCE")`
F-PATH-1  paths.py:214  `if is_link_like(st):`
R-1       audiomix/mix.py: open each asset once after the hash check, keep the fd, compare fstat(ino,size,mtime_ns) before every chunk
O-3       fontcheck.py: sort groups/segments once and bisect
```

## 4. Not reproduced (hypotheses that failed to find a defect)

WAV parser vs oracle (457 cases) · frame→sample/ms rounding, drift, round-half-even (4,775) · planner/mixer vs Fraction oracle (700 scenarios: 271 reject, 346 HOLD, 83 mixed incl. 10 `CLIPPING_REJECTED` and 4 `MIXED_WITH_CLAMPING`; exact stem bytes, clip counts, first clipped sample) · episode validator/SRT (900) · hostile JSON bytes, 150k-deep nesting (all typed) · path traversal / symlink / FIFO / overwrite · rejected-input side effects (27) and dry-run purity (11 CLI calls) under an `sys.addaudithook` that sees every launch/exec/file write regardless of spelling · corrupt fonts (1,380) · video-only default and baseline hash · diagnostic truncation boundaries · wall-clock bounds.

## 5. How defects are kept honest

* Reproducers assert the **correct** behaviour. `@known_defect` = `unittest.expectedFailure` in default mode; `ADV_STRICT=1` / `run_audit.py --strict` removes it, so the same assertions fail hard. If an implementation fix lands, the default run reports **UNEXPECTED SUCCESS** — nobody edits an expectation.
* Non-assertion exceptions inside a known-defect test (a broken scenario) are re-reported as **ERROR** and scenario preconditions raise `HarnessError`; a broken harness can never masquerade as a confirmed defect (this caught H6/H7 below).
* The oracle (`adv_oracle.py`) imports nothing from `video_core`.

## 6. Declared limitations confirmed / observations

R-1 transient swap-and-restore undetected (window = whole mix, not a "tiny gap") · L-HARDLINK accepted (documented) · L-SIGKILL leaves child + `IN_PROGRESS` (documented) · O-1 all-OMIT zero-fill stem is `MIXED_VERIFIED` (warnings carry the truth; avmux ignores them) · O-2 hash checks drop timestamp columns · O-3 font scan O(chars×groups), 0.43 µs per pair measured, extrapolations (5,000×20,000 ≈ 43 s; limit-max ≈ 31 h) **not executed** · O-4 integer-literal bomb defence is the interpreter's 4300-digit limit (3.11, 1 M digits, limit disabled = 4.3 s, measured once by hand) · O-5 names such as `COM¹`, U+FF0F, NFD/NFC are accepted (platform behaviour untested).

## 7. Unverified

Windows and macOS · real FFmpeg mux/AAC priming/decoded hashes (fake tools return canned text and are never mux evidence) · 10-minute / 200 M-work / 4 GiB ceilings · unreadable-but-present assets (suite runs as root; `audiomix/plan.py` opens the asset outside a `try`, so an `OSError` would surface untyped — static concern only) · audiomix CLI SIGTERM wiring end-to-end · perceptual sync and any quality.

## 8. Integrity and clean-export validation

See `INTEGRITY.md` (generated at the end of the review; commands and results are quoted there verbatim).

## 9. Harness defects and failed attempts (all kept)

H1–H7 in `FINDINGS.json`; logs of the first matrix (5 ERRORs caused by SIGINT=SIG_IGN under `&`) and the superseded 86-test matrix in `logs/attempts/` (`ATTEMPTS.md`). Exploratory probes with their outputs: `probes/`.

## 10. Re-running

```bash
cd tools/video-core/evidence/adversarial-review-20261105
python3.13 run_audit.py            # default: defects are expected failures
python3.13 run_audit.py --strict   # defects are hard failures (gate for adoption)
./run_all.sh "$(cd ../.. && pwd)" /tmp   # 3 interpreters x 2 modes, one at a time
```
