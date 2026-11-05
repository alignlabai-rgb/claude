"""Path safety. Filenames are data: they are checked, resolved and passed as single
argv elements; they are never interpolated into shell text or filtergraph text.

Policy (deliberately conservative, portable across POSIX and Windows):
  * relative, normalized, '/'-separated paths only
  * no ':' anywhere (protocol-like names, drive letters, ffmpeg 'proto:' syntax)
  * no backslash, control or format characters, glob/pattern/option characters
  * no component starting with '-' (option-like) or '.' (hidden / traversal)
  * no symlink anywhere along the path under the asset root (not only escapes)
  * the final target must be an existing regular file (no FIFO, device, dir)
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from dataclasses import dataclass

from .errors import Issue, UnsafePath

# extension -> (kind, ffmpeg demuxer forced with -f). Forcing the demuxer stops
# playlist/concat/script containers that can reference other files or URLs.
SOURCE_EXTENSIONS = {
    ".mp4": ("video", "mov"),
    ".m4v": ("video", "mov"),
    ".mov": ("video", "mov"),
    ".mkv": ("video", "matroska"),
    ".webm": ("video", "matroska"),
    ".png": ("image", "image2"),
    ".jpg": ("image", "image2"),
    ".jpeg": ("image", "image2"),
    ".bmp": ("image", "image2"),
}
FONT_EXTENSIONS = (".ttf", ".otf")
PROBE_EXTENSIONS = {".mp4": "mov", ".m4v": "mov", ".mov": "mov", ".mkv": "matroska", ".webm": "matroska"}

_ALLOWED_PUNCT = set("._-+()~@ ")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_DRIVE = re.compile(r"^[A-Za-z]:")
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_OUTPUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _cp(ch: str) -> str:
    return f"U+{ord(ch):04X}"


def check_relative_path(path: str, *, allowed_ext: dict | tuple | None = None) -> list[tuple[str, str]]:
    """Syntactic check only (no filesystem access). Returns (code, message) pairs."""
    problems: list[tuple[str, str]] = []
    if not isinstance(path, str) or not path:
        return [("path_empty", "path must be a non-empty string")]
    if len(path) > 512:
        problems.append(("path_too_long", "path exceeds 512 characters"))
    for ch in path:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            return problems + [("path_control_char", f"control/format character {_cp(ch)} is not allowed")]
    if path.startswith(("/", "\\")) or _DRIVE.match(path):
        return problems + [("path_absolute", "absolute, UNC or drive-qualified paths are not allowed")]
    if _SCHEME.match(path) or "://" in path:
        return problems + [("path_protocol", "protocol-like names are not allowed")]
    if "\\" in path:
        return problems + [("path_backslash", "use '/' separators; backslash is not allowed")]
    components = path.split("/")
    for comp in components:
        if comp == "":
            problems.append(("path_not_normalized", "empty path component ('//' or trailing '/')"))
            continue
        if comp == "..":
            problems.append(("path_traversal", "'..' traversal is not allowed"))
            continue
        if comp == ".":
            problems.append(("path_not_normalized", "'.' component is not allowed"))
            continue
        if comp.startswith("-"):
            problems.append(("path_option_like", f"component {comp!r} starts with '-' (option-like)"))
        if comp.startswith("."):
            problems.append(("path_hidden", f"hidden component {comp!r} is not allowed"))
        if comp.endswith((".", " ")) or comp.startswith(" "):
            problems.append(("path_windows_unsafe", f"component {comp!r} has leading space or trailing dot/space"))
        if comp.split(".")[0].upper() in _WIN_RESERVED:
            problems.append(("path_reserved_name", f"component {comp!r} is a reserved device name"))
        if len(comp.encode("utf-8")) > 255:
            problems.append(("path_component_too_long", "component exceeds 255 UTF-8 bytes"))
        for ch in comp:
            cat = unicodedata.category(ch)
            if ch in _ALLOWED_PUNCT or cat[0] in "LNM" or (ord(ch) > 127 and cat[0] == "P"):
                continue  # letters/digits/marks in any script; non-ASCII punctuation such as full-width brackets
            problems.append(("path_forbidden_char", f"character {_cp(ch)} is not allowed in file names"))
            break
    if allowed_ext is not None and not problems:
        ext = os.path.splitext(components[-1])[1].lower()
        if ext not in allowed_ext:
            problems.append(("source_extension_unsupported", f"extension {ext!r} is not in the allow-list"))
    return problems


@dataclass(frozen=True)
class ResolvedFile:
    rel: str
    abspath: str
    size: int
    ext: str


def _utf8_encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _root_not_utf8(what: str) -> UnsafePath:
    msg = (f"{what} contains a name that cannot be represented as UTF-8 (for example a raw non-UTF-8 byte in a POSIX directory name); "
           "plans and receipts are UTF-8 documents, so such a root is refused before anything is created. Use a UTF-8 directory name")
    return UnsafePath(msg, [Issue("root_invalid", what, msg)])


def real_directory(path: str, what: str) -> str:
    """Caller-chosen root: must be an existing directory. Returns its realpath.

    The path (as given and after resolution, so a symlinked parent counts too) must be encodable as UTF-8: every plan and receipt is a UTF-8
    document that carries these roots, and a name that cannot be encoded (a raw byte such as 0xE9 in a POSIX name decodes to a lone
    surrogate) would otherwise only fail late, after files exist. Valid non-ASCII names such as Japanese ones are unaffected. The
    rejection message never echoes the offending text.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        raise UnsafePath(f"{what} is not a usable path", [Issue("root_invalid", what, f"{what} is not a usable path")])
    if not _utf8_encodable(path):
        raise _root_not_utf8(what)
    try:
        real = os.path.realpath(os.path.abspath(path), strict=True)
    except (OSError, ValueError):
        raise UnsafePath(f"{what} does not exist", [Issue("root_missing", what, f"{what} does not exist")]) from None
    if not _utf8_encodable(real):
        raise _root_not_utf8(what)
    if not os.path.isdir(real):
        raise UnsafePath(f"{what} is not a directory", [Issue("root_not_directory", what, f"{what} is not a directory")])
    return real


