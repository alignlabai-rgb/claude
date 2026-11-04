# Limitation report (author's self-check — not independent acceptance)

Status: code and tests delivered for review. This is **not** a claim of production integration (SYS015/Motion) or of
creative, visual or audio acceptance. The author's checks below are not an independent review.

## Verified here, and where
* Linux x86_64, Python 3.11.x / 3.12.x / 3.13.x (default suite passes on all three), FFmpeg/ffprobe `6.1.1-3ubuntu5` with libx264 and the filters listed in the plan.
* One real render of the supplied case plus two additional opt-in synthetic integration renders (see `evidence/README.md`).

## Not verified / not claimed
* **macOS and Windows: untested.** Paths, process-group handling and `os.rename` are written to be portable and POSIX-only tests are skipped elsewhere, but no claim is made for those platforms. On Windows only `CREATE_NEW_PROCESS_GROUP` is used: orphan cleanup is weaker, there are no POSIX signals, and `max_argv_bytes` should be lowered to ≈30000.
* **Rendered quality is not evaluated.** `READBACK_MATCH` means ffprobe metadata + decoded frame count equal the plan. Solid-colour pixel samples of the evidence file are within ±4/255 of the nominal colour but systematically *lower* (e.g. nominal `#30506a` = 48,80,106 decoded as 44,78,102). Cause: the colour source is converted YUV→YUV through its internal format before the explicit BT.709 conversion; plus 8-bit limited-range 4:2:0 quantisation. A dev experiment (single frame, not an MP4) showed that inserting `format=gbrp` before the `scale=out_color_matrix=bt709` step brings the error to ≤3. **Not applied** because the supplied case was to be rendered once and the evidence must match the committed code; it is a one-line follow-up in `plan._clip_filter`.
* Burn-in: the integration test shows ffmpeg accepts the generated `drawtext` graph with a real CJK font and that the output reads back with the expected properties. Glyph appearance, line breaking, bidirectional/complex-script shaping and legibility were **not** visually reviewed. The cmap check proves coverage only, supports sfnt formats 4/12, rejects `.ttc`.
* File sources are lightly exercised (one synthetic mp4 + png + colour run). They are conformed to the project rate with `fps` (drop/duplicate), letter-boxed to the canvas, audio ignored, always start at source time 0. A source shorter than its clip is **not padded**: the frame count then mismatches and the receipt says `READBACK_MISMATCH`.
* Resource bounds: wall-clock, drained bytes, output size (`-fs`, truncation ⇒ invalid partial ⇒ mismatch), frame/pixel/duration/argv limits. **No CPU or memory cap** on FFmpeg itself.
* TOCTOU: sources are checked at plan time, re-resolved and hashed immediately before launch, and re-hashed afterwards (`inputs_unchanged`); a file swapped in the tiny gap between the last check and FFmpeg's `open` is not prevented. Hard links to outside files cannot be detected.
* Killing the *orchestrator* with SIGKILL leaves its FFmpeg child running until FFmpeg ends on its own (bounded by `-frames:v`/`-fs`) and the receipt stays `IN_PROGRESS` (= UNKNOWN). SIGINT/SIGTERM are handled only by the CLI; library callers must handle them (the runner catches `KeyboardInterrupt`).
* MP4 bytes are not promised reproducible across machines/FFmpeg builds/thread schedules (bit-exact flags are set; hashes are recorded, not compared).
* Requires FFmpeg with `-fps_mode` (≥ 5.1) and libx264; no version gate beyond reporting the version and probing the encoder/filters/demuxers. Other FFmpeg versions are unverified.
* The validator is a hand-written stdlib mirror of `schemas/episode.schema.json` (a test pins its bounds to the schema file). It is deliberately stricter: JSON integers only, clips must be listed in order, captions may not contain `\r`, blank lines or other control characters. Clip ids and caption ids are unique per collection, not across both; captions may overlap each other.
* Audio is an interface only (`audio.py`); nothing is muxed.
* SRT has millisecond resolution; at frame rates where two frame boundaries round to the same millisecond (> 1000 fps) validation rejects the episode (`caption_srt_resolution`) instead of emitting a zero-length cue.

