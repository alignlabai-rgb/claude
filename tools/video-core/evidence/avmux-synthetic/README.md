# AV mux evidence — one bounded real synthetic case (author's own run, Linux cloud, 2026-11-04; FFmpeg/ffprobe 6.1.1-3ubuntu5)

Generic synthetic inputs only; no product asset, no network, no download. **No media is committed**: the two outputs were delivered as response attachments.
This is distinct from the six-second baseline (not re-rendered) and does not re-mix the Stage B stem.

## Inputs

* **NEW video (generated exactly once):** 3 frames, 30/1 fps, 160x90, H.264 (`testsrc2` → `libx264 -pix_fmt yuv420p`), 5134 bytes, SHA-256 `9f361c19b5b1a6355ba4a5b4f886d06440111978bea6b01f760e13dc81ae5ad2` (command and result in `inputs.log`, made by `make_inputs.py`).
* **Existing Stage B stem, copied byte-for-byte (not remixed):** 4800 samples, mono, 48 kHz s16, 9644 bytes, SHA-256 `68f8180e0630509d8743c11c38de674481e3f711c54c12bffa996c0af1c8ba88`.
  30/1 fps × 3 frames = 4800 samples under the audio boundary policy, so the declared count matches.

## Commands actually run (once each)

```bash
python3 evidence/avmux-synthetic/make_inputs.py <scratch>/avmux-demo <stage-B stem.wav> --ffmpeg "$(command -v ffmpeg)"      # 21:25:35Z: the single video generation + manifests
python3 -m video_core.avmux validate <scratch>/avmux-demo/{mov,mp4}.manifest.json                                           # ok (both)
python3 -m video_core.avmux plan     <scratch>/avmux-demo/{mov,mp4}.manifest.json --asset-root <scratch>/avmux-demo/assets  # no subprocess, no files; readiness UNVERIFIED, video CALLER_STATED_UNVERIFIED
python3 -m video_core.avmux mux <scratch>/avmux-demo/mov.manifest.json --asset-root <scratch>/avmux-demo/assets --output-root <scratch>/avmux-demo/out --output-name mov-demo --mux   # 21:25:43Z exit 0
python3 -m video_core.avmux mux <scratch>/avmux-demo/mp4.manifest.json --asset-root <scratch>/avmux-demo/assets --output-root <scratch>/avmux-demo/out --output-name mp4-demo --mux   # 21:25:44Z exit 0
python3 -I evidence/avmux-synthetic/independent_check.py --ffmpeg … --ffprobe … --video clip.mp4 --wav stem.wav --mov out/mov-demo/output.mov --mp4 out/mp4-demo/output.mp4    # exit 0
```

Development-only, before these: argv-syntax probes of both mux argument lists to `-f null` (and the per-frame hash / audio-decode / ffprobe command shapes on the existing baseline MP4, read-only). No media produced.
That probing also revealed one real bug, fixed before the evidence run: ffprobe omits `start_pts/start_time` for a plain WAV, so the source-WAV start check accepts "not reported" and rejects only an explicit non-zero start.

## Route 1 — MOV + `pcm_s16le` (sample-exact contract) — `MUXED_VERIFIED_SAMPLE_EXACT`

`output.mov` 15186 bytes, SHA-256 `3d7b56c90c8c3c904192cbea039f6ad50bac1c9f380d10d086b54ee5e86478ae`; plan `c1b16ee3…c42c`. Receipt: 23 checks all ok; 15 executions all exit 0, no signal/timeout, drain complete; inputs re-hashed unchanged.
* Preflight (bounded ffprobe, before the directory existed): one H.264 stream, 160x90, `30/1`, 3 decoded frames, start 0; WAV: `pcm_s16le`, 48000 Hz, mono, 4800 samples.
* Readback: decoded video frame hashes and copied packet hashes equal the source's (3 each); audio stream start pts 0, `duration_ts` 4800 at 1/48000; **decoded audio = 9600 bytes with SHA-256 `247e9f8e…f794` = the WAV `data` chunk's SHA-256.**
* Independent check (own commands, no `video_core`): the same facts, and `pcm == wav_pcm` sample for sample.

## Route 2 — MP4 + built-in AAC (lossy; NOT sample-exact) — `MUXED_TECHNICAL_WITHIN_TOLERANCE`

`output.mp4` 7782 bytes, SHA-256 `543a712284260b5957eab1079e757fce602e221f4b435a5c7ffc2b2c72b36b90`; plan `659e920f…dc55`. Receipt: 21 checks all ok; `technical_verdict.sample_exact: false`; tolerance 1024 samples (chosen: one AAC frame).
Video preservation is the same as route 1 (decoded frame hashes and copied packet hashes equal, 3 each). **Observed AAC timing/padding (not hidden, not judged):**

| observation | value |
|---|---|
| audio stream start | `start_pts 0`, `start_time 0.000000`, time base 1/48000 (container edit list compensates the encoder delay) |
| container duration | `duration_ts 4800` samples (0.100000 s); audio minus video duration 0 |
| packets | 6 (`nb_frames 6`): pts −1024, 0, 1024, 2048, 3072, 4096; durations 1024 ×5 and 704 for the last |
| packet side data | first packet: `Skip Samples` `skip_samples 1024`, `discard_padding 0`; **no side data on the last packet** |
| **decoded samples** | **5120** = 6 × 1024 − 1024 skipped; expected 4800 → **delta +320** (the unannounced end padding is not trimmed by the decoder); `exact_sample_count: false` |
| within tolerance | yes (|320| ≤ 1024); `sample_exact_claimed: false`; `perceptual_sync_evaluated: false` |
| decoded PCM | differs from the WAV from the first byte, as expected for a lossy codec; reported as information only |

Receipt field note (known imprecision): the receipt's `audio_packets.discard_padding` reports the first **non-zero** padding value (here `null`); ffprobe's first-packet side data did report `discard_padding: 0`, which is preserved raw in `independent_check.json` (`packets_pts_duration_side_data`). The per-packet raw values are therefore in that file, not in the receipt summary. Not changed because each route may be muxed only once and the evidence must match the committed code.

## Files

`{mov,mp4}.manifest.json`, `{mov,mp4}.plan.stdout.json` (the pure plan, `UNVERIFIED`), `{mov,mp4}.plan.canonical.json` (SHA-256 = `plan_sha256`), `{mov,mp4}.receipt.json`, `{mov,mp4}.ffprobe.output.json`, `independent_check.json`, `inputs.log`,
`make_inputs.py`, `independent_check.py` (stdlib; neither imports `video_core`), `default-suite.log`.

## Honest scope

A technical measurement of a 3-frame synthetic clip on Linux/FFmpeg 6.1.1. It says nothing about audio or visual quality, perceived A/V sync, any other FFmpeg version or platform (Windows and macOS untested here), or any real workflow. Colour-conversion drift (baseline) is unrelated and unchanged.
