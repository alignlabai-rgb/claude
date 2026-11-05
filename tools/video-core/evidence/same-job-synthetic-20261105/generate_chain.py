#!/usr/bin/env python3
"""ONE small SYNTHETIC same-job chain through the EXISTING library APIs and the already-installed ffmpeg / ffprobe:

    audiomix validate/plan(--episode) -> audiomix mix -> video_core validate/plan/render -> avmux validate/plan/mux (MOV_PCM_S16LE, with the audio receipt) -> ffprobe + full decode of the finals

Geometry shared by every stage (so they are ONE job): 30/1 fps, 30 frames = 1 s, 48 000 samples mono 48 kHz s16, canvas 160x90.
Content: two solid-colour frames-blocks (no real IP, person or speech) and one integer square-wave tone. Nothing is downloaded, installed, called over the network or generated outside this process tree.
job / attempt / selection / binding refs are SYNTHETIC TEST ASSERTIONS: they carry no human adoption, voice qualification, authenticity or execution permission.

    python3 generate_chain.py --out <new evidence dir> [--work /tmp/vc-same-job]      (both must not exist; run once; stops at the first failing step and keeps what exists)

Run from anywhere; the library is imported by running `python3 -m video_core...` with cwd=tools/video-core. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.dirname(os.path.dirname(HERE))  # tools/video-core
RATE, FPS, FRAMES, W, H = 48000, 30, 30, 160, 90
SAMPLES = RATE * FRAMES // FPS  # 48000
SEL = "synthetic-same-job/selection-001"
BIND = "synthetic-same-job/binding-001"
DEC = "synthetic-same-job/decision-001"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tone_wav() -> bytes:
    period = RATE // 480
    pcm = b"".join(struct.pack("<h", 8000 if (i % period) < period // 2 else -8000) for i in range(SAMPLES))
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def dumps(o) -> bytes:
    return (json.dumps(o, indent=1, sort_keys=True) + "\n").encode()


class Chain:
    def __init__(self, out: str, work: str):
        self.out, self.work, self.n, self.steps = out, work, 0, []
        os.makedirs(os.path.join(out, "commands"))
        for d in ("inputs", "receipts", "media-facts"):
            os.makedirs(os.path.join(out, d))

    def put(self, rel: str, data: bytes) -> str:
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "xb") as fh:
            fh.write(data)
        return p

    def run(self, name: str, argv: list, cwd: str = None, timeout: int = 120) -> subprocess.CompletedProcess:
        self.n += 1
        tag = f"{self.n:02d}-{name}"
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": LIB, "PYTHONDONTWRITEBYTECODE": "1"}
        t0 = time.time()
        r = subprocess.run(argv, cwd=cwd or LIB, env=env, capture_output=True, timeout=timeout)
        meta = {"step": tag, "argv": [a.replace(self.work, "<WORK>") for a in argv], "cwd": "tools/video-core" if cwd is None else cwd.replace(self.work, "<WORK>"), "exit_code": r.returncode,
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "wall_seconds": round(time.time() - t0, 3), "stdout_bytes": len(r.stdout), "stderr_bytes": len(r.stderr),
                "stdout_sha256": sha(r.stdout), "stderr_sha256": sha(r.stderr)}
        self.put(f"commands/{tag}.cmd.json", dumps(meta))
        self.put(f"commands/{tag}.stdout", r.stdout.replace(self.work.encode(), b"<WORK>"))
        self.put(f"commands/{tag}.stderr", r.stderr.replace(self.work.encode(), b"<WORK>"))
        self.steps.append(meta)
        return r

    def fail(self, why: str):
        self.put("chain_result.json", dumps({"schema": "same_job_chain/1", "outcome": "STOPPED", "reason": why, "steps": self.steps}))
        print("STOPPED:", why)
        sys.exit(2)

    def need(self, r: subprocess.CompletedProcess, name: str, ok=(0,)):
        if r.returncode not in ok:
            self.fail(f"{name} exited {r.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="/tmp/vc-same-job")
    ns = ap.parse_args()
    for p in (ns.out, ns.work):
        if os.path.exists(p):
            print("refusing: exists:", p)
            return 1
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        print("ffmpeg / ffprobe not installed: nothing is downloaded or installed; reporting the shortfall")
        return 1
    ch = Chain(ns.out, ns.work)
    py = sys.executable
    versions = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.splitlines()[0] + "\n" + subprocess.run([fp, "-version"], capture_output=True, text=True).stdout.splitlines()[0] + "\n" \
        + "python " + sys.version.split()[0] + "\n"
    ch.put("versions.txt", versions.encode())
    # ---- inputs (all written once, never modified) ------------------------------------------------------------------------
    for d in ("audio/assets", "out_audio", "out_video", "out_mux", "mux_assets", "video"):
        os.makedirs(os.path.join(ns.work, d))
    wav_bytes = tone_wav()
    with open(os.path.join(ns.work, "audio/assets/tone_square_480.wav"), "xb") as fh:
        fh.write(wav_bytes)
    amanifest = {"manifest_version": 1, "timeline": {"fps": {"n": FPS, "d": 1}, "total_frames": FRAMES}, "output": {"sample_rate": RATE, "channels": 1, "bits": 16},
                 "scope": {"event_ids": ["ev_tone"]}, "policy": {"gap": "REQUIRE_FULL_COVERAGE", "overlap": "REJECT", "clipping": "REJECT"},
                 "assets": [{"id": "asset_tone", "path": "tone_square_480.wav", "sha256": sha(wav_bytes)}],
                 "events": [{"id": "ev_tone", "source_id": "synthetic_square_480hz", "state": "USE", "asset": "asset_tone", "source_in_sample": 0, "source_out_sample": SAMPLES,
                             "start_frame": 0, "end_frame": FRAMES, "decision_ref": DEC, "provenance_ref": BIND, "role": "tone"}]}
    episode = {"version": 1, "fps": {"n": FPS, "d": 1}, "canvas": {"width": W, "height": H}, "total_frames": FRAMES,
               "clips": [{"id": "block_a", "start_frame": 0, "duration_frames": 15, "source": {"kind": "color", "value": "#30506a"}},
                         {"id": "block_b", "start_frame": 15, "duration_frames": 15, "source": {"kind": "color", "value": "#6a5030"}}], "captions": []}
    am, ep = dumps(amanifest), dumps(episode)
    am_path, ep_path = os.path.join(ns.work, "audio/manifest.json"), os.path.join(ns.work, "video/episode.json")
    for p, b in ((am_path, am), (ep_path, ep)):
        with open(p, "xb") as fh:
            fh.write(b)
    ch.put("inputs/audio.manifest.json", am)
    ch.put("inputs/episode.json", ep)
    asset_root = os.path.join(ns.work, "audio/assets")
    # ---- audio: validate, plan (cross-checked against the episode), mix -----------------------------------------------------
    ch.need(ch.run("audiomix-validate", [py, "-m", "video_core.audiomix", "validate", am_path]), "audiomix validate")
    rp = ch.run("audiomix-plan", [py, "-m", "video_core.audiomix", "plan", am_path, "--asset-root", asset_root, "--episode", ep_path])
    ch.need(rp, "audiomix plan")
    rm = ch.run("audiomix-mix", [py, "-m", "video_core.audiomix", "mix", am_path, "--asset-root", asset_root, "--episode", ep_path, "--output-root", os.path.join(ns.work, "out_audio"),
                                 "--output-name", "stem-job", "--mix"])
    ch.need(rm, "audiomix mix")
    stem_dir = os.path.join(ns.work, "out_audio/stem-job")
    # ---- video: validate, plan, render (real ffmpeg) -------------------------------------------------------------------------
    ch.need(ch.run("video-validate", [py, "-m", "video_core", "validate", ep_path]), "video validate")
    ch.need(ch.run("video-plan", [py, "-m", "video_core", "plan", ep_path]), "video plan")
    rr = ch.run("video-render", [py, "-m", "video_core", "render", ep_path, "--output-root", os.path.join(ns.work, "out_video"), "--output-name", "video-job", "--render",
                                "--ffmpeg", ff, "--ffprobe", fp])
    ch.need(rr, "video render")
    vid_dir = os.path.join(ns.work, "out_video/video-job")
    # ---- mux inputs: byte copies into one asset root, then the avmux manifest pinning them ------------------------------------
    mux_assets = os.path.join(ns.work, "mux_assets")
    shutil.copyfile(os.path.join(stem_dir, "stem.wav"), os.path.join(mux_assets, "stem.wav"))
    shutil.copyfile(os.path.join(stem_dir, "receipt.json"), os.path.join(mux_assets, "stem.receipt.json"))
    shutil.copyfile(os.path.join(vid_dir, "output.mp4"), os.path.join(mux_assets, "clip.mp4"))
    rd = lambda p: open(p, "rb").read()  # noqa: E731
    stem_b, rcpt_b, clip_b = rd(os.path.join(mux_assets, "stem.wav")), rd(os.path.join(mux_assets, "stem.receipt.json")), rd(os.path.join(mux_assets, "clip.mp4"))
    mmanifest = {"avmux_version": 1, "route": "MOV_PCM_S16LE", "selection": {"ref": SEL},
                 "video": {"state": "USE", "path": "clip.mp4", "sha256": sha(clip_b), "declared": {"codec": "h264", "width": W, "height": H, "fps": {"n": FPS, "d": 1}, "total_frames": FRAMES}},
                 "audio": {"state": "USE", "path": "stem.wav", "sha256": sha(stem_b), "declared": {"sample_rate": RATE, "channels": 1, "sample_count": SAMPLES},
                           "receipt": {"path": "stem.receipt.json", "sha256": sha(rcpt_b)}}}
    mm = dumps(mmanifest)
    mm_path = os.path.join(ns.work, "mux.manifest.json")
    with open(mm_path, "xb") as fh:
        fh.write(mm)
    ch.put("inputs/mux.manifest.json", mm)
    ch.need(ch.run("avmux-validate", [py, "-m", "video_core.avmux", "validate", mm_path]), "avmux validate")
    ch.need(ch.run("avmux-plan", [py, "-m", "video_core.avmux", "plan", mm_path, "--asset-root", mux_assets]), "avmux plan")
    rx = ch.run("avmux-mux", [py, "-m", "video_core.avmux", "mux", mm_path, "--asset-root", mux_assets, "--output-root", os.path.join(ns.work, "out_mux"), "--output-name", "mux-job",
                              "--mux", "--ffmpeg", ff, "--ffprobe", fp])
    ch.need(rx, "avmux mux")
    mux_dir = os.path.join(ns.work, "out_mux/mux-job")
    # ---- keep every producer artefact UNMODIFIED (byte copies) ---------------------------------------------------------------
    keep = {"receipts/audio.receipt.json": os.path.join(stem_dir, "receipt.json"), "receipts/audio.plan.canonical.json": os.path.join(stem_dir, "plan.canonical.json"),
            "receipts/video.receipt.json": os.path.join(vid_dir, "receipt.json"), "receipts/video.plan.canonical.json": os.path.join(vid_dir, "plan.canonical.json"),
            "receipts/video.ffprobe.json": os.path.join(vid_dir, "ffprobe.json"), "receipts/mux.receipt.json": os.path.join(mux_dir, "receipt.json"),
            "receipts/mux.plan.canonical.json": os.path.join(mux_dir, "plan.canonical.json"), "receipts/mux.ffprobe.output.json": os.path.join(mux_dir, "ffprobe.output.json")}
    for rel, src in keep.items():
        ch.put(rel, rd(src))
    ch.put("receipts/audio.plan.stdout.json", rd(os.path.join(ch.out, "commands", [s for s in os.listdir(os.path.join(ch.out, "commands")) if s.endswith("audiomix-plan.stdout")][0])))
    # ---- finals: exact bytes / SHA-256 (hashlib AND sha256sum) / ffprobe / full decode ----------------------------------------
    finals = {"tone_input.wav": os.path.join(asset_root, "tone_square_480.wav"), "stem.wav": os.path.join(stem_dir, "stem.wav"), "output.mp4": os.path.join(vid_dir, "output.mp4"),
              "output.mov": os.path.join(mux_dir, "output.mov")}
    hashes = {}
    for name, p in finals.items():
        b = rd(p)
        ext = subprocess.run(["sha256sum", p], capture_output=True, text=True).stdout.split()[0]
        hashes[name] = {"bytes": len(b), "sha256": sha(b), "sha256sum_cli": ext, "agree": ext == sha(b)}
    ch.put("hashes.json", dumps(hashes))
    for name, p in finals.items():
        r = ch.run("ffprobe-" + name, [fp, "-v", "error", "-hide_banner", "-show_format", "-show_streams", "-of", "json", "-i", p])
        ch.put(f"media-facts/{name}.ffprobe.json", r.stdout)
        d = ch.run("decode-" + name, [ff, "-v", "error", "-nostdin", "-i", p, "-f", "null", "-"])
        ch.put(f"media-facts/{name}.decode.txt", f"exit={d.returncode}\nstderr_bytes={len(d.stderr)}\n".encode())
    ch.put("chain_result.json", dumps({"schema": "same_job_chain/1", "outcome": "COMPLETED", "geometry": {"fps": f"{FPS}/1", "frames": FRAMES, "seconds": FRAMES // FPS, "samples": SAMPLES, "canvas": f"{W}x{H}"},
                                       "assertions": {"selection_ref": SEL, "binding_ref": BIND, "decision_ref": DEC, "note": "synthetic test assertions only"}, "steps": ch.steps}))
    print("COMPLETED", len(ch.steps), "steps; evidence dir:", ch.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