## Known failures
None in the default suite at the time of writing. Two integration tests are skipped by default (opt-in) and were run once manually with the result recorded in `evidence/`. A first run of `evidence/independent_check.py` reported two failures caused by a parsing bug in that script (it dropped a `0,` csv row); the media was fine, the script was fixed, and both runs are kept in `evidence/`.

## Compatibility follow-up (second commit; Windows findings)

Changed: (1) the runner's return-code interpretation and outcome classification are now pure, tested functions
(`split_returncode`, `classify`) so the POSIX-signal and Windows-exit-code conventions are both pinned by tests that run on any OS —
no Windows execution happened in the Linux cloud; the failing `exit_code is None` expectation was a POSIX-only assumption, not a runner bug;
(2) FFmpeg capability listings are parsed by `capabilities.py` (2- and 3-flag filter layouts, CRLF, exact names, noise rejected) and an
unrecognised layout is an explicit `probe_failed` instead of "all missing"; (3) a Windows command-line bound plus a plan-time estimate that fixes
a defect of the first delivery (1024 bytes of slack per file input falsely rejected ≈100 file clips).

Unchanged on purpose: the planned command and the colour behaviour — the baseline plan hash `4bc20b3f…9f21` is pinned by a test, and the **colour
conversion drift described above remains an open issue**. The baseline was not re-rendered.

Still not verified: any Windows run (the follow-up tests that need POSIX fake tools remain skipped there); FFmpeg 8.1.2 **encoder and demuxer** listing layouts
(only the filter lines quoted in the finding were available; the 6-character encoder layout is assumed from libx264 having been found on 8.1.2);
the two-flag fixture is a reconstruction, not captured output (see `tests/fixtures/ffmpeg_listings/README.md`); on Windows, episodes needing more than
≈200 colour clips (≈100+ file clips depending on path length) exceed the command-line bound and are rejected rather than split.

## Stage A (third commit): Windows residuals

* **Unit corrected.** The Windows command-line bound counted Python code points (`len(list2cmdline(...))`); Windows consumes UTF-16 code units. The
  requester's own Windows launch of an owned Python with one 17000-emoji argument measured python_len 17106 / UTF-16 34106 / UTF-8 68106 and failed with
  WinError 206, which the 100000-byte and 32000-character checks both under-counted. `limits.cmdline_utf16_units` now measures UTF-16 code units; lone
  surrogates are counted as one unit (measurement never raises) and rejected on Windows (`argv_ill_formed_utf16`), only reported elsewhere.
* **Two Windows test failures fixed in the tests, not in the product:** one asserted an absolute estimate `< 40000` that depends on the host TEMP prefix
  (it now asserts the estimate equals the really materialized argv, which also proves there is no per-file slack); the other created three 100-character
  directories and hit WinError 3 because Windows long paths are off (it now checks long-root arithmetic with pure string estimates and creates no long paths).
  Long paths were not enabled and no genuine bound was relaxed.
* Author checks are Linux-only (Python 3.11/3.12/3.13, FFmpeg 6.1.1); the Windows results above are the requester's. Baseline plan hash and colour behaviour unchanged.

## Stage B (fourth commit): offline audio planner and WAV mixer

Delivered as a generic, optional component (`video_core/audiomix`, `AUDIO_SPEC.md`). Author checks are **Linux-only**; nothing was run on Windows or macOS.

