"""FAKE ffmpeg/ffprobe for avmux orchestration tests (POSIX scripts). They imitate only the calls avmux makes and produce no real media;
they are never render or mux evidence. Behaviour comes from behavior.json next to the scripts; every call is appended to calls.log."""

from __future__ import annotations

import json
import os
import sys

from tests.audio_fixtures import dumps, sha, tone, wav_bytes, write

PY = sys.executable

_FAKE = r'''#!{python}
import json, os, signal, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
b = json.load(open(os.path.join(here, "behavior.json")))
args = sys.argv[1:]
name = os.path.basename(sys.argv[0])
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps({{"tool": name, "argv": args, "cwd": os.getcwd(), "home": os.environ.get("HOME")}}) + "\n")
out = sys.stdout
def emit(line):
    out.write(line + "\n")
if "-version" in args:
    print(name + " version 0.0-fake Copyright (c) fake"); sys.exit(0)
def inpath():
    return args[len(args) - 1 - args[::-1].index("-i") + 1] if "-i" in args else None
def is_out(p):
    return p is not None and os.path.basename(p).startswith("output.")
if name == "ffprobe":
    p = inpath()
    if b.get("probe_exit"):
        sys.stderr.write("probe failed"); sys.exit(b["probe_exit"])
    if "-show_packets" in args:
        print(json.dumps(b["packets"])); sys.exit(0)
    if is_out(p):
        if b.get("out_probe_garbage"):
            print("not json"); sys.exit(0)
        print(json.dumps(b["probe_out"]))
    elif p.endswith(".wav"):
        print(json.dumps(b["probe_wav"]))
    else:
        print(json.dumps(b["probe_video"]))
    sys.exit(0)
for flag, key in (("-encoders", "encoders"), ("-decoders", "decoders"), ("-muxers", "muxers"), ("-demuxers", "demuxers")):
    if flag in args:
        if b.get("listing_layout") == "unknown" and flag == b.get("unknown_flag"):
            emit("Weird:"); emit("<<no entries>>"); sys.exit(0)
        if flag in ("-encoders", "-decoders"):
            emit("Encoders:" if flag == "-encoders" else "Decoders:")
            for n in b[key]: emit(" A....D " + n + "            fake")
        elif flag == "-muxers":
            emit("File formats:")
            for n in b[key]: emit("  E " + n + "            fake")
        else:
            emit("File formats:")
            for n in b[key]: emit(" D  " + n + "            fake")
        sys.exit(0)
last = args[-1] if args else ""
if last.endswith(".partial"):                       # ---- the mux itself
    mode = b.get("mode", "ok")
    run_dir = os.path.dirname(last)
    try:
        rec = json.load(open(os.path.join(run_dir, "receipt.json")))
        seen = {{"receipt_status_during_mux": rec.get("status"), "pid": os.getpid()}}
    except Exception as exc:
        seen = {{"receipt_error": repr(exc), "pid": os.getpid()}}
    with open(os.path.join(run_dir, "fake_observed.json"), "w") as fh:
        json.dump(seen, fh)
    if b.get("touch_input"):
        with open(b["touch_input"], "r+b") as fh:
            fh.write(b"X")
    if b.get("noise"):
        sys.stderr.write("E" * b["noise"] + "TAIL-MARKER"); sys.stderr.flush()
    with open(last, "wb") as fh:
        fh.write(b"" if mode == "noout" else b.get("mux_bytes", "FAKE-OUTPUT").encode())
    if mode == "nonzero":
        sys.stderr.write("fatal: simulated mux failure"); sys.exit(7)
    if mode == "sleep":
        time.sleep(60)
    if mode == "sigkill":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "orphan":
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    if mode == "noout":
        os.remove(last)
    sys.exit(0)
p = inpath()                                         # ---- decode / hash calls
src = not is_out(p)
if b.get("decode_exit") and not src:
    sys.stderr.write("decode failed"); sys.exit(b["decode_exit"])
if not src and b.get("decode_stderr"):
    sys.stderr.write(b["decode_stderr"])
if "framemd5" in args:
    sys.stdout.write(b["framemd5_src" if src else "framemd5_out"]); sys.exit(0)
if "framehash" in args:
    sys.stdout.write(b["framehash_src" if src else "framehash_out"]); sys.exit(0)
if "s16le" in args:
    sys.stdout.buffer.write(bytes.fromhex(b["decoded_audio_hex"])); sys.exit(0)
sys.stderr.write("fake: unhandled call"); sys.exit(99)
'''


def video_probe(w=160, h=90, fps="30/1", frames=3, codec="h264", start_pts=0, start_time="0.000000", extra=()):
    s = {"index": 0, "codec_type": "video", "codec_name": codec, "width": w, "height": h, "r_frame_rate": fps, "avg_frame_rate": fps,
         "nb_read_frames": str(frames), "start_pts": start_pts, "start_time": start_time, "duration": "0.100000", "time_base": "1/15360"}
    return {"streams": [s, *extra], "format": {"nb_streams": 1 + len(extra), "start_time": start_time, "duration": "0.100000"}}


