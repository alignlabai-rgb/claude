# Acceptance cases

1. The supplied two-color episode plans exactly 180 frames at 30/1 fps, 640x360, with the two supplied caption intervals. The plan is deterministic and creates no files or subprocesses.
2. At 30000/1001 fps, conversion and reported duration use exact rational values; tests must detect accumulated rounding drift and off-by-one caption endpoints.
3. Reject zero/negative duration, overlap, missing coverage, duplicate IDs, invalid colors, captions after total_frames and all configured resource excess before launching a process.
4. Reject absolute paths, ../ traversal, symlink escape, remote/protocol inputs, option-like filename injection and output overwrite. Verify fixtures cannot modify an input or an outside sentinel.
5. Preserve Japanese/ASCII text, line breaks and punctuation in exported UTF-8 SRT. Test colon, quote and backslash handling separately from shell safety. State font/render limitations.
6. When FFmpeg and ffprobe are present, perform one bounded synthetic 6-second render. Independently inspect output width/height, 30/1 fps and exactly 180 frames. Save versions, actual commands, exits and hashes. If unavailable, report a precise skip; do not fabricate an MP4 or a PASS.
7. Exercise timeout/nonzero exit/truncated diagnostics with an owned test subprocess. Preserve exact stage and unknown outcome; do not retry or kill unrelated processes.
8. Return the code and tests on a review branch. The requester performs independent acceptance; author self-check is not product acceptance or a claim of production integration.
