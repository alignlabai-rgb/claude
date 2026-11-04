# video_core — usage

Offline Python 3.11+ standard-library implementation of the contract in `SPEC.md`:
episode JSON → validated integer-frame timeline → canonical plan + structured FFmpeg argv →
optional local render → receipt with an independent ffprobe readback.
No network, provider API, credential, shell, GUI, GPU or background process is involved.

```bash
cd tools/video-core            # or: PYTHONPATH=tools/video-core python3 -m video_core ...
python3 -m video_core examples/episode.synthetic.json          # default action = plan (no side effects)
python3 -m video_core validate examples/episode.synthetic.json
python3 -m video_core srt examples/episode.synthetic.json > captions.srt   # byte-identical to examples/captions.synthetic.srt
```

## Commands

| command | effect |
|---|---|
| `plan EPISODE` (default) | Validates and prints the canonical plan + `plan_sha256`. Reads the episode, assets and font; creates nothing; launches nothing. Optional `--asset-root`, `--output-root/--output-name` (validated, not created), `--burn-in-font REL`. |
| `validate EPISODE` | Structure, timeline, limits; with `--asset-root` also resolves file sources. |
| `srt EPISODE` | Deterministic UTF-8 SRT to stdout (bytes, LF, no BOM; cue end = exclusive `end_frame`). |
| `render EPISODE --output-root ROOT [--output-name NAME] --render` | Renders into the **fresh** directory `ROOT/NAME` (default name `render-<plan hash prefix>`). Without `--render` it is a dry run. |
| `inspect MEDIA [--episode EPISODE]` | Read-only ffprobe readback of an existing local mp4/mov/mkv/webm; optional comparison with the episode's width/height/fps/frames. |

Tools: `--ffmpeg PATH` / `--ffprobe PATH`, otherwise PATH lookup (reported as `discovery` in the receipt).
Limits (lower the defaults, never exceed the v1 schema ceilings): `--max-frames --max-dimension --max-clips --max-captions
--max-pixel-volume --max-duration-seconds --max-fps --max-source-bytes --max-argv-bytes --max-windows-cmdline-chars --max-runtime-seconds
--max-output-bytes --max-diag-bytes`. Defaults are in `video_core/limits.py`.

Exit codes: `0` ok · `1` rejected before launch · `2` usage · `3` skipped (tool unavailable) ·
`4` ran but not `READBACK_MATCH` · `130` interrupted.

Python API: `video_core.prepare(bytes, ...) -> Plan`, `video_core.render.render(bytes, ...) -> receipt dict`
(also `video_core.render_episode`), `video_core.export_srt(episode)`, `video_core.readback.compare(...)`.

## What is rejected, and when

Everything below is rejected in `validate`/`plan`/`render` **before any file is created or process launched**
(issue codes are machine-readable): zero/negative duration, overlap, gaps/missing coverage, clips out of order,
duplicate ids (within clips, within captions), invalid colours, caption outside `[0, total_frames]`,
non-integer/bool/overflowing numbers, unknown fields, duplicate JSON keys, NaN, BOM, invalid UTF-8,
resource excess (frames, dimensions, pixel volume, duration, fps, clip/caption counts, input/source/font bytes,
argv size), absolute / `..` / backslash / drive / UNC / protocol-like (`http:`, `concat:`, `pipe:`) /
option-like (`-x`) / hidden / glob-or-pattern (`%03d`, `*`) / control-character file names, any symlink on
the path (not only escapes), FIFOs/devices/directories, extensions outside an allow-list (so playlist/concat
containers cannot be smuggled in), output names that are unsafe, **existing** output locations (no overwrite),
and output locations inside the asset root.

## FFmpeg capability probe (fail-closed)

Before anything is created, `render` runs `ffmpeg -encoders/-filters/-demuxers` (only for what the plan needs) and parses the
listings with the pure parsers in `video_core/capabilities.py`. Accepted entry layouts: filters with a 2- or 3-character flag
column (`..C scale V->V` in FFmpeg 6.1.1, `.. scale V->V` as reported for 8.1.2), encoders with a 6-character flag column
(`V....D libx264`), demuxers (` D  image2`, ` DE mov,mp4,...`); LF or CRLF. Names match exactly (`scale` ≠ `scale2ref`).
Outcomes in the `SKIPPED_TOOL_UNAVAILABLE` receipt (`reasons[0].detail`): `missing` = understood listing, name truly absent;
`probe_failed` = listing failed, was truncated, or exited 0 but **no line matched a known layout**
(`details[flag].reason`, `ignored_lines`, up to 5 truncated `sample_ignored` lines) — never treated as success and never reported as
"everything is missing".

## Argument-list bounds

