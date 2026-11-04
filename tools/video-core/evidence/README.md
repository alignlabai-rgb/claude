# Render evidence (author's own run, 2026-11-04)

Environment: Claude Code cloud session, Linux x86_64, Python 3.13.18, FFmpeg/ffprobe `6.1.1-3ubuntu5`
(`/usr/bin/ffmpeg`, `/usr/bin/ffprobe`), libx264 present. No network, download, provider or credential was used.
The MP4 itself is **not committed**; only receipts, plan, ffprobe JSON and hashes are.

## The one render of the supplied case

```bash
python3 -m video_core render examples/episode.synthetic.json --output-root <scratch>/evidence-out --output-name synthetic-6s --render --max-runtime-seconds 300
```

Started `2026-11-04T20:08:42Z`, exit code `0`, one invocation, first attempt (no earlier real render of this case).

| item | value |
|---|---|
| status | `READBACK_MATCH` (metadata + decoded-frame readback only; `quality_evaluated: false`) |
| plan sha256 | `4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21` (= sha256 of `plan.canonical.json`) |
| episode sha256 | `401ac89079cc60d4f7a158eeba9b6c1e57edb664a3322c8d00c2de4ac6a01440` |
| output.mp4 | 7665 bytes, sha256 `25d80ca487fa8423ab32a858e16a053fa3fbb551fcf7e5366418044c026c5ecf` (not committed) |
| captions.srt | 89 bytes, byte-identical to `examples/captions.synthetic.srt` |
| executions | version probes (exit 0), `-encoders`/`-filters` probes (exit 0), `render` (exit 0, no signal, no timeout, stderr empty, 0.906 s), `inspect` (ffprobe exit 0) — full argv in `receipt.synthetic-6s.json` |

Files: `receipt.synthetic-6s.json`, `plan.canonical.json`, `ffprobe.synthetic-6s.json`, `inspect.synthetic-6s.json` (separate `video_core inspect` run, `READBACK_MATCH`).

## Independent inspection (`independent_check.py`, does not import `video_core`)

Own ffprobe/ffmpeg calls on the output: 640×360, `r_frame_rate` = `avg_frame_rate` = `30/1`, 180 decoded frames (`nb_read_frames`),
1 stream, duration 6.000000 s, 180 per-frame PTS values with a constant step of exactly one frame (512 ticks of 1/15360 s), first PTS 0.
Corner-pixel colour of frames 0, 89 (`#30506a`) and 90, 179 (`#6a5030`) decoded within the ±6 tolerance used (observed 44,78,102 and 103,76,44;
nominal 48,80,106 and 106,80,48 — a systematic few-level shift described in `LIMITATIONS.md`). Result: `independent_check.json`, `all_ok: true`.

**Disclosure:** the first run of this script (`independent_check.run1-parser-bug.json`, `all_ok: false`, "frame pts count 179 / first pts 512") was wrong:
my script discarded the csv row `0,`. I confirmed against raw `ffprobe` output (180 rows, first pts 0, `start_time=0`), fixed the parser, and re-ran the measurement
on the same file. The render was not repeated.

## Other runs (different synthetic cases, not the supplied case)

* Default suite on Python 3.11.18 / 3.12.3 / 3.13.18: 152 tests, OK, 2 opt-in skipped — `default-suite.log`.
* Opt-in real-FFmpeg tests, run once: file sources (generated mp4 + png + colour) at 30000/1001, 90 frames → `READBACK_MATCH`; drawtext burn-in with a local IPA Gothic font → encoded and read back — `integration-tests.log`. Burn-in appearance was not visually reviewed.
* Development-only probes: one 100-frame `-f null` decode of the generated filtergraph (syntax check) and a four-variant single-frame colour-conversion experiment to `rawvideo`. No MP4 was produced by them.

## Not shown here
Audio, visual or creative quality; macOS/Windows; any SYS015/Motion integration.
