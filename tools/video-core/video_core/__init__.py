"""video_core: offline episode JSON -> integer-frame timeline -> FFmpeg plan -> receipt."""

__version__ = "0.1.0"

from .episode import Episode, load_episode, parse_json_bytes, validate_episode  # noqa: F401
from .limits import Limits  # noqa: F401
from .plan import Plan, prepare  # noqa: F401
from .srt import export_srt  # noqa: F401
from .render import render as render_episode  # noqa: F401  (module video_core.render keeps its name)