def wav_probe(channels=1, samples=4800, rate="48000", codec="pcm_s16le", duration_ts=None, start_pts=None):
    s = {"index": 0, "codec_type": "audio", "codec_name": codec, "sample_rate": rate, "channels": channels,
         "duration_ts": samples if duration_ts is None else duration_ts, "time_base": "1/48000"}
    if start_pts is not None:
        s.update(start_pts=start_pts, start_time=str(start_pts))
    return {"streams": [s], "format": {"nb_streams": 1, "format_name": "wav"}}


def out_probe(route="MOV_PCM_S16LE", channels=1, samples=4800, a_start_pts=0, a_start_time="0.000000", video=None, a_codec=None, extra=(), a_duration="0.100000"):
    v = (video or video_probe())["streams"][0]
    codec = a_codec or ("pcm_s16le" if route == "MOV_PCM_S16LE" else "aac")
    a = {"index": 1, "codec_type": "audio", "codec_name": codec, "sample_rate": "48000", "channels": channels, "start_pts": a_start_pts, "start_time": a_start_time,
         "duration_ts": samples, "time_base": "1/48000", "duration": a_duration, "nb_frames": "5"}
    return {"streams": [v, a, *extra], "format": {"nb_streams": 2 + len(extra), "start_time": "0.000000", "duration": "0.100000"}}


def hash_text(hashes, sizes=28800):
    lines = ["#format: frame checksums", "#version: 2", "#stream#, dts,        pts, duration,     size, hash"]
    lines += [f"0, {i:10d}, {i:10d}, 1, {sizes:8d}, {h}" for i, h in enumerate(hashes)]
    return "\n".join(lines) + "\n"


FRAMES = [sha(bytes([i])) for i in range(3)]
PACKETS = [sha(bytes([10 + i])) for i in range(3)]


def packets_json(skip=1024, padding=None, count=6):
    pk = [{"pts": 0, "dts": -1024, "duration": 1024, "size": 300}] + [{"pts": 1024 * i, "dts": 1024 * (i - 1), "duration": 1024, "size": 290} for i in range(1, count)]
    if skip is not None:
        pk[0]["side_data_list"] = [{"side_data_type": "Skip Samples", "skip_samples": skip, "discard_padding": 0}]
    if padding is not None:
        pk[-1]["side_data_list"] = [{"side_data_type": "Skip Samples", "skip_samples": 0, "discard_padding": padding}]
    return {"packets": pk}


def default_behavior(route="MOV_PCM_S16LE", channels=1, samples=4800, pcm: bytes | None = None, **over) -> dict:
    pcm = pcm if pcm is not None else wav_bytes(tone("square", 480, samples, 8000))[44:]
    b = {"encoders": ["pcm_s16le"] if route == "MOV_PCM_S16LE" else ["aac"], "decoders": ["h264", "pcm_s16le", "aac"],
         "muxers": ["mov", "mp4", "framemd5", "framehash", "s16le"], "demuxers": ["mov", "wav"],
         "probe_video": video_probe(), "probe_wav": wav_probe(channels, samples), "probe_out": out_probe(route, channels, samples),
         "framemd5_src": hash_text(FRAMES), "framemd5_out": hash_text(FRAMES), "framehash_src": hash_text(PACKETS), "framehash_out": hash_text(PACKETS),
         "decoded_audio_hex": pcm.hex(), "packets": packets_json(), "mux_bytes": "FAKE-OUTPUT"}
    b.update(over)
    return b


def make_av_tools(directory: str, **behavior):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "behavior.json"), "w") as fh:
        json.dump(behavior, fh)
    paths = []
    for name in ("ffmpeg", "ffprobe"):
        p = os.path.join(directory, name)
        with open(p, "w") as fh:
            fh.write(_FAKE.format(python=PY))
        os.chmod(p, 0o755)
        paths.append(p)
    return tuple(paths)


def calls(directory: str) -> list:
    path = os.path.join(directory, "calls.log")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def mux_calls(directory: str) -> list:
    return [c for c in calls(directory) if c["tool"] == "ffmpeg" and c["argv"] and c["argv"][-1].endswith(".partial")]


def av_manifest(route="MOV_PCM_S16LE", *, video_bytes=b"FAKE-VIDEO", wav=None, fps=(30, 1), total=3, samples=4800, channels=1, w=160, h=90, **over) -> dict:
    wav = wav if wav is not None else wav_bytes(tone("square", 480, samples, 8000))
    doc = {"avmux_version": 1, "route": route, "selection": {"ref": "caller-selection/synthetic-1"},
           "video": {"state": "USE", "path": "clip.mp4", "sha256": sha(video_bytes), "declared": {"codec": "h264", "width": w, "height": h, "fps": {"n": fps[0], "d": fps[1]}, "total_frames": total}},
           "audio": {"state": "USE", "path": "stem.wav", "sha256": sha(wav), "declared": {"sample_rate": 48000, "channels": channels, "sample_count": samples}}}
    doc.update(over)
    return doc


def seed_inputs(assets: str, video_bytes=b"FAKE-VIDEO", wav=None, samples=4800) -> tuple[bytes, bytes]:
    wav = wav if wav is not None else wav_bytes(tone("square", 480, samples, 8000))
    write(os.path.join(assets, "clip.mp4"), video_bytes)
    write(os.path.join(assets, "stem.wav"), wav)
    return video_bytes, wav