def _inside(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:  # different drives on Windows
        return False


def is_link_like(st) -> bool:
    """Symlink, or a Windows reparse point (junction, mount point, other link-like object)."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def resolve_asset(asset_root: str, rel: str, *, ptr: str, allowed_ext=None, max_bytes: int | None = None) -> ResolvedFile:
    """Resolve `rel` under `asset_root` without following any symlink."""
    problems = check_relative_path(rel, allowed_ext=allowed_ext)
    if problems:
        raise UnsafePath(f"unsafe path at {ptr}", [Issue(c, ptr, m) for c, m in problems])
    root = real_directory(asset_root, "asset_root")
    cur = root
    st = None
    for comp in rel.split("/"):
        cur = os.path.join(cur, comp)
        try:
            st = os.lstat(cur)
        except OSError:
            raise UnsafePath("source file does not exist", [Issue("asset_missing", ptr, f"{rel!r} does not exist under the asset root")]) from None
        if is_link_like(st):
            raise UnsafePath("symlink in source path", [Issue("symlink_rejected", ptr, f"{rel!r} passes through a symlink or reparse point (junction); these are rejected")])
    assert st is not None
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePath("source is not a regular file", [Issue("not_regular_file", ptr, f"{rel!r} is not a regular file")])
    real = os.path.realpath(cur)
    if not _inside(root, real):  # defence in depth; lstat chain should already guarantee this
        raise UnsafePath("path escapes asset root", [Issue("symlink_escape", ptr, f"{rel!r} resolves outside the asset root")])
    if max_bytes is not None and st.st_size > max_bytes:
        raise UnsafePath("source too large", [Issue("limit_source_bytes", ptr, f"{rel!r} is {st.st_size} bytes; limit is {max_bytes}")])
    return ResolvedFile(rel=rel, abspath=cur, size=st.st_size, ext=os.path.splitext(rel)[1].lower())


@dataclass(frozen=True)
class OutputLocation:
    root: str  # realpath of the caller-chosen output root
    name: str
    run_dir: str  # root/name, which must not exist yet


def check_output_location(output_root: str, name: str, asset_root: str | None = None) -> OutputLocation:
    """Validate (without creating anything) a fresh output directory root/name."""
    if not isinstance(name, str) or not _OUTPUT_NAME.fullmatch(name) or name.endswith(".") or name.split(".")[0].upper() in _WIN_RESERVED:
        raise UnsafePath(
            "unsafe output name",
            [Issue("output_name_invalid", "output_name", "output name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}, no trailing '.', no reserved device name")],
        )
    root = real_directory(output_root, "output_root")
    run_dir = os.path.join(root, name)
    if os.path.lexists(run_dir):  # also catches dangling symlinks
        raise UnsafePath("output exists", [Issue("output_exists", "output_name", "output location already exists; overwriting is refused")])
    if not os.access(root, os.W_OK | os.X_OK):
        raise UnsafePath("output root not writable", [Issue("output_root_not_writable", "output_root", "output root is not writable")])
    if asset_root is not None:
        assets = real_directory(asset_root, "asset_root")
        if _inside(assets, run_dir) or _inside(run_dir, assets):
            raise UnsafePath(
                "output overlaps asset root",
                [Issue("output_overlaps_assets", "output_root", "output location must not be inside (or contain) the asset root")],
            )
    return OutputLocation(root=root, name=name, run_dir=run_dir)


def check_media_for_inspect(path: str) -> tuple[str, str]:
    """A local media file for read-only inspection. Returns (abspath, demuxer)."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise UnsafePath("unusable path", [Issue("path_empty", "media", "media path is not usable")])
    if _SCHEME.match(path) and not _DRIVE.match(path) or "://" in path:
        raise UnsafePath("protocol-like path", [Issue("path_protocol", "media", "protocol-like inputs are not allowed")])
    absolute = os.path.abspath(path)
    base = os.path.basename(absolute)
    if base.startswith("-"):
        raise UnsafePath("option-like name", [Issue("path_option_like", "media", "file name starts with '-'")])
    ext = os.path.splitext(base)[1].lower()
    if ext not in PROBE_EXTENSIONS:
        raise UnsafePath("extension not allowed", [Issue("source_extension_unsupported", "media", f"extension {ext!r} is not in the inspect allow-list")])
    try:
        st = os.lstat(absolute)
    except OSError:
        raise UnsafePath("media does not exist", [Issue("asset_missing", "media", "media file does not exist")]) from None
    if is_link_like(st):  # same rule as resolve_asset: a symlink OR a Windows reparse point (junction, mount point, other link-like object)
        raise UnsafePath("symlink", [Issue("symlink_rejected", "media", "symlinked or reparse-point media is rejected")])
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePath("not a regular file", [Issue("not_regular_file", "media", "media is not a regular file")])
    return absolute, PROBE_EXTENSIONS[ext]
