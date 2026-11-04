# FFmpeg listing fixtures — provenance

* `*.ffmpeg-6.1.1.observed.txt` — **verbatim** lines captured on 2026-11-04 from
  `ffmpeg -hide_banner -filters|-encoders|-demuxers` of FFmpeg `6.1.1-3ubuntu5` (Linux). The real header/legend lines are
  kept in full; the entry lines are a selected subset (including look-alike decoys such as `scale2ref`, `libx264rgb`, `image2pipe`).
* `muxers.ffmpeg-6.1.1.observed.txt`, `decoders.ffmpeg-6.1.1.observed.txt` — verbatim, captured the same way (added for `video_core.avmux`); muxer lines have two leading spaces then `E`, decoder lines use the same six-character flag layout as encoders.
* `filters.two-flag-layout.reconstructed.txt` — **reconstructed, not captured**. The three entry lines for `color`, `scale`,
  `drawtext` (single-spaced) are the ones quoted in the Windows retest finding for FFmpeg 8.1.2 (two flag characters). The other
  entries reuse FFmpeg 6.1.1 names/descriptions with the two-character flag column and both spacing styles, and the header
  lines are placeholders for noise-rejection tests. No FFmpeg 8.1.2 output was available in this Linux session, so the 8.1.2 encoder and
  demuxer layouts are **not** represented by a fixture.
* `filters.malformed.txt` — hand-written noise/malformed lines that must never be accepted as entries.
