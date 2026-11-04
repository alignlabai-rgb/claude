# AV mux — generic spec v1 (`video_core.avmux`)

Optional, offline, stdlib-only muxing of **one explicitly selected local H.264 video** and **one qualified 48 kHz signed 16-bit PCM WAV stem** into a
fresh output directory, using the system FFmpeg/ffprobe through the bounded runner. It is a separate module and CLI: the video commands stay video-only
(`-an`), the audio planner/mixer stays independently usable, and no default, plan or hash of either changes (the baseline plan hash `4bc20b3f…9f21` is pinned by a test).

Generic only: no product, app, board, input-selection authority, provider runner, API, download, network, credential or workflow is involved or implied.
The caller decides *what* to mux and owns every creative and binding decision; this component checks technical facts and says plainly what it did not check.

**Not done, on purpose:** no `-shortest`, `-t`, `-ss`, no trimming/padding/stretching/resampling, no frame dropping or rescaling, no `-r/-vsync/-fps_mode`,
no loudness/normalization, no inference of voice, timing, silence or creative adoption, no source modification, no retry, no claim of perceptual sync or quality.

## Manifest (`schemas/avmux-manifest.schema.json`, `avmux_version` 1)

| field | meaning |
|---|---|
| `route` | `MOV_PCM_S16LE` (sample-exact editing intermediate) or `MP4_AAC` (lossy review/delivery candidate) |
| `selection.ref` | opaque caller reference for the explicit selection (a caller assertion; not authenticated; not evidence of adoption) |
| `video` | `state` (**only `USE`**; `MISSING`/`UNKNOWN`/`PROXY`/`HOLD` are rejected), `path` (relative `.mp4/.m4v/.mov`), `sha256`, `declared{codec:"h264", width, height, fps{n,d}, total_frames}` |
| `audio` | `state` (only `USE`), `path` (relative `.wav`), `sha256`, `declared{sample_rate:48000, channels:1\|2, sample_count}`, optional `receipt{path,sha256}` |