* **No mux and no quality verdict.** The deliverable is a structurally verified WAV stem. Audio, speech and creative quality are not evaluated; `READBACK` means RIFF fields, sample counts, peaks and hashes only. A later mux needs its own validation (see the seam in `AUDIO_SPEC.md`).
* **Opaque assertions.** `source_id`, `role`, `decision_ref`, `provenance_ref` are caller text. Nothing authenticates them; they are not evidence of human adoption.
* **Strict inputs only.** Only 48 kHz signed 16-bit PCM WAV (mono/stereo, plain PCM, no WAVE_FORMAT_EXTENSIBLE). Everything else is rejected, never converted.
* **Pure-Python mixing.** Speed is bounded by `max_mix_work`/`max_runtime_seconds`, not optimised: tens of millions of sample-channels per minute-scale run. The default 10-minute / 200 M-work ceilings are untested at their maximum size here.
* **Partial files are not valid WAVs.** `stem.wav.partial` carries the planned length in its header; it is preserved for inspection only.
* **Race windows.** Assets are hashed at plan time and re-hashed after mixing (`inputs_unchanged`); a change in between is reported as `UNKNOWN`, not prevented. Windows reparse-point (junction) rejection is implemented and unit-tested with a stat stub on Linux, not on a real junction. Cancellation by SIGTERM was exercised on Linux only (`signal.SIGTERM` handling differs on Windows).
* **Integration seam still open.** No existing workflow consumes this yet; manifests must be produced by a caller that owns the creative/binding decisions.
* Colour-conversion drift (baseline) is unchanged and still open.

## Finite-limits fix (fifth commit)

Independently reproduced finding: `Limits(max_runtime_seconds=nan|inf)`, `Limits(probe_timeout_seconds=nan)` and `Limits(max_drain_bytes=nan)` were accepted at `6206b1d`
(and the same at `8f1e4c5`, including `AudioLimits`); a NaN deadline makes `monotonic() >= deadline` permanently false, silently disabling the wall-clock bound. Now `numeric.py`
validates every caller limit (`Limits`, `AudioLimits`, `.replace()`), every CLI flag that reaches them, and the numeric arguments of `runner.run_bounded` itself
(`timeout_s`, `grace_s`, `drain_timeout_s`, `max_drain_bytes`, the capture-limit pairs). Rules: no bool; integer limits must be real ints (no `100.0`/`5.5`);
time limits finite, > 0 and <= 86400 s (grace/drain waits <= 3600 s); sub-second positive timeouts remain valid. All rejections are `ValueError` raised before any process launch or file write;
the CLIs turn them into exit code 2. Tested on Linux only (author); the requester's Windows retest should rerun the default suite. Existing ceilings, defaults and the baseline plan hash are unchanged.

## AV mux (sixth commit): optional `video_core.avmux`

Author checks are **Linux-only, FFmpeg/ffprobe 6.1.1-3ubuntu5 only**; no other FFmpeg version, macOS or Windows is claimed (the requester will run Windows). The real evidence is one 3-frame synthetic case (see `evidence/avmux-synthetic/`).

