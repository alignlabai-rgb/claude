"""Deterministic SRT export. UTF-8, no BOM, LF line endings, exact caption text.

Cue end is the (exclusive) end_frame boundary. Cues are ordered by
(start_frame, end_frame, input index) and numbered from 1. Text is written
verbatim: SRT has no escape syntax and none is applied, so colon, quotes,
backslashes, '%' and '-->' appear unchanged.
"""

from __future__ import annotations

from .errors import Issue, ValidationFailed
from .timebase import frame_to_ms, ms_to_srt


def export_srt(episode) -> bytes:
    order = sorted(range(len(episode.captions)), key=lambda i: (episode.captions[i].start_frame, episode.captions[i].end_frame, i))
    blocks = []
    for n, idx in enumerate(order, start=1):
        cap = episode.captions[idx]
        start_ms = frame_to_ms(cap.start_frame, episode.fps)
        end_ms = frame_to_ms(cap.end_frame, episode.fps)
        if end_ms <= start_ms:
            raise ValidationFailed(
                "caption not representable in SRT",
                [Issue("caption_srt_resolution", f"/captions/{idx}", "caption spans less than one millisecond at this frame rate")],
            )
        blocks.append(f"{n}\n{ms_to_srt(start_ms)} --> {ms_to_srt(end_ms)}\n{cap.text}")
    if not blocks:
        return b""
    return ("\n\n".join(blocks) + "\n").encode("utf-8")
