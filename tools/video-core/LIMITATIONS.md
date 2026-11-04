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
