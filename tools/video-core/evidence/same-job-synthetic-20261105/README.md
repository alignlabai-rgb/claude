# Same-job synthetic chain (one small run) and the caller candidate `55ce8bc`

Purpose: close the last shortfall of the producer-shape audit — a **single job** whose audio plan, stem mix, video render and MOV mux share one geometry — using only the existing library CLIs and the already-installed
`ffmpeg` / `ffprobe` (nothing downloaded or installed), then run the unchanged caller candidate on the real receipts. **Synthetic test data only**: two solid-colour blocks (`#30506a`, `#6a5030`, 160x90, 30/1 fps, 30 frames = 1 s) and an
integer 480 Hz square-wave tone (48 000 samples mono 48 kHz s16 = 1 s). No real IP, person, speech or user data.

`job / attempt / selection / binding / decision` refs and the owner snapshot are **SYNTHETIC TEST ASSERTIONS**: they mean no human adoption, voice qualification, authenticity or execution permission, and `READY` below is scoped to them.
`binding_ref` is the audio manifest's own `provenance_ref` (the manifest accepts it, unlike the earlier Stage-B demo manifest, which had none). Timing qualification (GAP-06) and creative approval are not claimed.

## What ran (once; `generate_chain.py`; versions in `run/versions.txt`: ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers; ffprobe version 6.1.1-3ubuntu5 Copyright (c) 2007-2023 the FFmpeg developers; Python 3.13.18)

All 17 steps exited 0 with empty stderr (`run/commands/NN-*.{cmd.json,stdout,stderr}` hold argv, exit code, stdout, stderr and their hashes; the scratch path is shown as `<WORK>`).

| step | command | exit | wall s | stdout / stderr bytes |
|---|---|---|---|---|
| 01-audiomix-validate | `/usr/local/bin/python3 -m video_core.audiomix validate <WORK>/audio/manifest.json…` | 0 | 0.13 | 301 / 0 |
| 02-audiomix-plan | `/usr/local/bin/python3 -m video_core.audiomix plan <WORK>/audio/manifest.json…` | 0 | 0.083 | 3832 / 0 |
| 03-audiomix-mix | `/usr/local/bin/python3 -m video_core.audiomix mix <WORK>/audio/manifest.json…` | 0 | 0.111 | 3465 / 0 |
| 04-video-validate | `/usr/local/bin/python3 -m video_core validate <WORK>/video/episode.json…` | 0 | 0.075 | 232 / 0 |
| 05-video-plan | `/usr/local/bin/python3 -m video_core plan <WORK>/video/episode.json…` | 0 | 0.07 | 4800 / 0 |
| 06-video-render | `/usr/local/bin/python3 -m video_core render <WORK>/video/episode.json…` | 0 | 0.56 | 13953 / 0 |
| 07-avmux-validate | `/usr/local/bin/python3 -m video_core.avmux validate <WORK>/mux.manifest.json…` | 0 | 0.073 | 322 / 0 |
| 08-avmux-plan | `/usr/local/bin/python3 -m video_core.avmux plan <WORK>/mux.manifest.json…` | 0 | 0.125 | 5658 / 0 |
| 09-avmux-mux | `/usr/local/bin/python3 -m video_core.avmux mux <WORK>/mux.manifest.json…` | 0 | 1.015 | 30520 / 0 |
| 10-ffprobe-tone_input.wav | `/usr/bin/ffprobe -v error -hide_banner -show_format…` | 0 | 0.042 | 1652 / 0 |
| 11-decode-tone_input.wav | `/usr/bin/ffmpeg -v error -nostdin -i…` | 0 | 0.05 | 0 / 0 |
| 12-ffprobe-stem.wav | `/usr/bin/ffprobe -v error -hide_banner -show_format…` | 0 | 0.046 | 1647 / 0 |
| 13-decode-stem.wav | `/usr/bin/ffmpeg -v error -nostdin -i…` | 0 | 0.049 | 0 / 0 |
| 14-ffprobe-output.mp4 | `/usr/bin/ffprobe -v error -hide_banner -show_format…` | 0 | 0.043 | 2786 / 0 |
| 15-decode-output.mp4 | `/usr/bin/ffmpeg -v error -nostdin -i…` | 0 | 0.052 | 0 / 0 |
| 16-ffprobe-output.mov | `/usr/bin/ffprobe -v error -hide_banner -show_format…` | 0 | 0.045 | 4251 / 0 |
| 17-decode-output.mov | `/usr/bin/ffmpeg -v error -nostdin -i…` | 0 | 0.06 | 0 / 0 |

