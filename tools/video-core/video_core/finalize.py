"""Best-effort finalization of a run directory, shared by the render / audiomix / avmux orchestrators.

Why: after the fresh run directory exists, every exit path (normal verdict, an input that vanished, an unexpected exception, an interrupt) must leave a receipt that
describes what is actually on disk. These helpers inventory the files (name, role, size, SHA-256, partial flag), re-hash the inputs (null plus a reason when unreadable) and
clean the owned `tmp/` directory, and they NEVER raise `Exception`: a failure here is recorded in the receipt (`finalize_errors`) and must not replace the original failure or
the status already chosen. They never overwrite a previous run's paths, never touch anything outside `run_dir`, and never delete the caller's input assets.

A KeyboardInterrupt (a second cancel) is deliberately not swallowed: the receipt then stays as last written (IN_PROGRESS means UNKNOWN by convention).
"""

from __future__ import annotations

import os
import shutil
from typing import Callable

from .tools import sha256_file


def _reason(exc: BaseException) -> str:
    """Exception class only (never the message, which may embed a path)."""
    return type(exc).__name__


def note_error(receipt: dict, step: str, exc: BaseException) -> None:
    receipt.setdefault("finalize_errors", []).append({"step": step, "error": _reason(exc)})


def file_record(path: str, name: str, role: str, partial: bool, hash_cap: Callable[[int], int]) -> dict:
    """{name, role, bytes, sha256, partial}; on a read problem bytes/sha256 are null and `error` names the exception class (the file is still listed)."""
    rec: dict = {"name": name, "role": role, "bytes": None, "sha256": None, "partial": partial}
    try:
        size = os.path.getsize(path)
        rec["bytes"] = size
        rec["sha256"] = sha256_file(path, hash_cap(size))
    except (OSError, ValueError) as exc:
        rec["error"] = _reason(exc)
    return rec


def inventory(run_dir: str, known: tuple, hash_cap: Callable[[int], int], *, include_unrecognised: bool = False, skip: tuple = ("receipt.json",)) -> list:
    """The known (name, role, partial) entries that exist, in the given order. With include_unrecognised (used on the error/interrupt paths, where nobody has vouched for
    the directory's contents) every other regular file directly in run_dir is listed too, role 'unrecognised'. Subdirectories (tmp/) are not listed. Never raises."""
    out, seen = [], set()
    for name, role, partial in known:
        p = os.path.join(run_dir, name)
        if os.path.isfile(p) and not os.path.islink(p):
            out.append(file_record(p, name, role, partial, hash_cap))
            seen.add(name)
    if not include_unrecognised:
        return out
    try:
        names = sorted(os.listdir(run_dir))
    except OSError:
        return out
    for name in names:
        p = os.path.join(run_dir, name)
        if name in seen or name in skip:
            continue
        try:
            if os.path.isfile(p) and not os.path.islink(p):
                out.append(file_record(p, name, "unrecognised", False, hash_cap))
        except OSError:
            continue
    return out


def rehash_inputs(entries: list, hasher: Callable[[dict], str]) -> bool:
    """Set entry['sha256_after'] for each input entry (null plus `sha256_after_error` when unreadable). Returns True only if every entry was read AND equals its
    sha256_before. Never raises."""
    unchanged = True
    for entry in entries:
        try:
            entry["sha256_after"] = hasher(entry)
            entry.pop("sha256_after_error", None)
        except (OSError, ValueError, KeyError) as exc:
            entry["sha256_after"] = None
            entry["sha256_after_error"] = _reason(exc)
        unchanged = unchanged and entry["sha256_after"] is not None and entry["sha256_after"] == entry.get("sha256_before")
    return unchanged


def cleanup_tmp(tmp_dir: str) -> str:
    """Remove the owned tmp directory. Returns 'removed' | 'not created' | 'left in place: <ExceptionClass>'. Never raises."""
    try:
        if not os.path.lexists(tmp_dir):
            return "not created"
        shutil.rmtree(tmp_dir)
        return "removed"
    except OSError as exc:
        return f"left in place: {_reason(exc)}"
