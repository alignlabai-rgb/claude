"""Strict numeric validation shared by every caller-facing limit and by the subprocess runner.

Rules (all violations raise ValueError, before any file is written or process launched):
  * bool is never a number here (True would silently mean 1)
  * integer quantities must be real ints (a float such as 100.0 or 5.5 is rejected, not truncated)
  * time quantities may be int or float but must be FINITE, > 0 and <= a documented ceiling:
    NaN and +/-infinity are rejected (a NaN deadline makes `now >= deadline` always False, silently disabling the bound),
    and so are absurd finite values such as 1e308 that would be unbounded in practice
  * sub-second positive timeouts (0.25, 0.001) remain legitimate
"""

from __future__ import annotations

import math

INT_MAX = 2**63 - 1
TIMEOUT_CEILING_SECONDS = 86_400.0  # 24 h: the longest wall-clock bound any caller may request
GRACE_CEILING_SECONDS = 3_600.0  # termination grace / pipe-drain waits


def _describe(value) -> str:
    return f"{type(value).__name__} {value!r}"[:80]


def positive_int(name: str, value, ceiling: int = INT_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer (got {_describe(value)})")
    if value <= 0:
        raise ValueError(f"{name} must be > 0 (got {value})")
    if value > ceiling:
        raise ValueError(f"{name} must be <= {ceiling} (got {value})")
    return value


def non_negative_int(name: str, value, ceiling: int = INT_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer (got {_describe(value)})")
    if value < 0:
        raise ValueError(f"{name} must be >= 0 (got {value})")
    if value > ceiling:
        raise ValueError(f"{name} must be <= {ceiling} (got {value})")
    return value


def finite_seconds(name: str, value, ceiling: float = TIMEOUT_CEILING_SECONDS) -> float:
    """A positive, finite duration in seconds (int or float), at most `ceiling`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number of seconds (got {_describe(value)})")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite (got {value}); NaN/infinity would disable the bound")
    if value <= 0:
        raise ValueError(f"{name} must be > 0 (got {value})")
    if value > ceiling:
        raise ValueError(f"{name} must be <= {ceiling:g} seconds (got {value:g})")
    return value
