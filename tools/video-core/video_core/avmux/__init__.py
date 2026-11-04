"""Optional offline muxing of one explicitly selected local H.264 video and one qualified 48 kHz s16 PCM WAV stem.

Separate from the video-only commands and from the audio planner/mixer, both of which stay independently usable and unchanged. See AVMUX_SPEC.md.
"""

from .manifest import AvManifest, load_manifest  # noqa: F401
from .plan import AvPlan, prepare_plan  # noqa: F401
