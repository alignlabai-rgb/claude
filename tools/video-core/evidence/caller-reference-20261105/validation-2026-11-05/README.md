# Validation 2026-11-05: ten independently reported false READYs against `508d263`

The author's 68 tests passed at `508d263` while independent validation found ten inputs that returned READY. This directory keeps the evidence of reproducing them **before** any fix, the attempts, and the result after the repair. Nothing in `508d263` was modified (it stays reproducible from git); the repair is a new commit.

## Reproduction (before)

`reproduce_false_ready.py --candidate <dir>` seeds from the candidate's own `world()`, corrects `audio_receipt.outputs[role=plan].sha256` to the owner's plan pin **in memory** (the 508 fixture itself held a different value there), then changes ONE field per case. The candidate under test was an unmodified `git archive` export of `508d263`.

* `before-508d263.attempt1-case8-also-changed-gap-policy.json` — my first attempt: 9 of 10 reproduced. Case 8 stayed HOLD because I had changed **two** things (also the required gap policy); kept as an attempt, not hidden.
* `before-508d263.json` — corrected case 8 (only `treated_warning_codes` changed): **10 of 10 false READY**, each with all 11 comparison stages PASS (case 9: the audio-receipt stage SKIPPED because the profile removed it). Controls as reported: corrected positive READY, valid unselected UNKNOWN READY, cross-job REJECTED, ordinary ZERO_FILL warning HOLD.
* `after-fix.json` — same script, repaired candidate: 0 false READY; case 1 HOLD, cases 2–10 REJECTED; all four controls unchanged.

## Producer contract used (pinned core `c7da5ac…`, read from code; no owner data)

* `plan_sha256 = sha256(canonical_json)` and the receipt's `plan` output file **is** those bytes, so `outputs[role=plan].sha256 == plan_sha256` for the audio and the video receipt (audiomix `plan.py`, video `plan.py`; asserted by the core test `test_audio_mix`). The 508 fixture's `H(plan-file)` was a fixture error, not a contract feature.
* Output entries are exactly `{name, role, bytes, sha256, partial}` (`_file_record`/`finalize.inventory`); roles audio `stem|stem_partial|plan`, video `video|video_partial|srt|plan|ffprobe_json`; `partial` is true exactly for the `*_partial` roles.
* Audio plan `output` = `{file, sample_rate 48000, channels, bits 16, samples, bytes}` with `bytes = 44 + samples*channels*2`; `timeline.total_samples = floor((2*frames*d*48000+n)/(2*n))`; `coverage = {covered_samples, gap_count, gaps[<=MAX_REPORTED_GAPS=100], gaps_truncated}`.
* Audio receipt `readback.checks` are exactly `channels, sample_rate, bits, sample_count, file_bytes`, each `{name, expected, observed, ok}`; `readback.wav` carries the ints.
* Video `compare()` records `video_stream_count, non_video_stream_count, width, height, r_frame_rate, avg_frame_rate, decoded_frame_count, [container_frame_count], format_duration, video_duration, codec_name, pix_fmt`; `decoded_frame_count.expected` is the plan's integer, `.observed` is ffprobe's `nb_read_frames` **string** (preserved: no float/bool/lenient coercion; a canonical decimal string or an equal int is accepted, anything else is a contradiction); `all_ok == all(ok)`.
* `unresolved_report` is the plan's `unresolved` list verbatim: `{event_id, source_id, state, selected}`.

## What each case showed and what now stops it

| # | Hole in 508 | Boundary now |
|---|---|---|
| 1 | `checks=null` treated as nothing to check | absent / null readback evidence → HOLD `READBACK_EVIDENCE_MISSING`; every required check name must be present (profile v0 lists them) |
| 2 | only `expected`/`ok` looked at | for exact-equality checks `ok` must equal `observed == expected` (observed int or canonical decimal string; null only with `ok=false`) else REJECTED `CHECK_INCONSISTENT`; frame count must equal the audio timeline |
| 3 | `bytes` only type-checked | non-negative; stem bytes == plan geometry; stem name == plan output file |
| 4 | plan output `partial=true` ignored | `partial` must be a boolean and consistent with the role (`OUTPUT_FLAG_CONTRADICTION`); a `*_partial` entry is HOLD `OUTPUT_PARTIAL` |
| 5 | plan output hash ignored | plan output sha256 must equal the receipt's `plan_sha256` and (audio) the owner's pin |
| 6 | coverage never validated | covered/gaps/gap_count/truncation consistency, timeline sample formula, output geometry; missing coverage HOLD |
| 7 | non-object sibling in `outputs` ignored | the whole list is validated; unknown/duplicate roles; max 32 entries |
| 8, 9 | profile identity honoured caller-modified policy | exact bounded v0 profile only; same id/version with any changed, extra or malformed value → REJECTED `PROFILE_POLICY_MODIFIED`; missing fields HOLD; other ids/versions REJECTED |
| 10 | `selected` compared with `is True` only | receipt unresolved rows validated exactly (4 keys, exact boolean, unique) and compared for equality with the plan's; unselected rows stay non-blocking and are preserved |

## Further holes found while repairing (not in the report)

The exact-leaf test (every leaf of a conforming world must be decisive unless declared not-checked) found, in addition: expected rows that were never compared with the plan (existence/state/binding of unselected owner rows), selected USE rows with unchecked binding fields, free-text row dispositions, an unvalidated `fps.rational`, unknown readback check names, and unknown keys in stage envelopes. Each is now validated; the leaves that remain unchecked are listed (see `not_checked` in every result and `IGNORED_LEAVES` in the test).

## Files

`reproduce_false_ready.py`, `before-508d263*.json`, `after-fix.json`. Tests / mutation controls / results are in the parent directory (`RESULTS.2026-11-05.txt`; the 508 `RESULTS.txt` is unchanged).
