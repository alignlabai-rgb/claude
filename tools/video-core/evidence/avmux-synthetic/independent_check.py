#!/usr/bin/env python3
"""Independent re-measurement of the two muxed files. Imports NOTHING from video_core; runs its own ffmpeg/ffprobe commands (structured argv, no shell).

usage: independent_check.py --ffmpeg F --ffprobe P --video SRC.mp4 --wav STEM.wav --mov OUT.mov --mp4 OUT.mp4

MOV route (sample-exact contract): decoded video frames and copied packets equal the source's; decoded audio equals the WAV PCM sample for sample.
MP4/AAC route (lossy): the same video checks, plus HONEST observations of timing/padding: stream start, packet timing, skip/padding side data if ffprobe exposes it, decoded sample count vs
expected, and -- only as information -- how many leading samples differ. It never calls AAC sample-exact and does not evaluate quality or perceptual sync.
Exit 0 iff the MOV contract holds, the MP4 video checks hold, and the AAC decoded count is within 1024 samples of 4800.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import wave

EXPECTED_SAMPLES, TOL = 4800, 1024


def run(argv, binary=False):
    p = subprocess.run(argv, capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout if binary else p.stdout.decode("utf-8", "replace")), p.stderr.decode("utf-8", "replace")[:300]


def frame_hashes(ff, path, mode):
    tail = ["-map", "0:v:0", "-f", "framemd5", "-"] if mode == "decoded" else ["-map", "0:v:0", "-c:v", "copy", "-f", "framehash", "-hash", "sha256", "-"]
    rc, out, err = run([ff, "-v", "error", "-nostdin", "-i", path, *tail])
    rows = []
    for line in out.splitlines():
        if line and not line.startswith("#"):
            parts = [x.strip() for x in line.split(",")]
            rows.append((parts[4], parts[5]))
    return rc, rows, err


def probe(fp, path, extra):
    rc, out, err = run([fp, "-v", "error", "-of", "json", *extra, path])
    return json.loads(out) if rc == 0 and out else None


def audio_pcm(ff, path):
    rc, raw, err = run([ff, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-"], binary=True)
    return rc, raw, err


def main():
    ap = argparse.ArgumentParser()
    for k in ("ffmpeg", "ffprobe", "video", "wav", "mov", "mp4"):
        ap.add_argument(f"--{k}", required=True)
    a = ap.parse_args()
    with wave.open(a.wav) as w:
        wav_pcm = w.readframes(w.getnframes())
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()) == (1, 48000, 2, EXPECTED_SAMPLES)
    out = {"expected_samples": EXPECTED_SAMPLES, "wav_pcm_sha256": hashlib.sha256(wav_pcm).hexdigest(), "routes": {}}
    src = {m: frame_hashes(a.ffmpeg, a.video, m) for m in ("decoded", "packets")}
    out["source_video"] = {"frames_decoded": len(src["decoded"][1]), "packets": len(src["packets"][1])}
    ok_all = True
    for route, path in (("MOV_PCM_S16LE", a.mov), ("MP4_AAC", a.mp4)):
        r = {"file": path.replace("\\", "/").rsplit("/", 1)[-1]}
        checks = []

        def chk(name, expected, observed, ok):
            checks.append({"name": name, "expected": expected, "observed": observed, "ok": bool(ok)})

        for m, label in (("decoded", "decoded_video_frames_equal_source"), ("packets", "copied_video_packets_equal_source")):
            rc, rows, err = frame_hashes(a.ffmpeg, path, m)
            chk(label, {"count": 3, "equal_to_source": True}, {"exit": rc, "count": len(rows), "equal_to_source": rows == src[m][1], "stderr": err}, rc == 0 and rows == src[m][1] and len(rows) == 3)
        info = probe(a.ffprobe, path, ["-count_frames", "-show_streams", "-show_format"])
        streams = info["streams"]
        chk("two_streams_one_video_one_audio", ["audio", "video"], sorted(s["codec_type"] for s in streams), sorted(s["codec_type"] for s in streams) == ["audio", "video"])
        v = next(s for s in streams if s["codec_type"] == "video")
        au = next(s for s in streams if s["codec_type"] == "audio")
        chk("video_h264_160x90_30fps_3frames", {"codec": "h264", "size": "160x90", "fps": "30/1", "frames": "3"},
            {"codec": v["codec_name"], "size": f"{v['width']}x{v['height']}", "fps": v["r_frame_rate"], "frames": v.get("nb_read_frames")},
            (v["codec_name"], v["width"], v["height"], v["r_frame_rate"], v.get("nb_read_frames")) == ("h264", 160, 90, "30/1", "3"))
        rc, pcm, err = audio_pcm(a.ffmpeg, path)
        dec = len(pcm) // 2
        r["audio_stream"] = {k: au.get(k) for k in ("codec_name", "profile", "sample_rate", "channels", "start_pts", "start_time", "time_base", "duration_ts", "duration", "nb_frames", "bit_rate")}
        r["format"] = {k: info["format"].get(k) for k in ("format_name", "start_time", "duration", "nb_streams", "size")}
        if route == "MOV_PCM_S16LE":
            chk("audio_codec_pcm_s16le", "pcm_s16le", au["codec_name"], au["codec_name"] == "pcm_s16le")
            chk("audio_start_pts_zero_and_duration_4800_samples", {"start_pts": 0, "duration_ts": 4800, "time_base": "1/48000"}, {"start_pts": au.get("start_pts"), "duration_ts": au.get("duration_ts"), "time_base": au.get("time_base")},
                au.get("start_pts") == 0 and au.get("duration_ts") == 4800 and au.get("time_base") == "1/48000")
            chk("decoded_pcm_equals_wav_pcm_sample_for_sample", {"bytes": len(wav_pcm), "sha256": out["wav_pcm_sha256"]}, {"exit": rc, "bytes": len(pcm), "sha256": hashlib.sha256(pcm).hexdigest(), "identical": pcm == wav_pcm},
                rc == 0 and pcm == wav_pcm)
            ok_all = ok_all and all(c["ok"] for c in checks)
        else:
            chk("audio_codec_aac", "aac", au["codec_name"], au["codec_name"] == "aac")
            chk("decoded_sample_count_within_one_aac_frame_of_4800", {"expected": EXPECTED_SAMPLES, "tolerance": TOL}, {"exit": rc, "decoded": dec, "delta": dec - EXPECTED_SAMPLES}, rc == 0 and abs(dec - EXPECTED_SAMPLES) <= TOL)
            pk = probe(a.ffprobe, path, ["-select_streams", "a:0", "-show_packets"]) or {"packets": []}
            sd = [(p.get("pts"), p.get("duration"), p.get("side_data_list")) for p in pk["packets"]]
            r["aac_observations"] = {
                "decoded_samples": dec, "delta_vs_expected": dec - EXPECTED_SAMPLES, "exact_sample_count": dec == EXPECTED_SAMPLES, "sample_exact_claimed": False,
                "packet_count": len(pk["packets"]), "packets_pts_duration_side_data": sd,
                "leading_samples_identical_to_wav_if_aligned_at_0": next((i for i in range(min(dec, EXPECTED_SAMPLES) * 2) if pcm[i:i + 1] != wav_pcm[i:i + 1]), None),
                "note": "AAC is lossy: byte differences from the WAV are expected and are NOT a defect; counts, start and padding are reported as observed; no quality or perceptual-sync judgement"}
            ok_all = ok_all and all(c["ok"] for c in checks)
        r["checks"] = checks
        r["all_ok"] = all(c["ok"] for c in checks)
        out["routes"][route] = r
    out["all_ok"] = ok_all
    out["note"] = "Technical measurement of a synthetic 3-frame case only; no audio/visual/creative quality judgement."
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0 if ok_all else 4


if __name__ == "__main__":
    sys.exit(main())
