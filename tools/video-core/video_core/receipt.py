"""Receipt helpers: atomic writes, timestamps, platform facts (no environment dump)."""

from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone

from . import __version__

RECEIPT_SCHEMA = "video_core.receipt/1"

STATUS_SKIPPED = "SKIPPED_TOOL_UNAVAILABLE"
STATUS_MATCH = "READBACK_MATCH"
STATUS_MISMATCH = "READBACK_MISMATCH"
STATUS_INSPECTION_FAILED = "INSPECTION_FAILED"
STATUS_PROCESS_FAILED = "PROCESS_FAILED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_SIGNALED = "SIGNALED"
STATUS_FLOOD = "FLOOD_KILLED"
STATUS_INTERRUPTED = "INTERRUPTED"
STATUS_LAUNCH_FAILED = "LAUNCH_FAILED"
STATUS_CONTAINMENT_REFUSED = "CONTAINMENT_REFUSED"  # an explicit resource envelope could not be applied; the target was not started
STATUS_UNKNOWN = "UNKNOWN"
STATUS_IN_PROGRESS = "IN_PROGRESS"  # a receipt still saying this means the run did not finish: treat as UNKNOWN

QUALITY_NOTE = (
    "Metadata and decoded-frame-count readback only. No audio, visual or creative quality was evaluated; "
    "a matching readback is not a quality or acceptance proof."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def platform_facts() -> dict:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "video_core": __version__,
    }


def dumps(obj) -> bytes:
    """Serialize a receipt or a CLI diagnostic document as UTF-8 JSON.

    Text that UTF-8 can encode is written literally, byte-for-byte as before (Japanese stays Japanese). A string that cannot be encoded
    (a lone surrogate, e.g. from a JSON key written as "\\ud800" or from a POSIX name carrying a raw non-UTF-8 byte decoded with
    surrogateescape) is written as the JSON escape for that exact code point, so the document stays valid JSON, a JSON decode returns
    the identical str, nothing is replaced or normalised, and reporting an error can never itself raise UnicodeEncodeError and strand
    a run.

    This helper is for receipts and diagnostics only. Canonical plan bytes (the hashed `canonical_bytes` functions) deliberately keep
    their strict UTF-8 encoding: a plan that cannot be encoded exactly must fail, never be escaped into a different hash.
    """
    return (json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8", "backslashreplace")


def write_new(path: str, data: bytes) -> None:
    """Create-exclusive write: refuses to overwrite anything."""
    with open(path, "xb") as fh:
        fh.write(data)


def replace_atomic(path: str, data: bytes) -> None:
    """Replace our own receipt via a sibling temp file + os.replace."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