Producer statuses (untouched): audio `MIXED_VERIFIED`, video `READBACK_MATCH`, mux `MUXED_VERIFIED_SAMPLE_EXACT` (`technical_verdict.sample_exact: True`); `quality_evaluated: false` in all three. Receipts, plans and ffprobe outputs are byte copies in `run/receipts/`.

## Final files (exact bytes; hashlib and `sha256sum` agree; `run/hashes.json`)

| file | bytes | SHA-256 |
|---|---|---|
| `output.mov` | 99212 | `af33577abe977feed6ac26412939f42fec8efa5e4d269bfad65335d00883ef34` |
| `output.mp4` | 2441 | `b72a847997937bc7f8f3d81f6c57dd976b3f9f13d8dfc600e41513dc4a61bce2` |
| `stem.wav` | 96044 | `95c3cb8e5ded7def13238652feeb9945435f6122614f08910d97ec4d6c68241c` |
| `tone_input.wav` | 96044 | `95c3cb8e5ded7def13238652feeb9945435f6122614f08910d97ec4d6c68241c` |

`run/media-facts/*.ffprobe.json`: `output.mp4` h264 160x90, 30 frames, 1.000000 s; `stem.wav` pcm_s16le 48 000 Hz mono 1.000000 s; `output.mov` h264 + pcm_s16le, 30 frames / 48 000 audio frames, 1.000000 s.
`run/media-facts/*.decode.txt`: full decode to null, exit 0, no stderr, for all four files. (`stem.wav` equals `tone_input.wav` byte for byte: one event at unity gain over the whole timeline.)
`transport/` holds the originals as create-only ASCII base64 (`output.mp4` raw; the PCM files gzip -9 -n then base64) with `manifest.json` (bytes, SHA-256, encoded SHA-256): decode with `base64 -d X.b64.txt [| gunzip] > X` then `sha256sum X`. Nothing is regenerated.

## Same-job pins (`comparison.json`, computed from the recorded files; all rows must be equal)

| tied value | sources | result |
|---|---|---|
| audio plan hash | 4 | equal |
| audio manifest hash | 2 | equal |
| stem bytes | 5 | equal |
| audio receipt bytes | 2 | equal |
| video bytes | 5 | equal |
| video plan hash | 3 | equal |
| frames | 7 | equal |
| samples | 5 | equal |
| geometry | 4 | equal |
| duration (s) | 4 | equal |
| selection ref | 2 | equal |

## Caller candidate (unchanged; profile not relaxed)

Inputs: the audio plan CLI stdout (real, unmodified), the audio and video receipts (real), the avmux-manifest-derived view (mechanical), real recorded exit codes; owner pins = file-byte SHA-256 values (stem / video from `sha256sum`-checked file bytes, manifest / plan / receipt from the recorded producer files), not copies of a receipt claim.

* **Positive:** `READY` — comparisons AUDIO_PLAN, AUDIO_RECEIPT, BOUNDS, EFFECTIVE_SCOPE, EXPECTED, IDENTITY, MANIFEST_VIEW, OBSERVED, PINS, PROFILE, SELECTION, VIDEO_RECEIPT all PASS; pins compared with {'manifest_sha256': 1, 'plan_sha256': 3, 'receipt_sha256': 1, 'stem_sha256': 2, 'video_sha256': 2} observed sources; selected scope `{'omit_no_contribution': [], 'qualified_silence': [], 'selected_unresolved': [], 'use': ['ev_tone']}`; no reasons. Scope: READY applies only to the synthetic snapshot values supplied to this call under the supplied UNREVIEWED profile.
* **One pin replaced** (owner stem pin := the video file's SHA-256, a real but wrong artefact of the same job): `REJECTED`, reasons ['PIN_MISMATCH', 'PIN_MISMATCH'] at `audio_receipt.outputs[stem]` and `avmux_manifest_view.audio_sha256`.
* The candidate has **no avmux-receipt stage**: the mux tie (manifest ↔ mux receipt inputs ↔ output facts) is in the pin table and the mux receipt's own 23 checks (all ok; 15 executions, all exit 0, none timed out), not in the candidate's verdict.

## Reproduce / verify

```bash
cd tools/video-core/evidence/same-job-synthetic-20261105
python3 generate_chain.py --out run2 --work /tmp/vc-same-job-2     # a NEW run (the recorded run/ is the original; x264 output is not claimed byte-reproducible elsewhere)
python3 compare_candidate.py --out /tmp/comparison.json              # recomputes the table and candidate verdicts from run/
python3 -m unittest discover -s . -p "test_*.py"                      # 7 short tests, evidence-only
```

Limits: one chain, solid colours and one tone (not a content, quality, timing or creative claim); Linux x86_64 only; ffmpeg 6.1.1 as installed; READY rests on synthetic assertions and on a candidate that is still an UNREVIEWED proposal; this is not an adoption, authentication or execution-permission result.
