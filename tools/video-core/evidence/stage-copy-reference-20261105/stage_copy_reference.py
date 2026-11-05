"""SIT-06 / GAP-12 stage-copy REFERENCE (test-only, UNREVIEWED; Python 3.11+ stdlib only).  Not connected to video_core / audiomix / avmux or any caller, binder or executor.

`stage_copy(source_root, source_name, pin, dest_root, dest_name)` copies ONE small file from a source root into a FRESH name in a different, existing destination root and then compares, byte for byte
by size + SHA-256, (1) the source before the copy, (2) the source after the copy and (3) the destination, against a PIN THE CALLER SUPPLIED IN ADVANCE ({"bytes", "sha256"}).  The pin is never derived
from what was observed after the copy.  This mirrors public SIT-06 ("mux_inputs/: copy of stem + receipt + video, re-hashed after copy") and GAP-12 ("stem handoff geometry is nobody's job") at a0acfdaf.

PROMOTION (changed after the first checkpoint, see README "Promotion boundary"): the verified partial gets its final name with a create-only step that cannot replace an existing file (POSIX hard link of a regular file, then removal of the helper's OWN partial name;
Windows os.rename, which refuses an existing destination; other platforms: HOLD with the partial kept).  The verification-to-promotion window for rewriting the partial or the source is NOT closed.

A match is an INTEGRITY COMPARISON only: not authenticity, qualification, adoption, readiness, execution permission, creative acceptance, atomicity or durability.
Behaviour that is ENFORCED here: strict argument shapes; roots must be existing directories that are DISJOINT after realpath (same root / alias, destination inside source root and source inside destination root are
refused and named); the source must be a regular file (a symlink or a Windows reparse point is refused; nothing is followed); the source must equal the pin BEFORE anything is created; the copy goes to
`<name>.partial` created with exclusive create ('xb'), is verified, and is promoted to `<name>` only after the verification, by a create-only step (see PROMOTION below); an existing final or partial name
is refused and never overwritten or deleted.  LIMITS (not solved, not invented): promotion itself no longer replaces a file that appears at the final name in between (that earlier defect was reproduced and fixed), but the window between verification and promotion
is not closed for a REWRITE of the partial or the source by another writer, and the whole run is not a transaction; no OS permission, ACL,
read-only or link protection is added or tested; a failed run LEAVES its own `<name>.partial` in place (recorded exactly, never promoted, never deleted by this helper); fsync is a request, not a durability proof;
realpath-based root comparison is only as good as the OS's path resolution (case folding, junctions and 8.3 names are not examined); files above MAX_BYTES are refused.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat

MAX_BYTES = 1 << 20
SCHEMA = "synthetic.stage_copy.result/1"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")            # shape of the public output-name rule, re-stated here (nothing is imported from video_core)
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
NOT_ASSERTED = ["authenticity", "qualification", "adoption", "readiness", "execution permission", "creative acceptance", "atomicity", "durability"]
_HEX = re.compile(r"[0-9a-f]{64}")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _name_ok(n):
    return type(n) is str and NAME.fullmatch(n) is not None and not n.endswith(".") and not n.endswith(".partial") and n.split(".")[0].upper() not in RESERVED


def _pin_ok(p):
    return (type(p) is dict and set(p) == {"bytes", "sha256"} and type(p["bytes"]) is int and 0 <= p["bytes"] <= MAX_BYTES and type(p["sha256"]) is str and _HEX.fullmatch(p["sha256"]) is not None)


def _promotion_method():
    """How a VERIFIED partial may be given its final name WITHOUT replacing an existing file.  POSIX: a regular-file hard link (link(2) fails if the final name exists = create-only), then the helper's own partial name is
    removed.  Windows: os.rename, which refuses an existing destination (not exercised on real Windows by this evidence).  Anything else: None -> HOLD, the partial is kept."""
    return "hardlink_create_only" if os.name == "posix" else "rename_no_overwrite" if os.name == "nt" else None


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def root_relationship(source_root, dest_root):
    """SAME_ROOT | DEST_INSIDE_SOURCE_ROOT | SOURCE_INSIDE_DEST_ROOT | DISJOINT, from realpath (an alias that resolves to the same directory is SAME_ROOT)."""
    s, d = _norm(source_root), _norm(dest_root)
    if s == d:
        return "SAME_ROOT"
    sep = os.sep
    if d.startswith(s.rstrip(sep) + sep):
        return "DEST_INSIDE_SOURCE_ROOT"
    if s.startswith(d.rstrip(sep) + sep):
        return "SOURCE_INSIDE_DEST_ROOT"
    return "DISJOINT"


def _digest(path, chunk=65536):
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            n += len(b)
            if n > MAX_BYTES:
                return {"bytes": n, "sha256": None, "over_limit": True}
            h.update(b)
    return {"bytes": n, "sha256": h.hexdigest()}


class _R:
    def __init__(self, pin):
        self.checks, self.observed, self.pin = [], {}, pin
        self.roots = None
        self.created = False
        self.promotion = None

    def check(self, name, expected, observed):
        ok = expected == observed
        self.checks.append({"name": name, "outcome": "MATCH" if ok else "MISMATCH", "expected": expected, "observed": observed})
        return ok


def _finish(r, outcome, reason, completed, dest_root=None, final=None, partial=None):
    residue = {"final_exists": None, "partial_exists": None, "partial_bytes": None, "partial_sha256": None, "dest_root_listing": None}
    if dest_root and final and partial and os.path.isdir(dest_root):
        residue["final_exists"], residue["partial_exists"] = os.path.lexists(final), os.path.lexists(partial)
        if r.created and residue["partial_exists"]:
            try:
                d = _digest(partial)
                residue["partial_bytes"], residue["partial_sha256"] = d["bytes"], d["sha256"]
            except OSError:
                pass
        residue["dest_root_listing"] = sorted(os.listdir(dest_root))[:50]
    return {"result_schema": SCHEMA, "outcome": outcome, "reason": reason, "completed": completed, "pin_supplied_in_advance": r.pin, "observed": r.observed, "checks": r.checks,
            "root_relationship": r.roots, "promotion": r.promotion, "residue": residue, "created_by_this_call": r.created, "not_asserted": list(NOT_ASSERTED)}


def stage_copy(source_root, source_name, pin, dest_root, dest_name, *, chunk_size=65536, hook=None) -> dict:
    """Never raises for expected failures.  hook(event, info) is a TEST-ONLY seam: events 'before_partial_create', 'chunk_written' and 'before_destination_verify'; an exception from it is reported as an interrupted copy."""
    r = _R(pin if _pin_ok(pin) else None)
    if not (type(source_root) is str and source_root and "\x00" not in source_root and type(dest_root) is str and dest_root and "\x00" not in dest_root and _name_ok(source_name)
            and _name_ok(dest_name) and _pin_ok(pin) and type(chunk_size) is int and 1 <= chunk_size <= MAX_BYTES and (hook is None or callable(hook))):
        return _finish(r, "REFUSED", "INVALID_ARGUMENT", False)
    if not (os.path.isdir(source_root) and os.path.isdir(dest_root)):
        return _finish(r, "REFUSED", "ROOT_MISSING", False)
    r.roots = root_relationship(source_root, dest_root)
    if r.roots != "DISJOINT":
        return _finish(r, "REFUSED", "ROOT_" + r.roots, False)
    src = os.path.join(source_root, source_name)
    final = os.path.join(dest_root, dest_name)
    partial = final + ".partial"
    try:
        st = os.lstat(src)
    except OSError:
        return _finish(r, "REFUSED", "SOURCE_MISSING", False, dest_root, final, partial)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or (getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
        return _finish(r, "REFUSED", "SOURCE_NOT_REGULAR_FILE", False, dest_root, final, partial)
    if st.st_size > MAX_BYTES:
        return _finish(r, "REFUSED", "SOURCE_TOO_LARGE", False, dest_root, final, partial)
    before = _digest(src)
    r.observed["source_before"] = before
    a = r.check("source_before_bytes_equal_pin", pin["bytes"], before["bytes"])
    b = r.check("source_before_sha256_equal_pin", pin["sha256"], before["sha256"])
    if not (a and b):
        return _finish(r, "REFUSED", "SOURCE_PIN_MISMATCH", False, dest_root, final, partial)
    if os.path.lexists(final):
        r.check("destination_final_name_is_fresh", True, False)
        return _finish(r, "REFUSED", "DESTINATION_EXISTS", False, dest_root, final, partial)
    if os.path.lexists(partial):
        r.check("destination_partial_name_is_fresh", True, False)
        return _finish(r, "REFUSED", "PARTIAL_EXISTS", False, dest_root, final, partial)
    stream, n = hashlib.sha256(), 0
    try:
        if hook:
            hook("before_partial_create", {"partial_path": partial, "source_path": src})
        with open(src, "rb") as fin, open(partial, "xb") as fout:
            r.created = True
            idx = 0
            while True:
                buf = fin.read(chunk_size)
                if not buf:
                    break
                stream.update(buf)
                n += len(buf)
                fout.write(buf)
                fout.flush()
                idx += 1
                if hook:
                    hook("chunk_written", {"index": idx, "partial_path": partial, "source_path": src})
            os.fsync(fout.fileno())
    except FileExistsError:
        r.check("destination_partial_name_is_fresh", True, False)
        return _finish(r, "REFUSED", "PARTIAL_EXISTS", False, dest_root, final, partial)
    except Exception as exc:                                   # OSError or an exception raised by the test hook
        r.observed["copy_stream_before_interruption"] = {"bytes": n, "sha256": stream.hexdigest()}
        r.observed["interruption"] = type(exc).__name__
        return _finish(r, "INTERRUPTED", "COPY_INTERRUPTED", False, dest_root, final, partial)
    r.observed["copy_stream"] = {"bytes": n, "sha256": stream.hexdigest()}
    try:
        if hook:
            hook("before_destination_verify", {"partial_path": partial, "source_path": src})
        after = _digest(src)
        dest = _digest(partial)
    except Exception as exc:
        r.observed["interruption"] = type(exc).__name__
        return _finish(r, "INTERRUPTED", "VERIFY_INTERRUPTED", False, dest_root, final, partial)
    r.observed["source_after"], r.observed["destination"] = after, dest
    ok_src = r.check("source_after_bytes_equal_pin", pin["bytes"], after["bytes"]) & r.check("source_after_sha256_equal_pin", pin["sha256"], after["sha256"])
    ok_dst = r.check("destination_bytes_equal_pin", pin["bytes"], dest["bytes"]) & r.check("destination_sha256_equal_pin", pin["sha256"], dest["sha256"])
    if not ok_src or not ok_dst:
        return _finish(r, "FAILED_VERIFICATION", "SOURCE_CHANGED_DURING_COPY" if not ok_src else "DESTINATION_MISMATCH", False, dest_root, final, partial)
    method = _promotion_method()
    r.promotion = {"method": method, "completed": False, "partial_removed": False}
    if method is None:
        return _finish(r, "HOLD", "PROMOTION_UNAVAILABLE", False, dest_root, final, partial)           # no known way to avoid replacing an existing file on this platform: keep the partial
    if os.path.lexists(final):
        r.check("destination_final_name_is_still_fresh_before_promotion", True, False)
        return _finish(r, "FAILED_VERIFICATION", "PROMOTION_BLOCKED", False, dest_root, final, partial)
    try:
        if method == "hardlink_create_only":
            if not stat.S_ISREG(os.lstat(partial).st_mode):
                return _finish(r, "HOLD", "PROMOTION_UNAVAILABLE", False, dest_root, final, partial)
            os.link(partial, final, follow_symlinks=False)          # create-only: raises FileExistsError if ANY writer created the final name in the meantime; the other file is never touched
        else:
            os.rename(partial, final)                               # Windows: refuses an existing destination
    except FileExistsError:
        r.check("destination_final_name_is_still_fresh_at_promotion", True, False)
        return _finish(r, "FAILED_VERIFICATION", "PROMOTION_BLOCKED", False, dest_root, final, partial)
    except (OSError, NotImplementedError):
        return _finish(r, "HOLD" if method == "hardlink_create_only" else "FAILED_VERIFICATION", "PROMOTION_UNAVAILABLE" if method == "hardlink_create_only" else "PROMOTION_ERROR", False, dest_root, final, partial)
    r.promotion["completed"] = True
    if method == "hardlink_create_only":
        try:
            os.unlink(partial)                                      # only the name this call created
            r.promotion["partial_removed"] = True
        except OSError:
            pass                                                    # the verified final exists; the extra partial link is reported in the residue, not hidden
    else:
        r.promotion["partial_removed"] = True
    try:
        size_after = os.stat(final).st_size
    except OSError:
        size_after = None
    r.check("promoted_file_bytes_equal_pin", pin["bytes"], size_after)
    return _finish(r, "INTEGRITY_MATCH", "ALL_PINNED_COMPARISONS_EQUAL", True, dest_root, final, partial)
