"""Caller-visible resource limits. All are enforced before any process launch.

The v1 schema ceilings (18,000 frames, 7680 px per dimension, 256 clips,
512 captions) are absolute: callers may lower the defaults but not exceed them.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, fields
from fractions import Fraction

from .errors import Issue, ValidationFailed
from .numeric import finite_seconds, positive_int

SCHEMA_MAX_FRAMES = 18_000
SCHEMA_MAX_DIMENSION = 7_680
SCHEMA_MAX_CLIPS = 256
SCHEMA_MAX_CAPTIONS = 512
SCHEMA_MAX_CAPTION_CHARS = 512
# Win32 CreateProcess: lpCommandLine is at most 32,767 characters *including* the terminating NUL.
WIN_CMDLINE_HARD_LIMIT = 32_766


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
    max_argv_bytes: int = 100_000  # total UTF-8 bytes of all arguments (+1 each)
    max_windows_cmdline_chars: int = 32_000  # UTF-16 code units of the list2cmdline string; enforced on Windows (name kept for CLI compatibility)
    max_runtime_seconds: float = 600.0  # render wall clock
    probe_timeout_seconds: float = 30.0  # -version / -encoders / -filters
    inspect_timeout_seconds: float = 120.0  # ffprobe -count_frames
    max_diag_bytes: int = 16_384  # kept per stream (head + tail)
    max_drain_bytes: int = 64 * 1024**2  # child output beyond this => killed
    max_output_bytes: int = 2 * 1024**3  # passed to ffmpeg -fs

    FLOAT_FIELDS = ("max_runtime_seconds", "probe_timeout_seconds", "inspect_timeout_seconds")

    def __post_init__(self) -> None:
        # every field is validated strictly *before* anything else looks at it: no bool, no NaN/inf, no float for an integer limit
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name in self.FLOAT_FIELDS:
                finite_seconds(f"limit {f.name}", v)
            else:
                positive_int(f"limit {f.name}", v)
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
        if self.max_windows_cmdline_chars > WIN_CMDLINE_HARD_LIMIT:
            raise ValueError(f"limit max_windows_cmdline_chars may not exceed the Win32 command line limit {WIN_CMDLINE_HARD_LIMIT}")
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


def is_windows() -> bool:
    return os.name == "nt"


def utf16_units(text: str) -> int:
    """Number of UTF-16 code units in `text`: BMP = 1, non-BMP = 2 (surrogate pair).

    Windows measures the CreateProcessW command line in UTF-16 code units, not in Python code
    points and not in UTF-8 bytes. A lone surrogate (ill-formed text) counts as the single unit
    CreateProcessW would receive for it, so measurement never raises.
    """
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def has_ill_formed_utf16(text: str) -> bool:
    """True if `text` contains a surrogate code point. In a Python str a well-formed pair is stored
    as one non-BMP code point, so any surrogate code point present is unpaired."""
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def cmdline_utf16_units(argv: list) -> int:
    """UTF-16 code units of the Windows command line Python builds for `argv` (list2cmdline),
    executable included. list2cmdline is the exact quoting subprocess applies on Windows, so the
    count is meaningful as an interface bound on any platform."""
    return utf16_units(subprocess.list2cmdline([str(a) for a in argv]))


cmdline_chars = cmdline_utf16_units  # backward-compatible name; the unit is UTF-16 code units, not code points


def argv_budget(argv: list) -> dict:
    texts = [str(a) for a in argv]
    return {
        # UTF-8 bytes (+1 separator each); surrogatepass keeps the count total and conservative (3 bytes per lone surrogate)
        "argv_bytes": sum(len(t.encode("utf-8", "surrogatepass")) + 1 for t in texts),
        "windows_cmdline_utf16_units": cmdline_utf16_units(texts),
        "ill_formed_utf16": any(has_ill_formed_utf16(t) for t in texts),
    }


def check_argv_budget(argv: list, limits: Limits, *, windows: bool | None = None) -> dict:
    """Reject an argument list that is too large *before* launch. Returns the measured budget.

    * The UTF-8 byte bound applies on every platform.
    * The Windows bound (UTF-16 code units of the list2cmdline string) and the ban on ill-formed
      text (lone surrogates) are enforced when running on Windows or with `windows=True` (tests);
      elsewhere they are only reported (`windows_cmdline_utf16_units`, `ill_formed_utf16`).
    """
    budget = argv_budget(argv)
    if budget["argv_bytes"] > limits.max_argv_bytes:
        raise ValidationFailed("ffmpeg argument list too large",
                               [Issue("limit_argv_bytes", "argv", f"ffmpeg arguments are {budget['argv_bytes']} bytes; limit is {limits.max_argv_bytes}")])
    win = is_windows() if windows is None else windows
    if win and budget["ill_formed_utf16"]:
        raise ValidationFailed("ill-formed text in command line",
                               [Issue("argv_ill_formed_utf16", "argv", "an argument contains an unpaired surrogate; it cannot be passed to a Windows process as valid text")])
    if win and budget["windows_cmdline_utf16_units"] > limits.max_windows_cmdline_chars:
        raise ValidationFailed("ffmpeg command line too long for Windows",
                               [Issue("limit_cmdline_windows", "argv",
                                      f"command line is {budget['windows_cmdline_utf16_units']} UTF-16 code units; Windows limit used is {limits.max_windows_cmdline_chars} (hard limit {WIN_CMDLINE_HARD_LIMIT})")])
    return budget