`max_argv_bytes` (default 100000, UTF-8 bytes) applies on every platform. `max_windows_cmdline_chars` (default 32000, ceiling 32766, the Win32
`CreateProcess` limit; the flag name is kept for compatibility but the **unit is UTF-16 code units**) is measured on the string
`subprocess.list2cmdline` builds — the one Python passes on Windows, executable included — counting BMP characters as 1 and non-BMP
characters (e.g. emoji) as 2. It is **enforced when running on Windows**, together with a ban on ill-formed text (an unpaired surrogate,
`argv_ill_formed_utf16`); on other platforms `plan` only reports them (`observations.argv_budget.{estimated_windows_cmdline_utf16_units,
fits_windows_limit, ill_formed_utf16}`; an estimate using the real roots when known, else 260 characters each) and `render` re-checks the
real argv before creating anything. Measuring never raises: a lone surrogate counts as one UTF-16 unit and three UTF-8 bytes.
Rule of thumb from measurement: about 200 colour-only clips fit the Windows bound; 256 do not.

## Render pipeline and receipt

`preflight → tool probe → create fresh ROOT/NAME → input hash → render → inspect → output hash → input re-hash → receipt`.

Files in `ROOT/NAME`: `output.mp4` (or `output.mp4.partial` if the run did not end cleanly), `captions.srt`,
`plan.canonical.json` (its SHA-256 **is** `plan_sha256`), `ffprobe.json`, `receipt.json`. `receipt.json` is created first with
`status: IN_PROGRESS` and replaced atomically at the end; a receipt still saying `IN_PROGRESS` means the run did not
finish and must be treated as UNKNOWN. The only temporary directory is `ROOT/NAME/tmp` (caption text files, optional
font copy, `HOME` for FFmpeg); it is removed at the end.

Receipt fields: `status`, `stage`, tool `version_line` + executable SHA-256 + discovery, `input` (episode / asset / font
hashes before and after, `inputs_unchanged`), `plan_sha256`, every `executions[]` entry (phase, actual argv, outcome,
`exit_code`, `signal` (POSIX only; on Windows a runner-killed child reports exit code 1 and no signal, so the outcome comes from `kill_reason`, never from the code), `timed_out`, `kill_reason`, `drain_complete`, `orphan_group_killed`, wall seconds, stderr head+tail,
total bytes, truncation flag and SHA-256 of the full stream), `outputs[]` (bytes, SHA-256, `partial`), `readback`
(summary of ffprobe + every expected/observed check), `quality_evaluated: false`.

Statuses: `READBACK_MATCH` (exit 0 + decoded frame count, size, rate, duration, codec, pix_fmt, stream layout all equal the plan) ·
`READBACK_MISMATCH` · `INSPECTION_FAILED` · `PROCESS_FAILED` (non-zero exit) · `TIMEOUT` · `SIGNALED` · `FLOOD_KILLED` ·
`INTERRUPTED` · `LAUNCH_FAILED` · `UNKNOWN` (exit 0 but no output / surviving child process / input changed during the run) ·
`SKIPPED_TOOL_UNAVAILABLE` (ffmpeg/ffprobe missing, not runnable, or lacking libx264/required filters/demuxers; nothing is created).
**None of these is a quality or acceptance verdict**: `READBACK_MATCH` is metadata plus a decoded-frame count.
Nothing is retried; partial files and diagnostics are kept.

## Captions

SRT is the canonical caption output. No escaping is applied (SRT has none): `:`, quotes, backslashes, `%`, `-->`, Japanese
text and `\n` are written verbatim. Optional burn-in (`--burn-in-font REL`, a `.ttf/.otf` under the asset root) uses
`drawtext` with `expansion=none`; caption text goes through UTF-8 text files in the owned `tmp/` directory and **never**
into argv or the filtergraph, so filtergraph escaping and shell escaping do not exist as problems. Frame gating is exact:
`between(n,start_frame,end_frame-1)`. Before planning, a stdlib cmap reader refuses fonts that lack any code point
(`font_missing_glyphs`); no font is downloaded or guessed.

## Audio

Out of scope. The output is video-only (`-an`). `video_core/audio.py` is a typing-only interface for a future optional local
audio adapter (no voice service, no provider, no network).

## Tests

```bash
cd tools/video-core
python3 -m unittest discover -s tests -t .                  # see evidence/stage-a-windows-residuals/README.md for current counts; fake ffmpeg/ffprobe; no real render
VIDEO_CORE_INTEGRATION=1 python3 -m unittest tests.test_integration_ffmpeg -v   # opt-in: real ffmpeg on generated synthetic media
VIDEO_CORE_INTEGRATION=1 VIDEO_CORE_TEST_FONT=/path/font.ttf python3 -m unittest tests.test_integration_ffmpeg -v  # + burn-in
```
