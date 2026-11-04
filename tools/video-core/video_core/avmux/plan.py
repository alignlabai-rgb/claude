"""AV mux plan: pure, side-effect-free.

`prepare_plan` creates no file and launches no process. It reads the manifest, stats/hashes the two selected local files and parses the
WAV with the strict stdlib reader (audiomix.wav). It does NOT run ffprobe, so the video's codec/size/rate/frame-count/start time are only
what the caller *declared*: the plan labels them CALLER_STATED_UNVERIFIED and never reports verified readiness. The explicit --mux
preflight measures them with a bounded ffprobe and rejects mismatches before any directory is created or the mux is launched.

Audio length rule: the declared sample count must equal audiomix's boundary policy for the timeline,
    sample(total_frames) = floor((2*frames*fps_d*48000 + fps_n) / (2*fps_n)),
and must equal the real WAV's sample count. Nothing is padded, trimmed, stretched, resampled or hidden with -shortest.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from ..audiomix import wav
from ..audiomix.limits import AudioLimits
from ..audiomix.plan import frame_to_sample
from ..episode import parse_json_bytes
from ..errors import Issue, UnsafePath, ValidationFailed
from ..limits import WIN_CMDLINE_HARD_LIMIT, Limits, check_argv_budget
from ..paths import check_output_location, resolve_asset
from ..plan import estimate_argv
from ..tools import locate, sha256_file
from .manifest import VIDEO_EXT, AvManifest, load_manifest
from .receipt_check import qualify_audio_receipt

PLAN_SCHEMA = "video_core.avmux_plan/1"
AAC_FRAME_SAMPLES = 1024
AAC_TOLERANCE_SAMPLES = 1024  # chosen, documented tolerance: one AAC frame (not derived from any standard)
ROUTE_CONTAINER = {"MOV_PCM_S16LE": "mov", "MP4_AAC": "mp4"}
PLAN_NAME = "plan.canonical.json"
RECEIPT_NAME = "receipt.json"
PROBE_OUT_NAME = "ffprobe.output.json"
NOTE_ASSERT = "selection.ref and every declared value are caller assertions: preserved verbatim, not authenticated, not evidence of creative adoption"


def output_names(route: str) -> tuple[str, str, str]:
    ext = ROUTE_CONTAINER[route]
    return f"output.{ext}", f"output.{ext}.partial", f"output.{ext}.mismatch"


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _reject(code, ptr, msg, cls=ValidationFailed):
    raise cls(msg, [Issue(code, ptr, msg)])


def mux_argv_template(route: str, vpath: str, apath: str, limits: Limits) -> list:
    """Structured argv. No -shortest, no -t, no -r/-vsync/-fps_mode, no -ar/-af/aresample, no -ss: nothing can hide a mismatch."""
    _final, partial, _mm = output_names(route)
    a = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-n",
         "-protocol_whitelist", "file", "-f", "mov", "-i", {"asset": vpath},
         "-protocol_whitelist", "file", "-f", "wav", "-i", {"asset": apath},
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
    if route == "MOV_PCM_S16LE":
        a += ["-c:a", "pcm_s16le"]
    else:
        a += ["-c:a", "aac", "-b:a", "128k"]
    a += ["-map_metadata", "-1", "-fflags", "+bitexact"]
    if route == "MP4_AAC":
        a += ["-movflags", "+faststart"]
    a += ["-fs", str(limits.max_output_bytes), "-f", ROUTE_CONTAINER[route], {"run_file": partial}]
    return a


def pcm_sha256(path: str, info: wav.WavInfo) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        fh.seek(info.data_offset)
        left = info.data_size
        while left:
            chunk = fh.read(min(1 << 20, left))
            if not chunk:
                raise OSError("short read of the WAV data chunk")
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


class AvPlan:
    def __init__(self, manifest, canonical, cj, observations, pcm_hash, expected_samples, video_file, audio_file, wav_info):
        self.manifest, self.canonical, self.canonical_json = manifest, canonical, cj
        self.sha256 = hashlib.sha256(cj).hexdigest()
        self.observations, self.pcm_sha256, self.expected_samples = observations, pcm_hash, expected_samples
        self.video_file, self.audio_file, self.wav_info = video_file, audio_file, wav_info

    def to_dict(self) -> dict:
        return {"mode": "plan", "plan_sha256": self.sha256, "readiness": self.canonical["readiness"], "plan": self.canonical, "observations": self.observations}


def _check_audio_receipt(m: AvManifest, asset_root: str, limits: Limits, *, stem_bytes: int, stem_channels: int) -> dict:
    """Read the pinned receipt with the strict JSON loader and qualify it mechanically (see receipt_check). Unauthenticated: the file is
    pinned by the SHA-256 in the same manifest, which proves which bytes were read and nothing about who wrote or approved them."""
    rf = resolve_asset(asset_root, m.receipt_path, ptr="/audio/receipt/path", allowed_ext=(".json",), max_bytes=limits.max_input_bytes)
    actual = sha256_file(rf.abspath, limits.max_input_bytes)
    if actual != m.receipt_sha256:
        _reject("audio_receipt_sha256_mismatch", "/audio/receipt/sha256", f"receipt sha256 {m.receipt_sha256} but the file hashes to {actual}")
    with open(rf.abspath, "rb") as fh:
        raw = fh.read(limits.max_input_bytes + 1)
    try:
        rec = parse_json_bytes(raw, limits)  # duplicate keys, NaN/Infinity, BOM, bad UTF-8, runaway nesting, huge integers -> ValidationFailed
    except ValidationFailed as exc:
        why = exc.issues[0].code if exc.issues else "json_invalid"
        _reject("audio_receipt_unreadable", "/audio/receipt/path", f"audio receipt is not strict JSON ({why})")
    facts = qualify_audio_receipt(rec, stem_sha256=m.audio_sha256, stem_bytes=stem_bytes, samples=m.audio_samples, channels=stem_channels)
    return {"path": m.receipt_path, "sha256": actual, **facts}


def prepare_plan(manifest_bytes: bytes, *, limits: Limits = Limits(), asset_root: str | None = None, output_root: str | None = None,
                 output_name: str | None = None, ffmpeg: str | None = None, ffprobe: str | None = None) -> AvPlan:
    m = load_manifest(manifest_bytes, limits)
    if asset_root is None:
        _reject("asset_root_required", "asset_root", "a caller-selected asset root is required")
    fps = Fraction(m.fps_n, m.fps_d)
    if fps > limits.max_fps:
        _reject("limit_fps", "/video/declared/fps", f"frame rate {fps} exceeds the limit {limits.max_fps}")
    expected = frame_to_sample(m.total_frames, m.fps_n, m.fps_d)
    if m.audio_samples != expected:
        _reject("audio_sample_count_mismatch", "/audio/declared/sample_count",
                f"declared {m.audio_samples} samples but {m.total_frames} frames at {m.fps_n}/{m.fps_d} fps are {expected} samples under the audio boundary policy "
                f"(difference {m.audio_samples - expected}); audio is never padded, trimmed or stretched")

    vf = resolve_asset(asset_root, m.video_path, ptr="/video/path", allowed_ext=VIDEO_EXT, max_bytes=limits.max_source_bytes)
    af = resolve_asset(asset_root, m.audio_path, ptr="/audio/path", allowed_ext=(".wav",), max_bytes=limits.max_source_bytes)
    try:
        vsha, asha = sha256_file(vf.abspath, limits.max_source_bytes), sha256_file(af.abspath, limits.max_source_bytes)
    except (OSError, ValueError) as exc:
        _reject("asset_unreadable", "assets", f"an input could not be hashed: {type(exc).__name__}", UnsafePath)
    if vsha != m.video_sha256:
        _reject("video_sha256_mismatch", "/video/sha256", f"declared sha256 {m.video_sha256} but the file hashes to {vsha}")
    if asha != m.audio_sha256:
        _reject("audio_sha256_mismatch", "/audio/sha256", f"declared sha256 {m.audio_sha256} but the file hashes to {asha}")
    with open(af.abspath, "rb") as fh:
        info = wav.parse_wav(fh, af.size, AudioLimits(), ptr="/audio/path")
    if info.channels != m.audio_channels:
        _reject("audio_channels_mismatch", "/audio/declared/channels", f"declared {m.audio_channels} channel(s) but the WAV has {info.channels}")
    if info.sample_count != m.audio_samples:
        _reject("audio_sample_count_mismatch", "/audio/declared/sample_count", f"declared {m.audio_samples} samples but the WAV really has {info.sample_count}")
    receipt = _check_audio_receipt(m, asset_root, limits, stem_bytes=af.size, stem_channels=info.channels) if m.receipt_path else None
    pcm_hash = pcm_sha256(af.abspath, info)

    location = None
    if output_root is not None or output_name is not None:
        if output_root is None or output_name is None:
            _reject("output_incomplete", "output_root", "output_root and output_name must be given together")
        location = check_output_location(output_root, output_name, asset_root)

    final, partial, mismatch = output_names(m.route)
    requirements = {
        "encoders": ["pcm_s16le"] if m.route == "MOV_PCM_S16LE" else ["aac"],
        "decoders": sorted({"h264", "pcm_s16le"} if m.route == "MOV_PCM_S16LE" else {"h264", "aac"}),
        "muxers": sorted({ROUTE_CONTAINER[m.route], "framemd5", "framehash", "s16le"}),
        "demuxers": ["mov", "wav"],
    }
    dur_v, dur_a = Fraction(m.total_frames, 1) / fps, Fraction(expected, 48_000)
    contract = ({"id": "MOV_PCM_S16LE:sample_exact_pcm", "sample_exact": True,
                 "requires": ["decoded video frame hashes equal the source's", "copied video packet hashes equal the source's",
                              "decoded audio is byte-identical to the WAV PCM (sha256 and byte count)", "audio stream starts at pts 0", "exactly one video and one audio stream"]}
                if m.route == "MOV_PCM_S16LE" else
                {"id": "MP4_AAC:lossy_within_tolerance", "sample_exact": False, "tolerance_samples": AAC_TOLERANCE_SAMPLES,
                 "requires": ["decoded video frame hashes equal the source's", "copied video packet hashes equal the source's",
                              f"decoded AAC sample count within {AAC_TOLERANCE_SAMPLES} samples of the declared count (exact match is reported, never assumed)",
                              f"audio stream start within {AAC_TOLERANCE_SAMPLES} samples of 0", "exactly one video and one audio stream"],
                 "never_claimed": ["sample-exact or bit-exact audio", "perceptual sync or audio quality"],
                 "observed_not_hidden": ["encoder delay / start offset", "packet skip-samples and discard-padding side data when ffprobe reports it", "decoded vs declared sample counts"]})
    canonical = {
        "schema": PLAN_SCHEMA, "route": m.route, "container": ROUTE_CONTAINER[m.route],
        "selection": {"ref": m.selection_ref, "note": NOTE_ASSERT},
        "timeline": {"fps": {"n": m.fps_n, "d": m.fps_d, "rational": f"{fps.numerator}/{fps.denominator}"}, "total_frames": m.total_frames,
                     "expected_audio_samples": expected, "boundary_policy": "audiomix frame_to_sample: floor((2*frame*fps_d*48000 + fps_n)/(2*fps_n))",
                     "video_duration_seconds": f"{dur_v.numerator}/{dur_v.denominator}", "audio_duration_seconds": f"{dur_a.numerator}/{dur_a.denominator}"},
        "video": {"path": m.video_path, "sha256": vsha, "bytes": vf.size, "demuxer": VIDEO_EXT[vf.ext],
                  "declared": {"codec": "h264", "width": m.width, "height": m.height, "frames": m.total_frames, "start_pts": 0},
                  "properties_basis": "CALLER_STATED_UNVERIFIED", "stream_handling": "copy (-c:v copy): no re-encode, no frame dropped or rescaled"},
        "audio": {"path": m.audio_path, "sha256": asha, "bytes": af.size, "pcm_sha256": pcm_hash,
                  "verified_from_file": {"sample_rate": info.sample_rate, "channels": info.channels, "bits": info.bits, "sample_count": info.sample_count},
                  "receipt": receipt, "stream_handling": "pcm_s16le copy-through" if m.route == "MOV_PCM_S16LE" else "aac encode (native encoder, s16->fltp format conversion only; no resample/stretch)"},
        "ffmpeg": {"argv": mux_argv_template(m.route, m.video_path, m.audio_path, limits), "cwd": "tmp",
                   "output": {"partial": partial, "final": final, "mismatch": mismatch}},
        "requirements": requirements, "contract": contract,
        "outputs": {"media": final, "plan": PLAN_NAME, "output_probe": PROBE_OUT_NAME, "receipt": RECEIPT_NAME},
        "readiness": "UNVERIFIED: video properties are caller-stated until the explicit --mux preflight measures them with ffprobe",
        "verdicts": {"technical": "decided only after a mux run (see receipt)", "quality_evaluated": False, "creative_acceptance": "NOT_EVALUATED"},
    }
    cj = canonical_bytes(canonical)
    ff, how_ff = locate("ffmpeg", ffmpeg)
    fp, how_fp = locate("ffprobe", ffprobe)
    import os
    est = estimate_argv(canonical["ffmpeg"]["argv"], os.path.realpath(asset_root), location.run_dir if location else None, ff)
    budget = check_argv_budget(est, limits)
    obs = {"input": {"manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "manifest_bytes": len(manifest_bytes)},
           "output": None if location is None else {"root": location.root, "name": location.name, "fresh": True},
           "tools_located": {"ffmpeg": {"path": ff, "discovery": how_ff}, "ffprobe": {"path": fp, "discovery": how_fp}},
           "argv_budget": {"estimated_argv_bytes": budget["argv_bytes"], "estimated_windows_cmdline_utf16_units": budget["windows_cmdline_utf16_units"],
                           "windows_limit_utf16_units": limits.max_windows_cmdline_chars, "windows_hard_limit_utf16_units": WIN_CMDLINE_HARD_LIMIT,
                           "fits_windows_limit": budget["windows_cmdline_utf16_units"] <= limits.max_windows_cmdline_chars, "ill_formed_utf16": budget["ill_formed_utf16"]},
           "limits": limits.to_dict(), "side_effects": "none (no file created, no process launched; ffprobe is NOT run by plan)"}
    return AvPlan(m, canonical, cj, obs, pcm_hash, expected, vf, af, info)
