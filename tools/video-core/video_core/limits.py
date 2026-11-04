"""Caller-visible resource limits. All are enforced before any process launch.

The v1 schema ceilings (18,000 frames, 7680 px per dimension, 256 clips,
512 captions) are absolute: callers may lower the defaults but not exceed them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from fractions import Fraction

SCHEMA_MAX_FRAMES = 18_000
SCHEMA_MAX_DIMENSION = 7_680
SCHEMA_MAX_CLIPS = 256
SCHEMA_MAX_CAPTIONS = 512
SCHEMA_MAX_CAPTION_CHARS = 512


@dataclass(frozen=True)
class Limits:
    max_frames: int = SCHEMA_MAX_FRAMES
    max_dimension: int = SCHEMA_MAX_DIMENSION
    max_clips: int = SCHEMA_MAX_CLIPS
    max_captions: int = SCHEMA_MAX_CAPTIONS
    max_caption_chars: int = SCHEMA_MAX_CAPTION_CHARS
    max_input_bytes: int = 4 * 1024 * 1024  # episode JSON
    max_pixel_volume: int = 1920 * 1080 * 18_000  # width * height * frames
    max_duration_seconds: int = 3_600
    max_fps: int = 120
    max_source_bytes: int = 4 * 1024**3
    max_font_bytes: int = 32 * 1024**2
    max_argv_bytes: int = 100_000  # Windows CreateProcess is ~32k; lower it there
    max_runtime_seconds: float = 600.0  # render wall clock
    probe_timeout_seconds: float = 30.0  # -version / -encoders / -filters
    inspect_timeout_seconds: float = 120.0  # ffprobe -count_frames
    max_diag_bytes: int = 16_384  # kept per stream (head + tail)
    max_drain_bytes: int = 64 * 1024**2  # child output beyond this => killed
    max_output_bytes: int = 2 * 1024**3  # passed to ffmpeg -fs

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(f"limit {f.name} must be a positive number")
        ceilings = {
            "max_frames": SCHEMA_MAX_FRAMES,
            "max_dimension": SCHEMA_MAX_DIMENSION,
            "max_clips": SCHEMA_MAX_CLIPS,
            "max_captions": SCHEMA_MAX_CAPTIONS,
            "max_caption_chars": SCHEMA_MAX_CAPTION_CHARS,
        }
        for name, ceiling in ceilings.items():
            if getattr(self, name) > ceiling:
                raise ValueError(f"limit {name} may not exceed the v1 schema ceiling {ceiling}")
        if self.max_fps > 60_000:
            raise ValueError("limit max_fps may not exceed the v1 schema ceiling 60000")

    def max_duration(self) -> Fraction:
        return Fraction(self.max_duration_seconds)

    def to_dict(self) -> dict:
        return asdict(self)

    def replace(self, **overrides) -> "Limits":
        data = asdict(self)
        data.update({k: v for k, v in overrides.items() if v is not None})
        return Limits(**data)
