#!/usr/bin/env python3
"""Create the inputs for the avmux synthetic case. Standalone: imports nothing from video_core.

usage: make_inputs.py WORK_DIR STEM_WAV --ffmpeg PATH
  * generates a NEW 3-frame, 30 fps, 160x90 H.264 clip (ffmpeg lavfi testsrc2 -> libx264) into WORK_DIR/assets/clip.mp4   (this is the ONE generation)
  * copies the existing Stage B stem (4800 samples, mono, 48 kHz s16) byte-for-byte into WORK_DIR/assets/stem.wav (it is NOT remixed)
  * writes WORK_DIR/mov.manifest.json and WORK_DIR/mp4.manifest.json (hashes + declared properties)
WORK_DIR must not exist. The generated media is not committed; only text evidence is.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

STEM_SHA256 = "68f8180e0630509d8743c11c38de674481e3f711c54c12bffa996c0af1c8ba88"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("stem")
    ap.add_argument("--ffmpeg", required=True)
    a = ap.parse_args()
    if sha(a.stem) != STEM_SHA256:
        sys.exit("the stem is not the Stage B stem (sha256 mismatch); refusing to continue")
    os.makedirs(os.path.join(a.work, "assets"))
    clip = os.path.join(a.work, "assets", "clip.mp4")
    cmd = [a.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30", "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-preset", "medium", "-crf", "18", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1", "-f", "mp4", clip]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    print(json.dumps({"video_generation_command": cmd, "exit": r.returncode, "stderr": r.stderr.decode()[:300]}))
    if r.returncode:
        sys.exit("video generation failed")
    stem = os.path.join(a.work, "assets", "stem.wav")
    shutil.copyfile(a.stem, stem)
    assert sha(stem) == STEM_SHA256
    for route, name in (("MOV_PCM_S16LE", "mov"), ("MP4_AAC", "mp4")):
        doc = {"avmux_version": 1, "route": route, "selection": {"ref": "synthetic-avmux-demo/selection-001"},
               "video": {"state": "USE", "path": "clip.mp4", "sha256": sha(clip),
                         "declared": {"codec": "h264", "width": 160, "height": 90, "fps": {"n": 30, "d": 1}, "total_frames": 3}},
               "audio": {"state": "USE", "path": "stem.wav", "sha256": STEM_SHA256, "declared": {"sample_rate": 48000, "channels": 1, "sample_count": 4800}}}
        with open(os.path.join(a.work, f"{name}.manifest.json"), "x", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
    print(json.dumps({"clip_sha256": sha(clip), "clip_bytes": os.path.getsize(clip), "stem_sha256": sha(stem)}))


if __name__ == "__main__":
    main()
