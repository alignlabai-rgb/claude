"""python -m video_core.avmux [validate|plan|mux] MANIFEST ...

`plan` is the default action. `mux` executes only with --mux and an explicit --output-root; otherwise it is a dry run.
validate/plan create no files and launch no subprocesses (ffprobe is NOT run by them); the explicit mux preflight measures the real video with ffprobe.
Exit codes: 0 verified (MOV: sample-exact contract met; MP4: AAC technical tolerance met) | 1 rejected before anything was created | 2 usage |
            3 skipped (tool unavailable) | 4 ran but not verified (mismatch, failure, timeout, signal, unknown) | 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
import sys

from .. import receipt as rc
from ..errors import Issue, ToolUnavailable, VideoCoreError
from ..limits import Limits
from . import mux as muxmod
from .manifest import load_manifest
from .mux import OK_STATUSES, mux
from .plan import prepare_plan

SUBCOMMANDS = ("validate", "plan", "mux", "reinspect")
_LIMIT_FLAGS = (("max-frames", int), ("max-source-bytes", int), ("max-argv-bytes", int), ("max-windows-cmdline-chars", int), ("max-runtime-seconds", float),
                ("max-output-bytes", int), ("max-diag-bytes", int), ("max-drain-bytes", int), ("probe-timeout-seconds", float), ("inspect-timeout-seconds", float))


def _emit(obj) -> None:
    sys.stdout.buffer.write(rc.dumps(obj))
    sys.stdout.buffer.flush()


def _read(path: str, limit: int) -> bytes:
    try:
        st = os.stat(path)
    except OSError:
        raise VideoCoreError("manifest cannot be read", [Issue("input_unreadable", "manifest", "manifest does not exist or cannot be read")]) from None
    if not stat.S_ISREG(st.st_mode):
        raise VideoCoreError("manifest is not a regular file", [Issue("input_not_regular", "manifest", "manifest must be a regular file")])
    if st.st_size > limit:
        raise VideoCoreError("manifest too large", [Issue("limit_input_bytes", "manifest", f"manifest is {st.st_size} bytes; limit is {limit}")])
    with open(path, "rb") as fh:
        return fh.read(limit + 1)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m video_core.avmux", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", metavar="{validate,plan,mux,reinspect}")
    for name, help_ in (("validate", "validate the manifest only (reads nothing else; creates nothing)"),
                        ("plan", "validate, hash the two inputs, parse the WAV, print the canonical plan (default; no files, no processes)"),
                        ("mux", "mux into a fresh directory (requires --mux; otherwise a dry run)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("manifest")
        if name != "validate":
            p.add_argument("--asset-root", help="existing directory both inputs must live under")
            p.add_argument("--output-root", help="existing directory under which the fresh output directory is created")
            p.add_argument("--output-name", help="fresh output directory name (default mux-<plan hash prefix>)")
            p.add_argument("--ffmpeg")
            p.add_argument("--ffprobe")
        if name == "mux":
            p.add_argument("--mux", action="store_true", help="actually run ffprobe checks and the mux (explicit opt-in)")
            from ..containment import add_cli_args as _add_envelope_args

            _add_envelope_args(p)
        g = p.add_argument_group("resource limits (finite numbers only; schema ceilings cannot be raised)")
        for flag, typ in _LIMIT_FLAGS:
            g.add_argument(f"--{flag}", type=typ, default=None, dest=flag.replace("-", "_"))
    _add_reinspect(sub)
    return ap


def _add_reinspect(sub) -> None:
    p = sub.add_parser("reinspect", help="re-read an EXISTING muxed file's audio packet side data into a NEW correction receipt (never remuxes)")
    p.add_argument("media", help="existing local .mp4/.mov/.m4v/.mkv/.webm (read-only)")
    p.add_argument("--expect-sha256", required=True, help="the media's SHA-256 recorded in the original receipt; a mismatch inspects nothing")
    p.add_argument("--original-receipt", help="the original receipt.json (referenced by SHA-256 and compared)")
    p.add_argument("--output-root", required=True)
    p.add_argument("--output-name")
    p.add_argument("--ffprobe")
    p.add_argument("--reinspect", action="store_true", help="actually run ffprobe and write the correction receipt (explicit opt-in; otherwise a dry run)")
    g = p.add_argument_group("resource limits (finite numbers only)")
    for flag, typ in _LIMIT_FLAGS:
        g.add_argument(f"--{flag}", type=typ, default=None, dest=flag.replace("-", "_"))


def _cmd_reinspect(ns, limits) -> int:
    from .reinspect import reinspect_audio_packets

    name = ns.output_name or "reinspect-" + ns.expect_sha256[:12].lower()
    if not ns.reinspect:
        _emit({"mode": "dry_run", "media": os.path.basename(ns.media), "expect_sha256": ns.expect_sha256, "output": os.path.join(ns.output_root, name),
               "note": "nothing read or written; pass --reinspect to hash the media, run ffprobe and write the correction receipt"})
        return 0
    receipt = reinspect_audio_packets(ns.media, expected_sha256=ns.expect_sha256, output_root=ns.output_root, output_name=name, ffprobe=ns.ffprobe,
                                      original_receipt_path=ns.original_receipt, limits=limits)
    _emit(receipt)
    return {"REINSPECTED": 0, "SKIPPED_TOOL_UNAVAILABLE": 3}.get(receipt["status"], 4)


def _limits(ns) -> Limits:
    kw = {f: getattr(ns, f, None) for f in Limits.__dataclass_fields__}
    kw["max_input_bytes"] = None
    return Limits().replace(**kw)


def _code(status: str) -> int:
    if status in OK_STATUSES:
        return 0
    if status == rc.STATUS_SKIPPED:
        return 3
    if status == rc.STATUS_INTERRUPTED:
        return 130
    return 4


def _cmd_validate(ns, limits) -> int:
    m = load_manifest(_read(ns.manifest, limits.max_input_bytes), limits)
    _emit({"ok": True, "route": m.route, "selection_ref": m.selection_ref, "frames": m.total_frames, "fps": f"{m.fps_n}/{m.fps_d}", "audio_samples_declared": m.audio_samples,
           "note": "manifest-only: files, hashes, the WAV and the audio length rule are checked by `plan`; video properties only by the explicit --mux preflight"})
    return 0


def _kw(ns, limits):
    return dict(limits=limits, asset_root=ns.asset_root, ffmpeg=ns.ffmpeg, ffprobe=ns.ffprobe)


def _cmd_plan(ns, limits) -> int:
    p = prepare_plan(_read(ns.manifest, limits.max_input_bytes), output_root=ns.output_root, output_name=ns.output_name, **_kw(ns, limits))
    _emit(p.to_dict())
    return 0


def _cmd_mux(ns, limits) -> int:
    data = _read(ns.manifest, limits.max_input_bytes)
    if not ns.output_root:
        raise VideoCoreError("mux needs an explicit output root", [Issue("output_root_required", "output_root", "--output-root is required")])
    name = ns.output_name or "mux-" + prepare_plan(data, **_kw(ns, limits)).sha256[:12]
    if not ns.mux:
        out = prepare_plan(data, output_root=ns.output_root, output_name=name, **_kw(ns, limits)).to_dict()
        out["mode"], out["note"] = "dry_run", "nothing created and no process launched; pass --mux to run the ffprobe preflight and the mux"
        if ns.envelope is not None:
            from ..containment import unsupported_limits

            out["envelope"] = {"requested": ns.envelope.requested(), "unsupported_here": unsupported_limits(ns.envelope), "applied": "nothing: a dry run starts no process"}
        _emit(out)
        return 0
    old = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())) if hasattr(signal, "SIGTERM") else None
    try:
        receipt = mux(data, output_root=ns.output_root, output_name=name, envelope=ns.envelope, **_kw(ns, limits))
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)
    _emit(receipt)
    return _code(receipt["status"])


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in SUBCOMMANDS and args[0] not in ("-h", "--help"):
        args = ["plan"] + args
    ap = _parser()
    ns = ap.parse_args(args)
    if ns.cmd is None:
        ap.print_usage(sys.stderr)
        return 2
    try:
        limits = _limits(ns)
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    ns.envelope = None
    if ns.cmd == "mux":
        from ..containment import envelope_from_args

        try:
            ns.envelope = envelope_from_args(ns)
        except ValueError as exc:
            print(f"usage error: {exc}", file=sys.stderr)
            return 2
    try:
        return {"validate": _cmd_validate, "plan": _cmd_plan, "mux": _cmd_mux, "reinspect": _cmd_reinspect}[ns.cmd](ns, limits)
    except ToolUnavailable as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return 3
    except VideoCoreError as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return 1
    except KeyboardInterrupt:
        return 130
