"""Plan construction: validated episode -> canonical, hashed, machine-independent plan.

Side-effect free: reads the episode bytes it is given, stats/reads asset and font
files, and *looks up* tool paths; it never creates a file, never executes a
process and never touches the network.

The canonical plan contains no absolute paths. The ffmpeg argv is a template whose
elements are plain strings or placeholders ({"asset": rel} for a validated input
under the asset root, {"run_file": name} for a file under the fresh output
directory). `materialize()` substitutes real paths only at render time. All
dynamic text that reaches the filtergraph is generated here from integers and
fixed names; caption text and file names never enter the filtergraph or a shell.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from . import fontcheck, tools
from .episode import ColorSource, Episode, FileSource, load_episode
from .errors import Issue, UnsafePath, ValidationFailed
from .limits import Limits
from .paths import FONT_EXTENSIONS, SOURCE_EXTENSIONS, ResolvedFile, check_output_location, resolve_asset
from .srt import export_srt
from .timebase import frame_to_srt, frame_to_seconds, ratio_str

PLAN_SCHEMA = "video_core.plan/1"
OUTPUT_FINAL = "output.mp4"
OUTPUT_PARTIAL = "output.mp4.partial"
SRT_NAME = "captions.srt"
RECEIPT_NAME = "receipt.json"
PLAN_NAME = "plan.canonical.json"
PROBE_NAME = "ffprobe.json"
FONT_TMP_NAME = "font.ttf"


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Plan:
    episode: Episode
    canonical: dict
    canonical_json: bytes
    sha256: str
    srt: bytes
    assets: list  # unique ResolvedFile, in first-use order
    font: ResolvedFile | None
    font_bytes: bytes | None
    caption_files: dict  # tmp file name -> bytes
    episode_sha256: str
    episode_size: int
    observations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": "plan",
            "plan_sha256": self.sha256,
            "plan": self.canonical,
            "observations": self.observations,
        }


def _argv_bytes(argv: list) -> int:
    total = 0
    for el in argv:
        if isinstance(el, str):
            total += len(el.encode("utf-8")) + 1
        else:  # placeholder: assume a generous absolute-path length
            total += len(next(iter(el.values())).encode("utf-8")) + 1 + 1024
    return total


def _clip_filter(index: int, clip, ep: Episode, input_index: int | None) -> str:
    fps = f"{ep.fps_n}/{ep.fps_d}"
    w, h, n = ep.width, ep.height, clip.duration_frames
    src = clip.source
    if isinstance(src, ColorSource):
        head = f"color=c=0x{src.value[1:]}:s={w}x{h}:r={fps}"
        body = f"{head},trim=end_frame={n},setpts=PTS-STARTPTS,scale=out_color_matrix=bt709:out_range=tv,setsar=1,format=yuv420p"
    else:
        body = (
            f"[{input_index}:v:0]fps=fps={fps},trim=end_frame={n},setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:out_color_matrix=bt709:out_range=tv,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
        )
    return f"{body}[v{index}]"


def _assert_filter_safe(graph: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:=,;[]()/-+*@' ")
    bad = sorted({ch for ch in graph if ch not in allowed})
    if bad:  # defence in depth: the graph is built only from ints, hex and fixed names
        raise AssertionError(f"unexpected characters in generated filtergraph: {bad!r}")


def build_canonical(ep: Episode, assets: dict, font: ResolvedFile | None, font_sha: str | None, font_size: int | None,
                    srt: bytes, limits: Limits) -> tuple[dict, dict]:
    """Return (canonical plan dict, caption_files {name: bytes})."""
    fps_s = ratio_str(ep.fps)
    clips_out, inputs, input_args, graph_parts = [], [], [], []
    for i, clip in enumerate(ep.clips):
        entry = {
            "id": clip.id,
            "start_frame": clip.start_frame,
            "end_frame": clip.end_frame,
            "duration_frames": clip.duration_frames,
            "start_seconds": ratio_str(frame_to_seconds(clip.start_frame, ep.fps)),
            "end_seconds": ratio_str(frame_to_seconds(clip.end_frame, ep.fps)),
        }
        input_index = None
        if isinstance(clip.source, ColorSource):
            entry["source"] = {"kind": "color", "value": clip.source.value}
        else:
            kind, demuxer = SOURCE_EXTENSIONS[assets[clip.source.path].ext]
            input_index = len(inputs)
            inputs.append((clip.source.path, kind, demuxer))
            entry["source"] = {"kind": "file", "path": clip.source.path, "media": kind, "demuxer": demuxer}
            args = ["-protocol_whitelist", "file", "-f", demuxer]
            if kind == "image":
                args += ["-pattern_type", "none", "-loop", "1", "-framerate", f"{ep.fps_n}/{ep.fps_d}"]
            input_args.extend(args + ["-i", {"asset": clip.source.path}])
        clips_out.append(entry)
        graph_parts.append(_clip_filter(i, clip, ep, input_index))

    n = len(ep.clips)
    labels = "".join(f"[v{i}]" for i in range(n))
    caption_files: dict[str, bytes] = {}
    captions_out = []
    for i, cap in enumerate(ep.captions):
        captions_out.append({
            "id": cap.id, "start_frame": cap.start_frame, "end_frame": cap.end_frame, "text": cap.text,
            "start_seconds": ratio_str(frame_to_seconds(cap.start_frame, ep.fps)),
            "end_seconds": ratio_str(frame_to_seconds(cap.end_frame, ep.fps)),
            "srt_start": frame_to_srt(cap.start_frame, ep.fps),
            "srt_end": frame_to_srt(cap.end_frame, ep.fps),
        })

    burn_in = None
    if font is not None:
        order = sorted(range(len(ep.captions)), key=lambda i: (ep.captions[i].start_frame, ep.captions[i].end_frame, i))
        draws, burn_caps = [], []
        for k, idx in enumerate(order, start=1):
            cap = ep.captions[idx]
            name = f"cap_{k:04d}.txt"
            caption_files[name] = cap.text.encode("utf-8")
            enable = f"between(n,{cap.start_frame},{cap.end_frame - 1})"  # end_frame is exclusive
            draws.append(
                f"drawtext=fontfile={FONT_TMP_NAME}:textfile={name}:expansion=none:fontsize={font_size}:fontcolor=white"
                f":box=1:boxcolor=black@0.5:boxborderw=8:x=(w-text_w)/2:y=h-text_h-h/12:enable='{enable}'"
            )
            burn_caps.append({"caption_id": cap.id, "file": name, "sha256": sha256_bytes(caption_files[name]), "enable": enable})
        graph_parts.append(f"{labels}concat=n={n}:v=1:a=0[vcat]")
        graph_parts.append("[vcat]" + ",".join(draws) + "[vout]" if draws else "[vcat]null[vout]")
        burn_in = {"font": {"asset": font.rel, "sha256": font_sha, "tmp_name": FONT_TMP_NAME}, "font_size": font_size,
                   "filter": "drawtext", "text_via": "textfile (never in argv/filtergraph)", "captions": burn_caps}
    else:
        graph_parts.append(f"{labels}concat=n={n}:v=1:a=0[vout]")
    graph = ";".join(graph_parts)
    _assert_filter_safe(graph)

    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-n"]
    argv += input_args
    argv += ["-filter_complex", graph, "-map", "[vout]", "-an", "-sn", "-dn",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-fps_mode", "cfr", "-r", f"{ep.fps_n}/{ep.fps_d}", "-frames:v", str(ep.total_frames),
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart",
             "-fs", str(limits.max_output_bytes), "-f", "mp4", {"run_file": OUTPUT_PARTIAL}]
    if _argv_bytes(argv) > limits.max_argv_bytes:
        raise ValidationFailed("ffmpeg argument list too large",
                               [Issue("limit_argv_bytes", "/clips", f"generated ffmpeg arguments exceed {limits.max_argv_bytes} bytes")])

    filters = ["color", "trim", "setpts", "scale", "format", "setsar", "concat"]
    if inputs:
        filters += ["fps", "pad"]
    if burn_in:
        filters.append("drawtext")
    requirements = {
        "encoders": ["libx264"],
        "filters": sorted(set(filters)),
        "demuxers": sorted({d for _p, _k, d in inputs}),
    }
    canonical = {
        "schema": PLAN_SCHEMA,
        "episode": {
            "fps": {"n": ep.fps_n, "d": ep.fps_d, "rational": fps_s},
            "canvas": {"width": ep.width, "height": ep.height},
            "total_frames": ep.total_frames,
            "duration_seconds": ratio_str(ep.duration),
        },
        "clips": clips_out,
        "captions": captions_out,
        "srt": {"file": SRT_NAME, "sha256": sha256_bytes(srt), "bytes": len(srt), "end_is_exclusive": True},
        "burn_in": burn_in,
        "requirements": requirements,
        "ffmpeg": {"argv": argv, "cwd": "tmp", "output": {"partial": OUTPUT_PARTIAL, "final": OUTPUT_FINAL}},
        "expected_readback": {
            "width": ep.width, "height": ep.height, "fps": fps_s, "frames": ep.total_frames,
            "duration_seconds": ratio_str(ep.duration), "codec": "h264", "pix_fmt": "yuv420p", "streams": "1 video, 0 other",
        },
        "outputs": {"video": OUTPUT_FINAL, "srt": SRT_NAME, "plan": PLAN_NAME, "ffprobe": PROBE_NAME, "receipt": RECEIPT_NAME},
    }
    return canonical, caption_files


def prepare(episode_bytes: bytes, *, limits: Limits = Limits(), asset_root: str | None = None,
            output_root: str | None = None, output_name: str | None = None,
            burn_in_font: str | None = None, font_size: int | None = None,
            ffmpeg: str | None = None, ffprobe: str | None = None) -> Plan:
    """Validate everything that can be validated without side effects and build the plan."""
    ep = load_episode(episode_bytes, limits)

    wanted = []  # unique file sources in first-use order
    for clip in ep.clips:
        if isinstance(clip.source, FileSource) and clip.source.path not in wanted:
            wanted.append(clip.source.path)
    if (wanted or burn_in_font) and asset_root is None:
        raise ValidationFailed("asset root required", [Issue("asset_root_required", "asset_root", "file sources and fonts require a caller-selected asset root")])
    assets = {}
    for rel in wanted:
        assets[rel] = resolve_asset(asset_root, rel, ptr="/clips/source/path", allowed_ext=SOURCE_EXTENSIONS, max_bytes=limits.max_source_bytes)

    font = font_bytes = font_sha = None
    if burn_in_font is not None:
        font = resolve_asset(asset_root, burn_in_font, ptr="burn_in_font", allowed_ext=FONT_EXTENSIONS, max_bytes=limits.max_font_bytes)
        with open(font.abspath, "rb") as fh:
            font_bytes = fh.read(limits.max_font_bytes + 1)
        if len(font_bytes) > limits.max_font_bytes:
            raise UnsafePath("font too large", [Issue("limit_font_bytes", "burn_in_font", "font exceeds the size limit")])
        font_sha = sha256_bytes(font_bytes)
        fontcheck.require_glyph_coverage(font_bytes, [c.text for c in ep.captions], "burn_in_font")
        if font_size is None:
            font_size = max(12, ep.height // 18)
        elif isinstance(font_size, bool) or not isinstance(font_size, int) or not 8 <= font_size <= ep.height:
            raise ValidationFailed("bad font size", [Issue("font_size_invalid", "font_size", "font size must be an integer between 8 and the canvas height")])

    location = None
    if output_root is not None or output_name is not None:
        if output_root is None or output_name is None:
            raise ValidationFailed("output location incomplete", [Issue("output_incomplete", "output_root", "output_root and output_name must be given together")])
        location = check_output_location(output_root, output_name, asset_root)

    srt = export_srt(ep)
    canonical, caption_files = build_canonical(ep, assets, font, font_sha, font_size, srt, limits)
    cj = canonical_bytes(canonical)
    plan = Plan(
        episode=ep, canonical=canonical, canonical_json=cj, sha256=sha256_bytes(cj), srt=srt,
        assets=[assets[r] for r in wanted], font=font, font_bytes=font_bytes, caption_files=caption_files,
        episode_sha256=sha256_bytes(episode_bytes), episode_size=len(episode_bytes),
    )
    ff, how_ff = tools.locate("ffmpeg", ffmpeg)
    fp, how_fp = tools.locate("ffprobe", ffprobe)
    plan.observations = {
        "input": {"episode_sha256": plan.episode_sha256, "episode_bytes": len(episode_bytes)},
        "assets": [{"path": a.rel, "bytes": a.size} for a in plan.assets],
        "output": None if location is None else {"root": location.root, "name": location.name, "fresh": True},
        "tools_located": {"ffmpeg": {"path": ff, "discovery": how_ff}, "ffprobe": {"path": fp, "discovery": how_fp}},
        "limits": limits.to_dict(),
        "side_effects": "none (no files created, no process launched by plan)",
    }
    return plan


def materialize(plan: Plan, *, ffmpeg_path: str, asset_root: str | None, run_dir: str) -> list[str]:
    """Substitute real paths into the argv template (render time only)."""
    out = []
    for el in plan.canonical["ffmpeg"]["argv"]:
        if isinstance(el, str):
            out.append(el)
        elif "asset" in el:
            out.append(os.path.join(asset_root, *el["asset"].split("/")))
        else:
            out.append(os.path.join(run_dir, el["run_file"]))
    out[0] = ffmpeg_path
    return out