* **Scope of support:** one already-H.264 video stream starting at 0 (stream copy) and 48 kHz s16 PCM mono/stereo WAV; MOV+`pcm_s16le` and MP4+native `aac` only. Other codecs, containers, A/V offsets, multiple streams, audio delay, `-shortest`-style policies, VFR, B-frame start offsets other than 0 are rejected, not handled.
* **MP4/AAC is lossy.** Its contract is a *declared tolerance* (1024 samples = one AAC frame, a choice, not a standard) on the decoded sample count and the audio start. In the real case the container said 4800 samples while the decoder returned 5120 (+320, untrimmed end padding): within tolerance and reported, but it means decoded AAC is not sample-aligned to the WAV and the route must not be used where sample accuracy matters. No perceptual sync or quality was evaluated.
* **Receipt imprecision (documented, not hidden):** `audio_packets.discard_padding`/`skip_samples` summarize the first non-zero values; ffprobe's raw per-packet side data (including `discard_padding: 0`) is only in the independent check output. A future change should record every side-data entry.
* **Plan is deliberately weaker than mux:** without ffprobe, video codec/size/rate/frames/start are caller-stated (`CALLER_STATED_UNVERIFIED`); only the explicit `--mux` preflight measures them. Reusing a prior receipt bound by the same SHA-256 to avoid re-probing is not implemented.
* **Decode checks cost time:** the readback decodes the whole video twice and the audio once with bounded timeouts (`inspect_timeout_seconds`); long videos need larger, still finite, limits. Per-frame hash text is capped at 4 MiB (≈ the schema's 18000 frames).
* **Race window:** inputs are hashed at plan time and re-hashed after the mux (`inputs_unchanged`); a change in between is reported as `UNKNOWN`, not prevented.
* **Tests use fake FFmpeg/ffprobe scripts** for orchestration (POSIX only; they skip on Windows); they are never mux evidence. Windows junction rejection is stub-tested on Linux.
* **No integration:** nothing consumes this; the caller owns which video/stem to mux and every creative decision. Colour-conversion drift (baseline) is unchanged and still open.

## Stage C (seventh commit): AAC packet record correction and optional resource containment

**A. AAC receipt accuracy.** The earlier receipt summarised packet side data as first-non-zero scalars, so the real MP4's observed `discard_padding: 0` showed as `null`. The mux receipt now records every Skip Samples record per packet (`side_data_records`: `observed` keys exactly as ffprobe printed them, `absent_fields`),
with per-field counts of reported / absent / observed-zero / non-zero (traceable by `packet_index`). Old evidence and receipts are untouched; `evidence/avmux-aac-packet-correction/` holds NEW re-inspection-only receipts for the existing outputs (hashes verified before and after, nothing remuxed).
The earlier statement "Receipt imprecision (documented, not hidden)" in the AV mux section is therefore resolved for new runs, not rewritten for old ones.

**B. Optional resource envelope (`CONTAINMENT.md`).** Closes the documented "no CPU or memory cap" gap only for callers that opt in, and only for the main ffmpeg process.
* **Verified on the author's Linux host:** per-process `RLIMIT_CPU/AS/FSIZE` applied by a new worker, read back, inherited by the target; CPU and memory exhaustion of owned fixtures; unsupported requests refuse before any launch; timeout/cancel cleanup of a target/child/grandchild with an unrelated sentinel surviving.
* **Not verified:** any Windows host (Job Object code, nested-job refusal, kill-on-close, `ActiveProcessLimit` accounting) — the Windows tests are written and skipped here; the official Microsoft and Python documentation pages could not be read (network policy denied `learn.microsoft.com`, `docs.python.org`), so Windows API facts in the code were from prior knowledge. (Superseded below by the coordinator-supplied notes.)
* **Still uncontained:** wall time (runner), POSIX tree-wide CPU/memory, resident memory (RLIMIT_AS is address space), descendants that leave the process group, ffprobe/readback commands, cgroups/namespaces/seccomp/network/filesystem isolation. macOS untested.
* The one real contained run used generous limits; it shows the mechanism works with the real ffmpeg, not that ffmpeg approached a limit. Colour-conversion drift (baseline) is unchanged.

### Addendum to Stage C (eighth commit): coordinator-supplied Microsoft notes

The coordinator supplied readbacks of the official Microsoft pages (fetched outside this host on 2026-11-04 UTC; cited in `CONTAINMENT.md` as coordinator-supplied notes, not host-fetched, and not proof of Windows execution). They agree with the design (descendants inherit the job, nested jobs from Windows 8, new job empty and without UI restrictions, struct field order and class 9,
user-mode periodic CPU accounting in 100 ns units, committed-memory limits, kill-on-close after the last handle). They confirm four of the six flag constants; `PROCESS_TIME` 0x2 and `PROCESS_MEMORY` 0x100 remain unconfirmed and need the reviewer's check.
**Correction:** the earlier claim that read-back would catch a wrongly numbered flag constant was wrong; read-back catches an OS that ignores/clamps/rejects a request, not a mis-numbered constant. Windows CPU limits are user-mode only and periodic, memory limits are committed (not resident) memory.
The single real FFmpeg contained run used a 3 GiB cap, not the later ≤ 256 MiB guidance, which FFmpeg cannot fit on this host (≈ 600 MB address space measured); it was not repeated and no cap was raised. Windows tests remain unrun by the author.

## Addendum: real contained FFmpeg run used wider caps than the later probe guidance

The single real contained encode (`evidence/containment-envelope/`) used `memory_bytes=3221225472`, `cpu_seconds=60`, `max_file_bytes=67108864`, i.e. wider than the later ≤ 256 MiB / ≤ 10 s guidance for resource probes. It was not repeated with tighter caps. See `evidence/containment-envelope/README.md`, Addendum 2. Windows containment remains untested by the author; the requester's Windows results are theirs.
