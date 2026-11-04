"""Optional offline local audio planner and PCM WAV mixer (48 kHz, signed 16-bit, mono/stereo).

Separate from the video-only commands (which keep `-an`). No resampling, stretching, speech/TTS, network, normalization or mux.
See AUDIO_SPEC.md.
"""

from .limits import AudioLimits  # noqa: F401
from .manifest import AudioManifest, load_manifest  # noqa: F401
from .plan import AudioPlan, frame_to_sample, prepare_plan  # noqa: F401