`audio.receipt` is an optional audio-planner receipt that *qualifies* the stem: it must be `MIXED_VERIFIED` (HOLD, clamped, rejected, timed-out or unknown stems are refused),
describe a stem with exactly this SHA-256, and report the declared sample count. Paths follow the shared rules (relative, `/`, no `..`/`:`/`\`/leading `-`/`.`, regular files only,
symlinks **and Windows reparse points/junctions** rejected); one `--asset-root` holds both inputs. Integers are JSON integers; strict JSON (no duplicate keys, NaN, BOM).

## What is checked, where, and what it proves

| stage | runs | checks |
|---|---|---|
| `validate` | nothing but the manifest | schema, states, enums, paths |
| `plan` (default) | **no subprocess, no file created.** Reads and hashes both files, parses the WAV with the strict stdlib parser | declared audio sample count == `floor((2·frames·fps_d·48000 + fps_n)/(2·fps_n))` (audiomix boundary policy) **and** == the WAV's real sample count; channels; both SHA-256; optional audio receipt; argv budget. **Video codec/size/rate/frames/start are only caller-stated** and labelled `CALLER_STATED_UNVERIFIED`; `readiness` says `UNVERIFIED…` — a plan never reports verified readiness. |
| `mux --mux` preflight | version + capability probes (`-encoders/-decoders/-muxers/-demuxers`), then bounded `ffprobe` of the real video and WAV | missing/unrunnable tool or capability ⇒ `SKIPPED_TOOL_UNAVAILABLE` (nothing created). Measured video must be exactly **one** H.264 stream, declared width/height, `r_frame_rate = avg_frame_rate =` declared rational fps, decoded frame count = declared total, start 0. ffprobe's view of the WAV must agree with the strict parse. Any mismatch ⇒ **rejection before a directory exists or the mux is launched**, listing every expected/measured pair. |
| mux | `-c:v copy` + the route's audio codec, bounded runtime / diagnostics / output bytes | one launch, no retry; partial/UNKNOWN preserved |
| readback | ffprobe of the output; decoded video frame hashes and copied video packet hashes of **source vs output**; decoded audio count and SHA-256; input re-hash | route contracts below |

Argument lists are bounded in UTF-8 bytes everywhere and in **UTF-16 code units** (non-BMP = 2) against the Windows command-line limit on Windows (reported elsewhere); the materialized argv is re-checked with the real roots before anything is created.

## Route contracts (kept separate; receipts say which one applied)

**`MOV_PCM_S16LE` — `MOV_PCM_S16LE:sample_exact_pcm`.** Audio is `pcm_s16le` copy-through into MOV. All must hold: output has exactly one video and one audio stream; video properties as above;
decoded video frame hashes equal the source's and the copied packet hashes equal the source's (frame count = declared); audio stream starts at pts 0 with `duration_ts × time_base = samples/48000`;
**decoding the audio to s16le gives exactly the WAV's PCM bytes (byte count and SHA-256 equal the WAV `data` chunk).** Status `MUXED_VERIFIED_SAMPLE_EXACT`.

**`MP4_AAC` — `MP4_AAC:lossy_within_tolerance`.** Audio is encoded with FFmpeg's built-in `aac` at 128 kb/s (inherent s16→fltp *format* conversion only; no rate change, no stretching). AAC is lossy and carries encoder delay and padding, so this route
**never claims sample-exact or bit-exact audio, nor perceptual sync or quality**. Declared, chosen (not standard-derived) tolerance: the decoded sample count must be within **1024 samples (one AAC frame)** of the declared count and the audio stream start within 1024 samples of 0;
video preservation checks are the same as MOV. Always reported, never hidden: decoded vs expected sample counts and the delta, `exact_sample_count`, stream `start_pts/start_time/time_base/duration_ts/nb_frames`, packet count with first/last packet timing,
and packet skip-samples / discard-padding side data **only if ffprobe exposes it** (absence is reported as absence, not as zero). Status `MUXED_TECHNICAL_WITHIN_TOLERANCE`; outside tolerance ⇒ `READBACK_MISMATCH`.

A route that cannot meet its declared contract returns a precise mismatch (never a pass): the file is quarantined as `output.<ext>.mismatch`, kept for inspection. Container choice is not interchangeable: MP4 cannot carry the MOV contract.

## Statuses and verdicts

`MUXED_VERIFIED_SAMPLE_EXACT`, `MUXED_TECHNICAL_WITHIN_TOLERANCE`, `READBACK_MISMATCH`, `INSPECTION_FAILED` (a readback step could not run), `PROCESS_FAILED`, `TIMEOUT`, `SIGNALED`, `FLOOD_KILLED`, `INTERRUPTED`, `LAUNCH_FAILED`,
`UNKNOWN` (exit 0 but no output / surviving child process / an input changed during the run), `SKIPPED_TOOL_UNAVAILABLE`. Process-level failures keep `output.<ext>.partial`.
The receipt's `technical_verdict` (`contract`, `met`, `sample_exact`, `tolerance`) is **separate from quality**: `quality_evaluated: false`, `creative_acceptance: "NOT_EVALUATED"`, `perceptual_sync_evaluated: false`. No status is a quality or acceptance verdict.

## Output directory and receipt

`ROOT/NAME` (fresh; existing locations and locations inside the asset root are refused) holds `output.mov|mp4`, `plan.canonical.json` (its SHA-256 is `plan_sha256`), `ffprobe.output.json` and `receipt.json` (created `IN_PROGRESS`, replaced atomically; a receipt still `IN_PROGRESS` means UNKNOWN).
The one temporary directory is `ROOT/NAME/tmp` (HOME for FFmpeg), removed at the end. The receipt records tool versions and executable hashes, every execution's actual argv/exit/signal/timeout/drain and truncated diagnostics (head+tail, total bytes, SHA-256 of the whole stream),
input hashes before and after, the plan hash, output hashes, the preflight source checks and every expected/observed readback check.

## CLI — `python -m video_core.avmux {validate,plan,mux}`

`plan` is the default; `mux` runs only with `--mux` and an explicit `--output-root`. Options: `--asset-root`, `--output-name` (default `mux-<plan hash prefix>`), `--ffmpeg`, `--ffprobe`, and finite, correctly typed limits
(`--max-frames --max-source-bytes --max-argv-bytes --max-windows-cmdline-chars --max-runtime-seconds --max-output-bytes --max-diag-bytes --max-drain-bytes --probe-timeout-seconds --inspect-timeout-seconds`; NaN/inf/bool/float-for-integer values exit 2 before anything runs).
Exit codes: `0` verified (either route's contract met) · `1` rejected · `2` usage · `3` skipped · `4` ran but not verified · `130` interrupted.

## Next integration seam

A caller that owns the selection produces a manifest (optionally with the audio planner's `MIXED_VERIFIED` receipt) and runs the explicit mux. Anything beyond this — choosing which video/stem, A/V offset policy, other codecs/containers, `-shortest`-style policies,
delivery packaging — needs its own separately validated stage and is not decided here.
