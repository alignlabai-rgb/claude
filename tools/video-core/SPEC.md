# Implementation contract v1

Deliver a Python package `video_core` and command `python -m video_core` with `validate`, `plan`, `render`, and `inspect` subcommands. `plan` is the default action. Use structured argv without a shell. Separate input validation, frame arithmetic, source resolution, filter planning, subprocess execution and output inspection.

Episode v1 has fps numerator/denominator, even canvas dimensions, total_frames, sequential non-overlapping clips and timed captions. Treat the timeline as integer frames. Use Fraction or exact rational arithmetic; reject invalid/negative/overflowing ranges, duplicate IDs, missing coverage, overlaps and captions extending past the endpoint. Do not silently rescale or invent duration. Initial output ceiling: 18,000 frames and 7680 pixels per dimension; expose explicit caller limits and reject excess before process launch.

Color sources are fully synthetic. File sources must resolve to existing local files within a caller-selected asset root; reject absolute paths, traversal, symlink escape, remote URLs and protocol-like names. Treat filenames as data. Restrict output to a caller-selected output root; refuse overwrites by default and keep inputs unchanged. Validate dry-run paths without creating files or launching FFmpeg.

Captions preserve exact UTF-8 text and frame intervals. Export deterministic SRT and allow a provided local font for burn-in. Do not download fonts or silently claim a missing glyph is rendered. Keep subtitle escaping independent of shell escaping. Embedded speech/audio generation and provider integration are outside scope. Design a separate optional local audio adapter; do not invent a voice service or require it for the color baseline.

Render owns only its fresh output and bounded temporary directory. Bound subprocess runtime and captured diagnostic bytes, record phase/exit/signal/timeout/drain separately, and retain unknown or partial results without automatic retry. Produce a receipt with input hash, canonical plan hash, tool versions, output hashes and ffprobe results. A planned file or successful process return alone is not a render-quality proof. Record unavailable render tools as SKIPPED_TOOL_UNAVAILABLE.

Mac, Linux and Windows paths should be supported at the interface level. Only claim a platform after actual tests there. Keep CPU-only defaults. No GPU runtime, GUI, cloud credentials, network request, deployment, repo sharing or existing system modification is required.

Deliver source, tests, fixture instructions and a concise limitation report. Do not commit real media, personal paths, credentials, environment dumps or unrelated code. Keep all changes within this new repository and return a review branch; no automatic merge or production deployment.
