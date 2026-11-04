"""Owned, bounded FAKE ffmpeg / ffprobe for orchestration tests (POSIX). They emit canned listings / JSON and never decode or encode media;
every mux result produced with them is a test double, NOT mux evidence. Written independently of tests/avmux_fakes.py.

Behaviour comes from behavior.json next to the scripts. Every call is appended to calls.log (one JSON list per line).
Knobs (all bounded; the longest sleep is 8 s and the harness SIGKILLs only its own process group):
  sleep_on        {"tool": "ffmpeg"|"ffprobe", "match": substring of the argv, "marker": file to create when the sleep starts}
  mux_mode        ok | nonzero | sleep | noout | flood_stderr | delete_input | pipe_holder (needs holder_pid_file)
  delete_input    path removed by the mux step (simulates an input vanishing during the run)
  noise_bytes     deterministic stderr bytes written by the mux step (diagnostics truncation tests)
  decoded_extra   number of extra zero samples appended to the decoded audio (tolerance tests)
"""

from __future__ import annotations

import json
import os
import sys

from adv_support import PY, write_exec

_FAKE = r'''#!@@PY@@
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
beh = json.load(open(os.path.join(here, "behavior.json")))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps([name] + args) + "\n")
s = beh.get("sleep_on")
if s and s["tool"] == name and any(s["match"] in a for a in args):
    open(s["marker"], "w").write(str(os.getpid()))
    time.sleep(8)
    sys.stderr.write("fake slept to completion")
    sys.exit(0)
if "-version" in args:
    print(name + " version 0.0-adv-fake Copyright (c) test double"); sys.exit(0)
LIST = {
  "-encoders": [" A....D pcm_s16le", " A....D aac", " V....D libx264"],
  "-decoders": [" VFS..D h264", " A....D pcm_s16le", " A....D aac"],
  "-muxers":   ["  E mov", "  E mp4", "  E framemd5", "  E framehash", "  E s16le"],
  "-demuxers": [" DE mov,mp4,m4a,3gp,3g2,mj2", " D  wav", " D  image2", " D  matroska,webm"],
  "-filters":  [" ... color            |->V", " ... trim             V->V", " ... setpts           V->V", " ... scale            V->V", " ... format           V->V",
                " ... setsar           V->V", " ... concat           N->N", " ... fps              V->V", " ... pad              V->V", " ... drawtext         V->V"],
}
for flag, lines in LIST.items():
    if flag in args:
        print("\n".join(lines)); sys.exit(0)
def inpath():
    return args[len(args) - 1 - args[::-1].index("-i") + 1] if "-i" in args else ""
if name == "ffprobe":
    p = inpath()
    if "-show_packets" in args:
        print(json.dumps(beh["packets"])); sys.exit(0)
    base = os.path.basename(p)
    key = "probe_wav" if p.endswith(".wav") else ("probe_out" if base.startswith("output.") else "probe_video")
    print(json.dumps(beh[key])); sys.exit(0)
last = args[-1] if args else ""
if last.endswith(".partial"):
    mode = beh.get("mux_mode", "ok")
    run_dir = os.path.dirname(last)
    try:
        seen = json.load(open(os.path.join(run_dir, "receipt.json"))).get("status")
    except Exception as exc:
        seen = "ERR:" + repr(exc)
    open(os.path.join(run_dir, "fake_observed_receipt_status.txt"), "w").write(str(seen) + " pid=" + str(os.getpid()))
    if mode != "noout":                       # like a real muxer the partial file exists before the run ends
        with open(last, "wb") as fh:
            fh.write(b"FAKE-CONTAINER-BYTES")
    if beh.get("noise_bytes"):
        n = beh["noise_bytes"]
        sys.stderr.write(("0123456789abcdef" * (n // 16 + 1))[:n]); sys.stderr.flush()
    if mode == "pipe_holder":                 # one self-expiring (5 s) grandchild keeps the inherited pipes open after this process exits 0
        import subprocess
        subprocess.Popen([sys.executable, "-c", "import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(5)" % beh["holder_pid_file"]])
        time.sleep(0.3)
    if mode == "delete_input":
        os.remove(beh["delete_input"])
    if mode == "sleep":
        open(os.path.join(run_dir, "fake_mux_started"), "w").write(str(os.getpid()))
        time.sleep(8)
    if mode == "nonzero":
        sys.stderr.write("fatal: simulated mux failure"); sys.exit(7)
    if mode == "flood_stderr":
        chunk = b"E" * 65536
        end = time.time() + 6
        while time.time() < end:
            sys.stderr.buffer.write(chunk); sys.stderr.buffer.flush()
    sys.exit(0)
p = inpath()
src = not os.path.basename(p).startswith("output.")
if "framemd5" in args:
    sys.stdout.write(beh["framemd5_src" if src else "framemd5_out"]); sys.exit(0)
if "framehash" in args:
    sys.stdout.write(beh["framehash_src" if src else "framehash_out"]); sys.exit(0)
if "s16le" in args:
    sys.stdout.buffer.write(bytes.fromhex(beh["decoded_audio_hex"]) + b"\x00\x00" * beh.get("decoded_extra", 0)); sys.exit(0)
sys.exit(0)
'''


