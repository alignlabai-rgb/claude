"""REFERENCE ONLY - an opt-in helper that is NOT imported by video_core, wired to no CLI flag, and enabled nowhere. It lives in an evidence directory on purpose.

What it shows: if an owner decides that a read route should accept (trusted_root, relative_path) instead of an arbitrary absolute path, the existing, already-tested descendant
semantics of `paths.resolve_asset` can be reused unchanged:
  * `trusted_root` is chosen by the CALLER and may itself be an alias (symlink / junction / ordinary directory): `real_directory` resolves it, exactly as for --asset-root today;
  * `rel` must be a normalized, '/'-separated relative path (no '..', '.', absolute, drive, ':', backslash, control/format chars, option-like or hidden components, Windows-unsafe
    or reserved names, UTF-8 component length <= 255) with an allowed extension;
  * every component below the root is `os.lstat`-ed and rejected if link-like (symlink, or the Windows reparse attribute) - parents included;
  * the target must be a regular file, inside the resolved root, and not larger than `max_bytes`.
The functions are thin delegations (no re-implementation, so no second policy to drift). They do NOT make resolution and a later open() atomic: see the README.
Which trusted root a route should use, and what to do with arbitrary absolute paths, are OWNER DECISIONS this file does not make.
"""

from __future__ import annotations

from video_core import paths


def resolve_under_trusted_root(trusted_root: str, rel: str, *, allowed_ext=None, max_bytes: int | None = None, ptr: str = "/path") -> paths.ResolvedFile:
    """Same result and same rejections as `paths.resolve_asset(trusted_root, rel, ...)` (codes: root_*, path_*, source_extension_unsupported, asset_missing,
    symlink_rejected, not_regular_file, symlink_escape, limit_source_bytes)."""
    return paths.resolve_asset(trusted_root, rel, ptr=ptr, allowed_ext=allowed_ext, max_bytes=max_bytes)


def media_under_trusted_root(trusted_root: str, rel: str, *, max_bytes: int | None = None) -> tuple[str, str]:
    """The inspect-route analogue of `paths.check_media_for_inspect`, for a path under a trusted root: returns (abspath, ffprobe demuxer) using the inspect extension allow-list."""
    rf = resolve_under_trusted_root(trusted_root, rel, allowed_ext=paths.PROBE_EXTENSIONS, max_bytes=max_bytes, ptr="/media")
    return rf.abspath, paths.PROBE_EXTENSIONS[rf.ext]
