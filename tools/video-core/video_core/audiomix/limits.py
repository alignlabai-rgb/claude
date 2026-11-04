"""Resource limits for the audio planner/mixer. All are enforced before any file is created."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

SCHEMA_MAX_FRAMES = 18_000
SCHEMA_MAX_EVENTS = 1_024
SCHEMA_MAX_ASSETS = 256
SAMPLE_RATE = 48_000
MIN_CHUNK = 64
MAX_CHUNK = 1_048_576


@dataclass(frozen=True)
class AudioLimits:
    max_input_bytes: int = 4 * 1024 * 1024  # manifest JSON (name shared with the video limits for the JSON reader)
    max_events: int = SCHEMA_MAX_EVENTS
    max_assets: int = SCHEMA_MAX_ASSETS
    max_frames: int = SCHEMA_MAX_FRAMES
    max_output_samples: int = SAMPLE_RATE * 600  # 10 minutes at 48 kHz
    max_source_bytes: int = 512 * 1024**2  # per asset file
    max_total_source_bytes: int = 2 * 1024**3  # sum over assets actually bound by selected USE events
    max_concurrent: int = 8  # simultaneous selected USE events (only with overlap policy ALLOW_SUM)
    max_mix_work: int = 200_000_000  # sum over mixed events of samples * output channels
    chunk_samples: int = SAMPLE_RATE  # output samples processed (and held in memory) per step
    max_runtime_seconds: float = 300.0
    max_wav_header_bytes: int = 4096  # bytes of chunk headers examined while walking a RIFF file
    max_wav_chunks: int = 64

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(f"limit {f.name} must be a positive number")
        for name, ceiling in (("max_events", SCHEMA_MAX_EVENTS), ("max_assets", SCHEMA_MAX_ASSETS), ("max_frames", SCHEMA_MAX_FRAMES)):
            if getattr(self, name) > ceiling:
                raise ValueError(f"limit {name} may not exceed the v1 ceiling {ceiling}")
        if not MIN_CHUNK <= self.chunk_samples <= MAX_CHUNK:
            raise ValueError(f"limit chunk_samples must be between {MIN_CHUNK} and {MAX_CHUNK}")
        if self.max_concurrent > 64:
            raise ValueError("limit max_concurrent may not exceed 64")

    def replace(self, **overrides) -> "AudioLimits":
        data = asdict(self)
        data.update({k: v for k, v in overrides.items() if v is not None})
        return AudioLimits(**data)

    def to_dict(self) -> dict:
        return asdict(self)