def install(dirpath: str, behavior: dict) -> dict:
    os.makedirs(dirpath, exist_ok=True)
    for n in ("ffmpeg", "ffprobe"):
        write_exec(os.path.join(dirpath, n), _FAKE.replace("@@PY@@", PY))
    set_behavior(dirpath, behavior)
    return {"ffmpeg": os.path.join(dirpath, "ffmpeg"), "ffprobe": os.path.join(dirpath, "ffprobe")}


def set_behavior(dirpath: str, behavior: dict) -> None:
    with open(os.path.join(dirpath, "behavior.json"), "w", encoding="utf-8") as fh:
        json.dump(behavior, fh)


def calls(dirpath: str) -> list:
    p = os.path.join(dirpath, "calls.log")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def mov_behavior(wav_pcm: bytes, *, frames: int = 3, samples: int = 4800, channels: int = 1, width: int = 64, height: int = 64, fps: str = "30/1") -> dict:
    """Canned ffprobe / hash answers for a faithful 3-frame MOV_PCM_S16LE case. Hash lines have the framemd5/framehash shape."""
    vid = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": width, "height": height, "r_frame_rate": fps, "avg_frame_rate": fps,
           "nb_read_frames": str(frames), "start_pts": 0, "start_time": "0.000000"}
    rows = "".join("0, %d, %d, 1, %d, %s\n" % (i, i, 1000 + i, ("%064x" % (i + 1))) for i in range(frames))
    md5rows = "".join("0, %d, %d, 1, 12288, %s\n" % (i, i, ("%032x" % (i + 7))) for i in range(frames))
    head = "#format: frame checksums\n#version: 2\n#hash: MD5\n#tb 0: 1/30\n#media_type 0: video\n#codec_id 0: rawvideo\n#dimensions 0: 64x64\n#sar 0: 0/1\n#stream#, dts, pts, duration, size, hash\n"
    aud_in = {"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": channels, "duration_ts": samples, "time_base": "1/48000"}
    aud_out = {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": channels, "start_pts": 0, "start_time": "0.000000",
               "time_base": "1/48000", "duration_ts": samples, "duration": "0.100000"}
    return {"probe_video": {"streams": [vid], "format": {"start_time": "0.000000", "duration": "0.100000"}},
            "probe_wav": {"streams": [aud_in], "format": {}},
            "probe_out": {"streams": [dict(vid, index=0), aud_out], "format": {"start_time": "0.000000", "duration": "0.100000"}},
            "packets": {"packets": []},
            "framemd5_src": head + md5rows, "framemd5_out": head + md5rows,
            "framehash_src": "#format: hash\n" + rows, "framehash_out": "#format: hash\n" + rows,
            "decoded_audio_hex": wav_pcm.hex()}
