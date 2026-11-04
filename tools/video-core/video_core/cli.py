"""Command line: python -m video_core [plan|validate|render|inspect|srt] ...

`plan` is the default action (`python -m video_core episode.json`). `render` only
executes with --render and an explicit --output-root; otherwise it is a dry run.

Exit codes: 0 ok | 1 rejected before launch | 2 usage | 3 skipped (tool unavailable)
            4 ran but not a READBACK_MATCH (failed/timeout/signal/unknown/mismatch) | 130 interrupted
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
import sys

from . import __version__, plan as planmod, readback, receipt as rc, tools
from .episode import load_episode
from .errors import Issue, ToolUnavailable, VideoCoreError
from .limits import Limits
from .paths import SOURCE_EXTENSIONS, check_media_for_inspect, resolve_asset
from .render import render
from .srt import export_srt

SUBCOMMANDS = ("validate", "plan", "render", "inspect", "srt")


def _emit(obj) -> None:
    sys.stdout.buffer.write(rc.dumps(obj))
    sys.stdout.buffer.flush()


def _limit_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("resource limits (lower the defaults; schema ceilings cannot be raised)")
    for name, typ in (
        ("max-frames", int), ("max-dimension", int), ("max-clips", int), ("max-captions", int), ("max-pixel-volume", int),
        ("max-duration-seconds", int), ("max-fps", int), ("max-source-bytes", int), ("max-argv-bytes", int), ("max-windows-cmdline-chars", int),
        ("max-runtime-seconds", float), ("max-output-bytes", int), ("max-diag-bytes", int),
    ):
        g.add_argument(f"--{name}", type=typ, default=None, dest=name.replace("-", "_"))


def _limits(ns) -> Limits:
    keys = [f for f in Limits.__dataclass_fields__ if getattr(ns, f, None) is not None]
    return Limits().replace(**{k: getattr(ns, k) for k in keys})


def _read_episode(path: str, limits: Limits) -> bytes:
    try:
        st = os.stat(path)
    except OSError:
        raise VideoCoreError("episode file cannot be read", [Issue("episode_unreadable", "episode", "episode file does not exist or cannot be read")]) from None
    if not stat.S_ISREG(st.st_mode):
        raise VideoCoreError("episode is not a regular file", [Issue("episode_not_regular", "episode", "episode must be a regular file")])
    if st.st_size > limits.max_input_bytes:
        raise VideoCoreError("episode too large", [Issue("limit_input_bytes", "episode", f"episode is {st.st_size} bytes; limit is {limits.max_input_bytes}")])
    with open(path, "rb") as fh:
        return fh.read(limits.max_input_bytes + 1)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m video_core", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"video_core {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="{validate,plan,render,inspect,srt}")

    def common(p, needs_assets=True):
        p.add_argument("episode", help="episode JSON file (read-only)")
        if needs_assets:
            p.add_argument("--asset-root", help="existing directory that file sources and fonts must live under")
        _limit_args(p)

    v = sub.add_parser("validate", help="validate an episode (no side effects)")
    common(v)
    pl = sub.add_parser("plan", help="validate and print the canonical plan (default action; no side effects)")
    common(pl)
    r = sub.add_parser("render", help="render into a fresh directory (requires --render; otherwise a dry run)")
    common(r)
    for p in (pl, r):
        p.add_argument("--output-root", help="existing directory under which the fresh output directory is created")
        p.add_argument("--output-name", help="name of the fresh output directory (default render-<plan hash prefix>)")
        p.add_argument("--burn-in-font", metavar="REL", help="font file (.ttf/.otf) relative to --asset-root; enables drawtext burn-in")
        p.add_argument("--font-size", type=int)
        p.add_argument("--ffmpeg", help="ffmpeg executable (default: PATH lookup)")
        p.add_argument("--ffprobe", help="ffprobe executable (default: PATH lookup)")
    r.add_argument("--render", action="store_true", help="actually execute ffmpeg (explicit opt-in)")
    i = sub.add_parser("inspect", help="read back an existing local MP4/MOV/MKV/WEBM with ffprobe (read-only)")
    i.add_argument("media")
    i.add_argument("--episode", help="compare against this episode's expected width/height/fps/frames")
    i.add_argument("--ffprobe")
    _limit_args(i)
    s = sub.add_parser("srt", help="write the deterministic UTF-8 SRT to stdout")
    common(s, needs_assets=False)
    return ap


def _status_code(status: str) -> int:
    if status == rc.STATUS_MATCH:
        return 0
    if status == rc.STATUS_SKIPPED:
        return 3
    if status == rc.STATUS_INTERRUPTED:
        return 130
    return 4


def _cmd_validate(ns, limits) -> int:
    data = _read_episode(ns.episode, limits)
    ep = load_episode(data, limits)
    checked = 0
    if ns.asset_root:
        for clip in ep.clips:
            if clip.source.kind == "file":
                resolve_asset(ns.asset_root, clip.source.path, ptr="/clips/source/path", allowed_ext=SOURCE_EXTENSIONS, max_bytes=limits.max_source_bytes)
                checked += 1
    _emit({"ok": True, "fps": planmod.ratio_str(ep.fps), "total_frames": ep.total_frames, "duration_seconds": planmod.ratio_str(ep.duration),
           "canvas": {"width": ep.width, "height": ep.height}, "clips": len(ep.clips), "captions": len(ep.captions),
           "file_sources_resolved": checked, "file_sources_unresolved": sum(1 for c in ep.clips if c.source.kind == "file") - checked})
    return 0


def _prepare_from(ns, limits, with_output: bool):
    data = _read_episode(ns.episode, limits)
    return data, dict(limits=limits, asset_root=ns.asset_root, burn_in_font=ns.burn_in_font, font_size=ns.font_size, ffmpeg=ns.ffmpeg, ffprobe=ns.ffprobe)


def _cmd_plan(ns, limits) -> int:
    data, kw = _prepare_from(ns, limits, True)
    p = planmod.prepare(data, output_root=ns.output_root, output_name=ns.output_name, **kw)
    _emit(p.to_dict())
    return 0


def _cmd_render(ns, limits) -> int:
    data, kw = _prepare_from(ns, limits, True)
    if not ns.output_root:
        raise VideoCoreError("render needs an explicit output root", [Issue("output_root_required", "output_root", "--output-root is required")])
    name = ns.output_name or "render-" + planmod.prepare(data, **kw).sha256[:12]
    if not ns.render:
        p = planmod.prepare(data, output_root=ns.output_root, output_name=name, **kw)
        out = p.to_dict()
        out["mode"] = "dry_run"
        out["note"] = "nothing executed or created; pass --render to render"
        _emit(out)
        return 0
    old = None
    if hasattr(signal, "SIGTERM"):
        old = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        receipt = render(data, output_root=ns.output_root, output_name=name, **kw)
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)
    _emit(receipt)
    return _status_code(receipt["status"])


def _cmd_inspect(ns, limits) -> int:
    media, demuxer = check_media_for_inspect(ns.media)
    exp = load_episode(_read_episode(ns.episode, limits), limits) if ns.episode else None
    path, how = tools.locate("ffprobe", ns.ffprobe)
    if path is None:
        _emit({"status": rc.STATUS_SKIPPED, "reasons": [{"tool": "ffprobe", "reason": f"ffprobe not found (discovery: {how})"}], "quality_evaluated": False})
        return 3
    cwd = os.path.dirname(media)
    rec, runs = tools.probe_version("ffprobe", path, how, limits, cwd)
    out = {"schema": "video_core.inspect/1", "quality_evaluated": False, "note": rc.QUALITY_NOTE, "tool": rec.to_dict(),
           "media_sha256": tools.sha256_file(media), "media_bytes": os.path.getsize(media)}
    if rec.problem:
        out.update(status=rc.STATUS_SKIPPED, reasons=[{"tool": "ffprobe", "reason": rec.problem}], executions=[r.to_dict() for r in runs])
        _emit(out)
        return 3
    res, parsed, raw = readback.run_probe(path, media, demuxer, limits, cwd)
    out["executions"] = [r.to_dict() for r in runs + [res]]
    if parsed is None:
        out.update(status=rc.STATUS_INSPECTION_FAILED)
        _emit(out)
        return 4
    out["summary"] = readback.summarize(parsed)
    out["ffprobe_json_sha256"] = planmod.sha256_bytes(raw)
    out["status"] = "INSPECTED"
    code = 0
    if exp is not None:
        cmp = readback.compare(out["summary"], width=exp.width, height=exp.height, fps=exp.fps, total_frames=exp.total_frames)
        out["comparison"] = cmp
        out["status"] = rc.STATUS_MATCH if cmp["all_ok"] else rc.STATUS_MISMATCH
        code = 0 if cmp["all_ok"] else 4
    _emit(out)
    return code


def _cmd_srt(ns, limits) -> int:
    ep = load_episode(_read_episode(ns.episode, limits), limits)
    sys.stdout.buffer.write(export_srt(ep))
    sys.stdout.buffer.flush()
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in SUBCOMMANDS and args[0] not in ("-h", "--help", "--version"):
        args = ["plan"] + args  # default action
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
    handler = {"validate": _cmd_validate, "plan": _cmd_plan, "render": _cmd_render, "inspect": _cmd_inspect, "srt": _cmd_srt}[ns.cmd]
    try:
        return handler(ns, limits)
    except ToolUnavailable as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return 3
    except VideoCoreError as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return 1
    except KeyboardInterrupt:
        return 130
