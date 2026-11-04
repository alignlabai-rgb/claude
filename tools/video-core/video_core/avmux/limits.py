"""avmux resource limits.

The shared video `Limits` (finite, strictly typed, schema ceilings preserved) is reused as is: avmux needs max_frames, max_source_bytes,
max_output_bytes, max_runtime_seconds (the mux), probe_timeout_seconds, inspect_timeout_seconds (each ffprobe / full decode), max_diag_bytes,
max_drain_bytes, max_argv_bytes and max_windows_cmdline_chars. Nothing here relaxes or re-implements those rules.
"""

from ..limits import Limits  # noqa: F401

# Bytes of per-frame hash text kept from a decode (framemd5/framehash lines are ~80 bytes each; 18000 frames fit).
HASH_TEXT_CAP = 4 * 1024 * 1024
# Extra stdout drain allowance on top of the expected PCM size when decoding audio to raw s16le for counting/hashing.
PCM_DRAIN_SLACK = 4 * 1024 * 1024
