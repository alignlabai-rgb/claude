"""Optional local audio adapter *interface* (design only; not wired into render).

The colour/caption baseline needs no audio and produces a video-only MP4 (`-an`).
A future adapter would turn already-local audio assets into a frame-aligned plan
that a later render stage can mux. This module deliberately contains no voice
synthesis, no provider client and no network code; it is a typing contract only.

The concrete offline planner/mixer lives in `video_core.audiomix` (see AUDIO_SPEC.md); it is
separate, optional, and does not change the video-only behaviour of the commands here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioSegment:
    asset_path: str  # relative path under the asset root (validated like any file source)
    start_frame: int  # integer frame on the episode timeline
    duration_frames: int
    gain_db: float = 0.0


@dataclass(frozen=True)
class AudioPlan:
    segments: tuple[AudioSegment, ...]
    sample_rate: int = 48_000


class AudioAdapter(Protocol):
    """Implementations must be offline, deterministic for identical inputs, and must
    never write outside the work directory they are given."""

    name: str

    def describe(self) -> dict: ...

    def prepare(self, episode, asset_root: str, work_dir: str) -> AudioPlan: ...
