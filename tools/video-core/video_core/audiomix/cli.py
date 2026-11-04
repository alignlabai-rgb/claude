"""python -m video_core.audiomix [validate|plan|mix] MANIFEST ...

`plan` is the default action. `mix` only executes with --mix and an explicit --output-root; otherwise it is a dry run.
Exit codes: 0 ok (plan/validate ok, or MIXED_VERIFIED) | 1 rejected before anything was created | 2 usage |
            4 mix ran but is not MIXED_VERIFIED (clamping, clipping rejected, timeout, mismatch, unknown) |
            5 HOLD (structured, no output) | 130 cancelled/interrupted.
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
import sys
import threading

from .. import receipt as rc
from ..errors import Issue, VideoCoreError
from .limits import AudioLimits
from .manifest import load_manifest
from .mix import S_CANCELLED, S_HOLD, S_INTERRUPTED, S_VERIFIED, mix
from .plan import analyze, cross_check_episode, prepare_plan

SUBCOMMANDS = ("validate", "plan", "mix")
_LIMIT_FLAGS = (("max-events", int), ("max-assets", int), ("max-frames", int), ("max-output-samples", int), ("max-source-bytes", int),
                ("max-total-source-bytes", int), ("max-concurrent", int), ("max-mix-work", int), ("chunk-samples", int), ("max-runtime-seconds", float))


def _emit(obj) -> None:
    sys.stdout.buffer.write(rc.dumps(obj))
    sys.stdout.buffer.flush()


def _read(path: str, limit: int, what: str) -> bytes:
    try:
        st = os.stat(path)
    except OSError:
        raise VideoCoreError(f"{what} cannot be read", [Issue("input_unreadable", what, f"{what} does not exist or cannot be read")]) from None
    if not stat.S_ISREG(st.st_mode):
        raise VideoCoreError(f"{what} is not a regular file", [Issue("input_not_regular", what, f"{what} must be a regular file")])
    if st.st_size > limit:
        raise VideoCoreError(f"{what} too large", [Issue("limit_input_bytes", what, f"{what} is {st.st_size} bytes; limit is {limit}")])
    with open(path, "rb") as fh:
        return fh.read(limit + 1)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m video_core.audiomix", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", metavar="{validate,plan,mix}")
    for name, help_ in (("validate", "validate the manifest (no files read besides the manifest/episode; nothing created)"),
                        ("plan", "validate, verify the selected local assets, print the canonical plan (default; creates nothing)"),
                        ("mix", "mix a READY plan into a fresh directory (requires --mix; otherwise a dry run)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("manifest", help="audio manifest JSON (read-only)")
        p.add_argument("--episode", help="video episode JSON; the manifest timeline must match its fps and frame count")
        if name != "validate":
            p.add_argument("--asset-root", help="existing directory that the manifest's asset paths must live under")
            p.add_argument("--output-root", help="existing directory under which the fresh output directory is created")
            p.add_argument("--output-name", help="fresh output directory name (default stem-<plan hash prefix>)")
        if name == "mix":
            p.add_argument("--mix", action="store_true", help="actually mix (explicit opt-in)")
        g = p.add_argument_group("resource limits (lower the defaults; ceilings cannot be raised)")
        for flag, typ in _LIMIT_FLAGS:
            g.add_argument(f"--{flag}", type=typ, default=None, dest=flag.replace("-", "_"))
    return ap


def _limits(ns) -> AudioLimits:
    return AudioLimits().replace(**{f: getattr(ns, f, None) for f in AudioLimits.__dataclass_fields__})


def _status_code(status: str) -> int:
    if status == S_VERIFIED:
        return 0
    if status == S_HOLD:
        return 5
    if status in (S_CANCELLED, S_INTERRUPTED):
        return 130
    return 4


def _cmd_validate(ns, limits) -> int:
    data = _read(ns.manifest, limits.max_input_bytes, "manifest")
    m = load_manifest(data, limits)
    if ns.episode:
        cross_check_episode(m, _read(ns.episode, 4 * 1024 * 1024, "episode"))
    a = analyze(m, limits)
    _emit({"ok": True, "scope": list(m.scope), "events": len(m.events), "assets": len(m.assets), "total_frames": m.total_frames,
           "total_samples": a["total_samples"], "channels": m.channels, "hold_if_planned": a["hold"], "warnings": a["warnings"],
           "gap_count": len(a["gaps"]), "note": "manifest-only: asset files, hashes and WAV headers are checked by `plan`, not here"})
    return 0


def _plan_kwargs(ns, limits):
    data = _read(ns.manifest, limits.max_input_bytes, "manifest")
    ep = _read(ns.episode, 4 * 1024 * 1024, "episode") if ns.episode else None
    return data, dict(limits=limits, asset_root=ns.asset_root, episode_bytes=ep)


def _cmd_plan(ns, limits) -> int:
    data, kw = _plan_kwargs(ns, limits)
    p = prepare_plan(data, output_root=ns.output_root, output_name=ns.output_name, **kw)
    _emit(p.to_dict())
    return 5 if p.status == "HOLD" else 0


def _cmd_mix(ns, limits) -> int:
    data, kw = _plan_kwargs(ns, limits)
    if not ns.output_root:
        raise VideoCoreError("mix needs an explicit output root", [Issue("output_root_required", "output_root", "--output-root is required")])
    name = ns.output_name or "stem-" + prepare_plan(data, **kw).sha256[:12]
    if not ns.mix:
        out = prepare_plan(data, output_root=ns.output_root, output_name=name, **kw).to_dict()
        out["mode"], out["note"] = "dry_run", "nothing created; pass --mix to mix"
        _emit(out)
        return 0
    cancel = threading.Event()
    old = signal.signal(signal.SIGTERM, lambda *_: cancel.set()) if hasattr(signal, "SIGTERM") else None
    try:
        receipt = mix(data, output_root=ns.output_root, output_name=name, cancel=cancel.is_set, **kw)
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)
    _emit(receipt)
    return _status_code(receipt["status"])


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
    try:
        return {"validate": _cmd_validate, "plan": _cmd_plan, "mix": _cmd_mix}[ns.cmd](ns, limits)
    except VideoCoreError as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return 1
    except KeyboardInterrupt:
        return 130
