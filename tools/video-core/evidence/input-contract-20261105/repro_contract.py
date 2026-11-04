#!/usr/bin/env python3
"""Before/after reproducer for the five confirmed input/diagnostic-boundary defects (F-CLI-1, F-AUD-1, F-REC-1, F-REC-2, F-ENC-1).

It RECORDS observations as JSON (it never fails on a defect, so the same file can be run on the unfixed and the fixed tree):

    python3 repro_contract.py OUT.json

Everything is synthetic and owned: a throw-away temp directory, tiny hand-built WAV bytes, no real media, no network. Child processes are
interpreter-only (`python -m video_core...`, <= 10 s each) and run with PATH pointing at marker scripts named ffmpeg/ffprobe; any launch of
one of them is recorded (`media_process_launched`). Stdlib + video_core only. Source commit and interpreter go into the result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from video_core import errors  # noqa: E402
from video_core.audiomix import mix as amix, plan as aplan  # noqa: E402
from video_core.avmux import plan as vplan  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "episode.synthetic.json")
BASELINE_PLAN = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"
TIMEOUT = 10


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def jb(doc) -> bytes:
    try:
        return json.dumps(doc, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(doc, ensure_ascii=True).encode("ascii")


def wav(frames) -> bytes:
    pcm = b"".join(struct.pack("<h", v) for v in frames)
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 96000, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tone(n=4800, seed=77):
    return [((i * seed) % 2000) - 1000 for i in range(n)]


# ---------------------------------------------------------------------------------------------------------------- documents
def audio_manifest(*, fps=(30, 1), total=30, events=(), scope=None, assets=(), extra_ref=None):
    doc = {"manifest_version": 1, "timeline": {"fps": {"n": fps[0], "d": fps[1]}, "total_frames": total},
           "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
           "scope": {"event_ids": list(scope) if scope is not None else [e["id"] for e in events]},
           "policy": {"gap": "ALLOW_ZERO_FILL"}, "assets": list(assets), "events": list(events)}
    return doc


def avmux_manifest(*, wav_bytes=b"", samples=4800, receipt=None, ref="caller-selection/synthetic-1"):
    d = {"avmux_version": 1, "route": "MOV_PCM_S16LE", "selection": {"ref": ref},
         "video": {"state": "USE", "path": "v.mp4", "sha256": sha(b"synthetic-not-a-video"),
                   "declared": {"codec": "h264", "width": 64, "height": 64, "fps": {"n": 30, "d": 1}, "total_frames": 3}},
         "audio": {"state": "USE", "path": "a.wav", "sha256": sha(wav_bytes),
                   "declared": {"sample_rate": 48000, "channels": 1, "sample_count": samples}}}
    if receipt is not None:
        d["audio"]["receipt"] = receipt
    return d


# ---------------------------------------------------------------------------------------------------------------- CLI harness
class Sandbox:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="vc-input-contract-")
        self.bin = os.path.join(self.dir, "markerbin")
        self.cwd = os.path.join(self.dir, "cwd")
        self.marker = os.path.join(self.dir, "LAUNCHED")
        os.mkdir(self.bin)
        os.mkdir(self.cwd)
        for name in ("ffmpeg", "ffprobe"):
            p = os.path.join(self.bin, name)
            with open(p, "w") as fh:
                fh.write(f"#!/bin/sh\necho {name} >> '{self.marker}'\nexit 9\n")
            os.chmod(p, 0o755)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def write(self, name, data: bytes) -> str:
        p = self.path(name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def tree(self, root=None):
        out = []
        for base, dirs, files in os.walk(root or self.cwd):
            out += [os.path.relpath(os.path.join(base, n), root or self.cwd) for n in dirs + files]
        return sorted(out)

    def run(self, module, args, *, cwd=None):
        env = {"PATH": self.bin + os.pathsep + "/usr/bin:/bin", "PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1", "HOME": self.dir}
        before = self.tree(cwd)
        try:
            p = subprocess.run([sys.executable, "-m", module, *args], capture_output=True, cwd=cwd or self.cwd, env=env, timeout=TIMEOUT)
            rc, out, err, timed_out = p.returncode, p.stdout, p.stderr, False
        except subprocess.TimeoutExpired as exc:
            rc, out, err, timed_out = None, exc.stdout or b"", exc.stderr or b"", True
        res = {"exit": rc, "timed_out": timed_out, "traceback_on_stderr": b"Traceback" in err,
               "stderr_last_line": (err.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:160],
               "stdout_bytes": len(out), "media_process_launched": os.path.exists(self.marker),
               "files_created": sorted(set(self.tree(cwd)) - set(before))}
        try:
            doc = json.loads(out)
            res["stdout_json"] = True
            res["ok"] = doc.get("ok") if isinstance(doc, dict) else None
            res["error_kind"] = (doc.get("error") or {}).get("kind") if isinstance(doc, dict) and isinstance(doc.get("error"), dict) else None
            res["issue_codes"] = [i["code"] for i in (doc.get("error") or {}).get("issues", [])] if isinstance(doc, dict) and isinstance(doc.get("error"), dict) else None
            res["_doc"] = doc
        except ValueError:
            res["stdout_json"] = False
        res["_raw"] = out
        return res

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def strip(res):
    return {k: v for k, v in res.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------------------------------------------- F-CLI-1
def cli_surrogate(sb: Sandbox):
    ep = json.load(open(EXAMPLE, encoding="utf-8"))
    ep_ok = jb(ep)
    ep_jp = copy.deepcopy(ep)
    ep_jp["captions"][0]["text"] = "日本語の字幕：テスト"
    am = audio_manifest(events=[{"id": "e", "source_id": "s", "state": "UNKNOWN"}])
    avm = avmux_manifest(wav_bytes=b"x")

    def with_key(doc, key_json):  # the key is spelled as a JSON escape in the file, exactly as in the audit
        return (json.dumps(doc, ensure_ascii=False)[:-1] + ", " + key_json + ": 1}").encode("utf-8")

    out = {}
    for label, module, doc in (("video_core", "video_core", ep), ("audiomix", "video_core.audiomix", am), ("avmux", "video_core.avmux", avm)):
        bad = sb.write(f"{label}.lone-surrogate-key.json", with_key(doc, '"\\ud800"'))
        r = sb.run(module, ["validate", bad])
        out[f"{label}: validate, extra key \\ud800"] = strip(r)
        if r.get("stdout_json"):
            # lossless: a JSON-conforming decode of the diagnostic returns the exact offending key
            paths = [i["path"] for i in r["_doc"]["error"]["issues"]]
            out[f"{label}: validate, extra key \\ud800"]["issue_path_decodes_to_exact_key"] = any(p.endswith("/\ud800") for p in paths)
        # valid but unknown Japanese key: rejected, key shown as UTF-8 (normal serialisation must not change)
        jp_key = sb.write(f"{label}.unknown-jp-key.json", with_key(doc, '"未知キー"'))
        r = sb.run(module, ["validate", jp_key])
        s = strip(r)
        s["key_emitted_as_literal_utf8"] = "未知キー".encode("utf-8") in r["_raw"]
        out[f"{label}: validate, unknown Japanese key (control)"] = s
    # positive controls
    good = sb.write("ep.ok.json", ep_ok)
    out["video_core: validate valid episode (control)"] = strip(sb.run("video_core", ["validate", good]))
    jp = sb.write("ep.jp.json", jb(ep_jp))
    r = sb.run("video_core", ["validate", jp])
    out["video_core: validate Japanese caption (control)"] = strip(r)
    srt = sb.run("video_core", ["srt", jp])
    expected = None
    try:
        sys.path.insert(0, ROOT)
        from video_core.episode import load_episode
        from video_core.srt import export_srt
        expected = export_srt(load_episode(jb(ep_jp)))
    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
        expected = repr(exc).encode()
    out["video_core: srt of the Japanese caption is the exact UTF-8 bytes (control)"] = {"exit": srt["exit"], "bytes_equal_to_library_export": srt["_raw"] == expected,
                                                                                         "contains_expected_text": "日本語の字幕：テスト".encode("utf-8") in srt["_raw"]}
    am_jp = audio_manifest(events=[{"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "決定-参照 日本語"}])
    out["audiomix: validate Japanese decision_ref (control)"] = strip(sb.run("video_core.audiomix", ["validate", sb.write("am.jp.json", jb(am_jp))]))
    av_jp = avmux_manifest(wav_bytes=b"x", ref="選択-参照")
    out["avmux: validate Japanese selection.ref (control)"] = strip(sb.run("video_core.avmux", ["validate", sb.write("av.jp.json", jb(av_jp))]))
    plan = sb.run("video_core", [EXAMPLE])
    out["video_core: baseline plan hash (control)"] = {"exit": plan["exit"], "plan_sha256": plan["_doc"]["plan_sha256"] if plan.get("stdout_json") else None,
                                                       "equals_pinned_baseline": plan.get("stdout_json") and plan["_doc"]["plan_sha256"] == BASELINE_PLAN}
    return out


# ---------------------------------------------------------------------------------------------------------------- F-AUD-1
def audio_zero_samples():
    silence = {"id": "a", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r", "start_frame": 0, "end_frame": 10}
    asset = {"id": "x", "path": "x.wav", "sha256": "0" * 64}

    def ev(i, state, s, e, **kw):
        d = {"id": i, "source_id": "s", "state": state, "start_frame": s, "end_frame": e}
        d.update(kw)
        return d

    cases = {
        "audit pair 1: selected silence [0,10) + UNSELECTED UNKNOWN [2,3)": ([silence, ev("b", "UNKNOWN", 2, 3)], ["a"], []),
        "audit pair 2: selected silence [0,10) + SELECTED OMIT [2,3)": ([silence, ev("b", "OMIT", 2, 3, decision_ref="r2")], ["a", "b"], []),
        "unselected USE [12,13) (never read or verified)": ([silence, ev("b", "USE", 12, 13, asset="x", source_in_sample=0, source_out_sample=1)], ["a"], [asset]),
        "unselected INTENTIONAL_SILENCE [12,13)": ([silence, ev("b", "INTENTIONAL_SILENCE", 12, 13, decision_ref="r2")], ["a"], []),
        "unselected OMIT [12,13)": ([silence, ev("b", "OMIT", 12, 13, decision_ref="r2")], ["a"], []),
        "selected USE [12,13) must still be rejected": ([silence, ev("b", "USE", 12, 13, asset="x", source_in_sample=0, source_out_sample=1)], ["a", "b"], [asset]),
        "selected INTENTIONAL_SILENCE [12,13) must still be rejected": ([silence, ev("b", "INTENTIONAL_SILENCE", 12, 13, decision_ref="r2")], ["a", "b"], []),
        "selected UNKNOWN [12,13) (unchanged: rejected, see INPUT_CONTRACT.md)": ([silence, ev("b", "UNKNOWN", 12, 13)], ["a", "b"], []),
        "selected zero-sample INTENTIONAL_SILENCE alone": ([ev("a", "INTENTIONAL_SILENCE", 2, 3, decision_ref="r")], ["a"], []),
    }
    out = {}
    for label, (events, scope, assets) in cases.items():
        try:
            p = aplan.prepare_plan(jb(audio_manifest(fps=(60000, 1), total=60, events=events, scope=scope, assets=assets)))
            out[label] = {"result": p.status, "hold_codes": [h["code"] for h in p.hold], "coverage_gap_count": p.canonical["coverage"]["gap_count"],
                          "event_samples": {e["id"]: e.get("samples") for e in p.canonical["events"]}}
        except errors.VideoCoreError as exc:
            out[label] = {"result": "REJECTED", "codes": [i.code for i in exc.issues]}
        except Exception as exc:  # noqa: BLE001
            out[label] = {"result": "LEAK:" + type(exc).__name__}
    return out


# ---------------------------------------------------------------------------------------------------------------- F-REC-1 / F-REC-2
def build_receipt_world(sb: Sandbox):
    """A genuine receipt written by the real audio mixer API (pure Python, no media process) for a 4800-sample stem."""
    src = wav(tone())
    assets = os.path.join(sb.dir, "mixassets")
    os.mkdir(assets)
    with open(os.path.join(assets, "src.wav"), "wb") as fh:
        fh.write(src)
    doc = audio_manifest(total=3, assets=[{"id": "a", "path": "src.wav", "sha256": sha(src)}],
                         events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
    out = os.path.join(sb.dir, "mixout")
    os.mkdir(out)
    rec = amix.mix(jb(doc), asset_root=assets, output_root=out, output_name="real")
    stem = open(os.path.join(out, "real", "stem.wav"), "rb").read()
    raw = open(os.path.join(out, "real", "receipt.json"), "rb").read()
    return rec, stem, raw


def receipt_cases(sb: Sandbox):
    rec, stem, raw = build_receipt_world(sb)
    assets = os.path.join(sb.dir, "avassets")
    os.mkdir(assets)
    vbytes = b"synthetic-not-a-video"
    for n, b in (("a.wav", stem), ("v.mp4", vbytes)):
        with open(os.path.join(assets, n), "wb") as fh:
            fh.write(b)
    good = json.loads(raw)

    def attempt(data: bytes):
        with open(os.path.join(assets, "r.json"), "wb") as fh:
            fh.write(data)
        doc = avmux_manifest(wav_bytes=stem, receipt={"path": "r.json", "sha256": sha(data)})
        try:
            p = vplan.prepare_plan(jb(doc), asset_root=assets)
            r = p.canonical["audio"]["receipt"]
            return {"result": "ACCEPTED", "receipt_in_plan": {k: r[k] for k in ("status", "plan_sha256")}}
        except errors.VideoCoreError as exc:
            return {"result": "REJECTED", "codes": [i.code for i in exc.issues]}
        except BaseException as exc:  # noqa: BLE001
            return {"result": "LEAK", "exception": type(exc).__name__}

    def mod(fn):
        d = copy.deepcopy(good)
        fn(d)
        return json.dumps(d).encode()

    stem_sha = sha(stem)
    three = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": stem_sha}], "readback": {"wav": {"sample_count": 4800}}}
    cases = {
        "CONTROL genuine receipt written by the real audio mixer": raw,
        "CONTROL HOLD status (three-field)": json.dumps({**three, "status": "HOLD"}).encode(),
        # F-REC-1 shapes
        "[]": b"[]", "null": b"null", '"x"': b'"x"', "1": b"1", "empty file": b"", "UTF-8 BOM": b"\xef\xbb\xbf" + raw,
        "audit literal (schema-less) outputs: \"x\"": json.dumps({**three, "outputs": "x"}).encode(),
        "audit literal (schema-less) outputs: [1]": json.dumps({**three, "outputs": [1]}).encode(),
        "audit literal (schema-less) readback.wav: \"x\"": json.dumps({**three, "readback": {"wav": "x"}}).encode(),
        "genuine receipt but outputs: \"x\"": mod(lambda d: d.update(outputs="x")),
        "genuine receipt but outputs: [1]": mod(lambda d: d.update(outputs=[1])),
        "genuine receipt but readback.wav: \"x\"": mod(lambda d: d["readback"].update(wav="x")),
        "genuine receipt but readback: null": mod(lambda d: d.update(readback=None)),
        "genuine receipt but readback.checks: \"x\"": mod(lambda d: d["readback"].update(checks="x")),
        "genuine receipt but warnings: 5": mod(lambda d: d.update(warnings=5)),
        "genuine receipt but an extra stem_partial output": mod(lambda d: d["outputs"].append({"role": "stem_partial", "name": "stem.wav.partial", "partial": True, "bytes": 1, "sha256": "0" * 64})),
        "genuine receipt but mix.clipped_samples=1": mod(lambda d: d["mix"].update(clipped_samples=1)),
        "genuine receipt but inputs_unchanged=false": mod(lambda d: d["input"].update(inputs_unchanged=False)),
        "genuine receipt but stem sha256 of another file": mod(lambda d: [o.update(sha256="0" * 64) for o in d["outputs"] if o["role"] == "stem"]),
        "100000 nested arrays": b"[" * 100000 + b"]" * 100000,
        # F-REC-2 inconsistent / weak receipts
        "schema-less three-field receipt": json.dumps(three).encode(),
        "genuine receipt but readback.all_ok=false": mod(lambda d: d["readback"].update(all_ok=False)),
        "genuine receipt but one readback check ok=false": mod(lambda d: d["readback"]["checks"][0].update(ok=False)),
        "genuine receipt but stem partial=true": mod(lambda d: [o.update(partial=True) for o in d["outputs"] if o["role"] == "stem"]),
        "genuine receipt but quality_evaluated=true": mod(lambda d: d.update(quality_evaluated=True)),
        "genuine receipt but written=false": mod(lambda d: d.update(written=False)),
        "genuine receipt but stage=mix": mod(lambda d: d.update(stage="mix")),
        "genuine receipt but schema=other": mod(lambda d: d.update(schema="video_core.receipt/1")),
        "genuine receipt but plan_sha256 malformed": mod(lambda d: d.update(plan_sha256="zz")),
        "genuine receipt but sample_count 4800.0 (float)": mod(lambda d: d["readback"]["wav"].update(sample_count=4800.0)),
        "genuine receipt but sample_count true": mod(lambda d: d["readback"]["wav"].update(sample_count=True)),
        "genuine receipt but readback wav channels 2": mod(lambda d: d["readback"]["wav"].update(channels=2)),
        "genuine receipt but readback wav sample_rate 44100": mod(lambda d: d["readback"]["wav"].update(sample_rate=44100)),
        "genuine receipt but duplicate status keys (HOLD then MIXED_VERIFIED)": raw.replace(b'"status": "MIXED_VERIFIED"', b'"status": "HOLD",\n  "status": "MIXED_VERIFIED"', 1),
        "genuine receipt but NaN in a counter": raw.replace(b'"clipped_samples": 0', b'"clipped_samples": NaN', 1),
    }
    return {k: attempt(v) for k, v in cases.items()}


# ---------------------------------------------------------------------------------------------------------------- F-ENC-1
def encoding_roots(sb: Sandbox):
    out = {"platform": platform.system()}
    ep = EXAMPLE
    root_jp = sb.path("出力先")
    os.mkdir(root_jp)
    r = sb.run("video_core", ["plan", ep, "--output-root", root_jp, "--output-name", "run1"])
    out["video plan, valid Japanese output root (control)"] = {**strip(r), "root_in_stdout_as_utf8": "出力先".encode() in r["_raw"]}
    raw_root = os.path.join(os.fsencode(sb.dir), b"out_caf\xe9")
    try:
        os.mkdir(raw_root)
    except OSError as exc:
        out["raw 0xe9 directory"] = f"not creatable on this platform: {type(exc).__name__}"
        return out
    out["raw 0xe9 directory"] = "created (POSIX raw-byte name)"
    r = sb.run("video_core", ["plan", ep, "--output-root", os.fsdecode(raw_root), "--output-name", "run1"])
    out["video plan with --output-root b'out_caf\\xe9'"] = strip(r)
    # library level: real_directory itself
    from video_core import paths
    try:
        paths.real_directory(os.fsdecode(raw_root), "output_root")
        out["paths.real_directory(raw 0xe9 root)"] = "ACCEPTED"
    except errors.VideoCoreError as exc:
        out["paths.real_directory(raw 0xe9 root)"] = {"result": "REJECTED", "codes": [i.code for i in exc.issues]}
    except BaseException as exc:  # noqa: BLE001
        out["paths.real_directory(raw 0xe9 root)"] = {"result": "LEAK", "exception": type(exc).__name__}
    # asset root with a raw byte, via the avmux plan CLI (no output requested)
    assets = os.path.join(sb.dir, "av_in")
    os.mkdir(assets)
    stem = wav(tone())
    for n, b in (("a.wav", stem), ("v.mp4", b"synthetic-not-a-video")):
        open(os.path.join(assets, n), "wb").write(b)
    mf = sb.write("av.json", jb(avmux_manifest(wav_bytes=stem)))
    ok = sb.run("video_core.avmux", ["plan", mf, "--asset-root", assets])
    out["avmux plan, valid ASCII asset root (control)"] = {k: ok.get(k) for k in ("exit", "ok", "traceback_on_stderr")}
    raw_assets = os.path.join(os.fsencode(sb.dir), b"assets_caf\xe9")
    os.mkdir(raw_assets)
    for n in ("a.wav", "v.mp4"):
        shutil.copy(os.path.join(assets, n), os.path.join(os.fsdecode(raw_assets), n))
    r = sb.run("video_core.avmux", ["plan", mf, "--asset-root", os.fsdecode(raw_assets)])
    out["avmux plan with raw 0xe9 asset root"] = strip(r)
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    sb = Sandbox()
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, cwd=ROOT, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, cwd=ROOT, text=True).stdout.strip())
        res = {"schema": "video_core.input_contract_repro/1", "source_head": git, "working_tree_dirty": dirty, "python": platform.python_version(),
               "platform": platform.system(), "note": "observations only; the same script runs before and after the fix",
               "F-CLI-1": cli_surrogate(sb), "F-AUD-1": audio_zero_samples(), "F-REC-1_F-REC-2": receipt_cases(sb), "F-ENC-1": encoding_roots(sb)}
    finally:
        sb.close()
    text = json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
