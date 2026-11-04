# Stage B evidence — synthetic mono stem (author's own run, Linux cloud, 2026-11-04)

A tiny generic fixture only: owned integer-synthesised tones, no company/product audio, no speech, no network, no mux.
The WAV is **not committed**; it was delivered as a response attachment. Everything here is text.

## What the stem is

3 frames at 30/1 fps = 4800 samples, mono, 48 kHz, signed 16-bit PCM, 9644 bytes,
SHA-256 `68f8180e0630509d8743c11c38de674481e3f711c54c12bffa996c0af1c8ba88`.
480 Hz square (amp 8000) on samples [0, 3200) plus 600 Hz triangle (amp 6000, gain 1/2) on [1600, 4800); they overlap in frame 1 (`policy.overlap: ALLOW_SUM`),
so the peak is 8000 + 3000 = 11000. `policy.gap: REQUIRE_FULL_COVERAGE` holds because the two events cover all 3 frames. A third event, `ev_unresolved_not_selected`
(`MISSING`, role `diegetic`), is outside the explicit scope: it appears in the report and does not block.

## Commands actually run (once each; one mix)

```bash
python3 evidence/stage-b-audio-stem/make_synthetic_assets.py <scratch>/audio-demo          # tone WAVs + manifest (assets not committed)
python3 -m video_core.audiomix validate <scratch>/audio-demo/manifest.json                  # ok, 4800 samples, no hold
python3 -m video_core.audiomix plan <scratch>/audio-demo/manifest.json --asset-root <scratch>/audio-demo/assets   # READY, plan sha256 a897549b…228e
python3 -m video_core.audiomix mix <scratch>/audio-demo/manifest.json --asset-root <scratch>/audio-demo/assets \
        --output-root <scratch>/audio-demo/out --output-name stem-demo --mix                # exit 0, MIXED_VERIFIED (started 2026-11-04T20:52:42Z)
python3 -I evidence/stage-b-audio-stem/verify_stem_independent.py <scratch>/audio-demo/out/stem-demo/stem.wav   # exit 0, all_ok
```

## Files

| file | content |
|---|---|
| `manifest.synthetic.json` | the manifest (pins both tone assets' SHA-256) |
| `plan.canonical.json` | canonical plan; its SHA-256 is `a897549b8c56c5b474952420059db3adfda8aaf458093ee49373fd82eb90228e` and `tests/test_audio_golden.py` checks the planner reproduces these exact bytes |
| `receipt.stem-demo.json` | mix receipt: manifest/asset hashes before and after (`inputs_unchanged: true`), plan hash, stem SHA-256, actual WAV properties re-read by the strict parser, peaks (11000), 0 clipped samples, `quality_evaluated: false` |
| `independent_verification.json` | `verify_stem_independent.py`: parses RIFF fields with `struct` + the stdlib `wave` module and re-derives all 4800 samples from the documented construction with its own integer code; 20 checks, 0 sample mismatches |
| `make_synthetic_assets.py`, `verify_stem_independent.py` | standalone, stdlib-only; neither imports `video_core` |
| `default-suite.log` | default suite on Python 3.11 / 3.12 / 3.13 |

## Honest scope

This verifies structure and exact sample values of a synthetic stem against its documented construction. It says nothing about audio or creative quality, nothing about Windows/macOS,
and no muxed video exists. The 8.1.2 Windows smoke and the baseline render were not repeated.
