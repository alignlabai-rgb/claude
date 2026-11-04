# Fixtures

All fixtures are synthetic and generic; nothing is downloaded and no real media is committed.

| fixture | purpose |
|---|---|
| `examples/episode.synthetic.json` | The supplied 6 s, 640×360, 30/1 fps, 180-frame two-colour episode with two Japanese captions. Plan/validate/SRT tests and the evidence render use it unchanged. |
| `examples/captions.synthetic.srt` | The supplied SRT; `export_srt` must reproduce it byte for byte (`tests/test_srt.py`). |
| `tests/helpers.py` | `episode_doc()`/`color_clips()` build variants (30000/1001, 60/2, oversized, etc.); `make_fake_tools()` writes **fake** `ffmpeg`/`ffprobe` Python scripts that imitate exit codes, signals, sleeps, noisy stderr, surviving children and tampering. They never produce video and are never render evidence. |
| `tests/fontbuilder.py` | Builds tiny synthetic sfnt blobs containing only a `cmap` (formats 4 and 12) for glyph-coverage tests. Not usable fonts. |
| `tests/test_integration_ffmpeg.py` | Generates throw-away media at test time with ffmpeg's `lavfi` sources (`testsrc2`, `color`) inside a temp dir. Optional burn-in uses a font you name via `VIDEO_CORE_TEST_FONT` (copied to the temp dir; never committed). |

To reproduce the evidence render (needs `ffmpeg` + `ffprobe`):

```bash
mkdir -p /tmp/vc-out
python3 -m video_core render examples/episode.synthetic.json --output-root /tmp/vc-out --output-name synthetic-6s --render
python3 evidence/independent_check.py /tmp/vc-out/synthetic-6s/output.mp4 --ffprobe "$(command -v ffprobe)" --ffmpeg "$(command -v ffmpeg)" \
  --width 640 --height 360 --fps 30/1 --frames 180 --sample 0:#30506a --sample 89:#30506a --sample 90:#6a5030 --sample 179:#6a5030
```

`evidence/independent_check.py` does not import `video_core`; it runs its own ffprobe/ffmpeg commands.
