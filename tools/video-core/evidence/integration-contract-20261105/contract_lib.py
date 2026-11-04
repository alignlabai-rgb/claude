"""Shared library for the integration-contract suite (harness code only; NOT a production adapter and NOT a replacement validator).

* deterministic stdlib-only fixture bytes (WAV tones, plainly-labelled non-media placeholder "videos")
* an isolated per-case "world" in a temp dir (assets/ out/ bin/ docs/) with tree hashing
* run_cli(): the ONLY place that launches a process. It launches the harness-owned runner (harness_runner.py) which runs one real public CLI
  of video_core in-process under an audit hook. It refuses any argv that could render, mix, mux or probe media.
* api_ref(): file/symbol/line/sha256 references into the library source, resolved at run time (a reference that no longer resolves fails loudly)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VC_DIR = os.path.abspath(os.environ.get("VCC_VIDEO_CORE_DIR") or os.path.join(HERE, "..", ".."))   # tools/video-core (override only for the mutation self-check)
RUNNER = os.path.join(HERE, "harness_runner.py")
FIXDIR = os.path.join(HERE, "fixtures")
PER_CALL_TIMEOUT = 10.0
PER_CASE_BUDGET = 10.0

RATE = 48_000
FORBIDDEN_FLAGS = ("--render", "--mix", "--mux")           # actually execute ffmpeg / mix / mux: never used by this job
FORBIDDEN_SUBCOMMANDS = {"video_core": {"inspect", "render_exec"}}   # inspect launches ffprobe
ALLOWED_MODULES = ("video_core", "video_core.audiomix", "video_core.avmux")

# --------------------------------------------------------------------------------------------- deterministic fixture bytes


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tone(kind: str, freq: int, n: int, amp: int = 1000) -> list:
    p = RATE // freq
    out = []
    for i in range(n):
        ph = i % p
        if kind == "square":
            out.append(amp if ph < p // 2 else -amp)
        elif kind == "triangle":
            h = p // 2
            out.append(-amp + (2 * amp * ph) // h if ph < h else amp - (2 * amp * (ph - h)) // h)
        elif kind == "saw":
            out.append(-amp + (2 * amp * ph) // p)
        elif kind == "zero":
            out.append(0)
        else:
            raise ValueError(kind)
    return out


def wav_bytes(samples) -> bytes:
    """48 kHz s16le mono PCM WAV, written independently of video_core.audiomix.wav."""
    pcm = b"".join(struct.pack("<h", v) for v in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def placeholder_video(tag: str) -> bytes:
    """NOT a video. Plain bytes with a media-looking extension, to show what plan-time checks cannot see."""
    return (f"SYNTHETIC-PLACEHOLDER-NOT-MEDIA {tag}\n" * 4).encode("ascii")


def fixture_bytes() -> dict:
    return {
        "seg_a.wav": wav_bytes(tone("square", 480, 6000)),
        "stem.wav": wav_bytes(tone("triangle", 600, 4805)),            # 3 frames @ 30000/1001 -> 4805 samples
        "stem_v2.wav": wav_bytes(tone("saw", 600, 4805)),              # same length, different content ("another version")
        "stem_30.wav": wav_bytes(tone("triangle", 600, 4800)),         # 3 frames @ 30/1 -> 4800 samples
        "stem_silent.wav": wav_bytes(tone("zero", 600, 4805)),         # all zeros: indistinguishable from "intentional" by the file alone
        "clip_a.mp4": placeholder_video("clip_a v1"),
        "clip_a_v2.mp4": placeholder_video("clip_a v2"),               # same size as v1, different bytes
        "proxy_clip_a.mp4": placeholder_video("proxy-of-clip_a"),
        "video_ok.mp4": placeholder_video("video_ok"),
    }


def build_fixture_json() -> dict:
    """The committed fixtures/*.json, regenerated deterministically. The suite fails if the committed files differ."""
    fb = fixture_bytes()
    h = {k: sha256(v) for k, v in fb.items()}
    episode = {
        "version": 1, "fps": {"n": 30000, "d": 1001}, "canvas": {"width": 320, "height": 180}, "total_frames": 3,
        "clips": [
            {"id": "clip_a", "start_frame": 0, "duration_frames": 2, "source": {"kind": "file", "path": "clip_a.mp4"}},
            {"id": "clip_b", "start_frame": 2, "duration_frames": 1, "source": {"kind": "color", "value": "#30506a"}},
        ],
        "captions": [],
    }
    episode_color = copy.deepcopy(episode)
    episode_color["clips"] = [{"id": "clip_a", "start_frame": 0, "duration_frames": 2, "source": {"kind": "color", "value": "#6a5030"}},
                              {"id": "clip_b", "start_frame": 2, "duration_frames": 1, "source": {"kind": "color", "value": "#30506a"}}]
    selection = {
        "_note": ("OWNER-SIDE input of the selection/binding owner. It is never passed to video_core. Synthetic: no real story, voice or timing. "
                  "Opaque refs are invented strings; nothing here is an approval."),
        "snapshot_ref": "synthetic-snapshot/0001",
        "rows": [
            {"row_id": "ev_vo_1", "selected": True, "state": "USE", "binding_ref": "owner-binding/synthetic-001", "source_sha256": h["seg_a.wav"],
             "timing_qualification": {"status": "QUALIFIED", "kind": "sample_range", "qualification_ref": "owner-timing/synthetic-q1"}},
            {"row_id": "ev_sil_2", "selected": True, "state": "INTENTIONAL_SILENCE", "binding_ref": "owner-decision/silence-001"},
            {"row_id": "ev_vo_3", "selected": True, "state": "USE", "binding_ref": "owner-binding/synthetic-003", "source_sha256": h["seg_a.wav"],
             "timing_qualification": {"status": "QUALIFIED", "kind": "sample_range", "qualification_ref": "owner-timing/synthetic-q3"}},
            {"row_id": "ev_x_missing", "selected": False, "state": "MISSING"},
            {"row_id": "ev_x_unknown", "selected": False, "state": "UNKNOWN"},
            {"row_id": "ev_x_proxy", "selected": False, "state": "PROXY"},
            {"row_id": "ev_x_hold", "selected": False, "state": "HOLD"},
        ],
    }
    audio = {
        "manifest_version": 1,
        "timeline": {"fps": {"n": 30000, "d": 1001}, "total_frames": 3},
        "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
        "scope": {"event_ids": ["ev_vo_1", "ev_sil_2", "ev_vo_3"]},
        "policy": {"gap": "REQUIRE_FULL_COVERAGE", "overlap": "REJECT", "clipping": "REJECT"},
        "assets": [{"id": "asset_a", "path": "seg_a.wav", "sha256": h["seg_a.wav"]},
                   {"id": "asset_unselected", "path": "seg_unselected.wav", "sha256": "0" * 64}],
        "events": [
            {"id": "ev_vo_1", "source_id": "src_syn_1", "state": "USE", "role": "vo", "decision_ref": "owner-binding/synthetic-001", "start_frame": 0, "end_frame": 1,
             "asset": "asset_a", "source_in_sample": 1000, "source_out_sample": 2602},
            {"id": "ev_sil_2", "source_id": "src_syn_2", "state": "INTENTIONAL_SILENCE", "decision_ref": "owner-decision/silence-001", "start_frame": 1, "end_frame": 2},
            {"id": "ev_vo_3", "source_id": "src_syn_3", "state": "USE", "role": "vo", "decision_ref": "owner-binding/synthetic-003", "start_frame": 2, "end_frame": 3,
             "asset": "asset_a", "source_in_sample": 4000, "source_out_sample": 5602},
            {"id": "ev_x_missing", "source_id": "src_syn_x1", "state": "MISSING"},
            {"id": "ev_x_unknown", "source_id": "src_syn_x2", "state": "UNKNOWN"},
            {"id": "ev_x_proxy", "source_id": "src_syn_x3", "state": "PROXY"},
            {"id": "ev_x_use_unselected", "source_id": "src_syn_x4", "state": "USE", "start_frame": 0, "end_frame": 1, "asset": "asset_unselected",
             "source_in_sample": 0, "source_out_sample": 1602},
        ],
    }
    av_mov = {
        "avmux_version": 1, "route": "MOV_PCM_S16LE", "selection": {"ref": "synthetic-selection/0001"},
        "video": {"state": "USE", "path": "video_ok.mp4", "sha256": h["video_ok.mp4"],
                  "declared": {"codec": "h264", "width": 320, "height": 180, "fps": {"n": 30000, "d": 1001}, "total_frames": 3}},
        "audio": {"state": "USE", "path": "stem.wav", "sha256": h["stem.wav"], "declared": {"sample_rate": 48000, "channels": 1, "sample_count": 4805},
                  "receipt": {"path": "audio_receipt.json", "sha256": None}},
    }
    receipt = {
        "_fixture_note": ("HARNESS-FABRICATED. Not produced by `audiomix mix` (no mix was run in this job). The shape mirrors the real receipt so the avmux "
                          "plan can read it; the library cannot tell the difference, which is the point of GAP-04."),
        "schema": "video_core.audio_receipt/1", "status": "MIXED_VERIFIED", "written": True, "stage": "complete", "quality_evaluated": False,
        "plan_sha256": "f" * 64,
        "input": {"manifest_sha256": "e" * 64, "manifest_bytes": 1, "inputs_unchanged": True, "assets": []},
        "warnings": [], "unresolved_report": [],
        "outputs": [{"name": "stem.wav", "role": "stem", "bytes": len(fb["stem.wav"]), "sha256": h["stem.wav"], "partial": False}],
        "readback": {"all_ok": True, "checks": [], "wav": {"sample_rate": 48000, "channels": 1, "bits": 16, "sample_count": 4805, "sha256": h["stem.wav"]}},
    }
    rcpt_bytes = canon(receipt)
    av_mov["audio"]["receipt"]["sha256"] = sha256(rcpt_bytes)
    av_mp4 = copy.deepcopy(av_mov)
    av_mp4["route"] = "MP4_AAC"
    index = {"_note": "sha256 of every generated byte fixture and committed JSON fixture; the suite recomputes and compares", "bytes": h,
             "bytes_sizes": {k: len(v) for k, v in fb.items()}}
    return {"episode.video_only.selected_subset.json": episode, "episode.video_only.color_only.json": episode_color,
            "owner_selection_table.synthetic.json": selection, "audio_manifest.selected_subset.json": audio,
            "avmux_manifest.mov.json": av_mov, "avmux_manifest.mp4.json": av_mp4, "audio_receipt.harness_fabricated.json": receipt,
            "_index_bytes": index}


def canon(obj) -> bytes:
    """Pretty, key-sorted, LF, UTF-8: the exact bytes of every committed JSON fixture."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def load_fixture(name: str):
    with open(os.path.join(FIXDIR, name), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def write_fixtures() -> None:
    os.makedirs(FIXDIR, exist_ok=True)
    built = build_fixture_json()
    index = built.pop("_index_bytes")
    json_hashes = {}
    for name, obj in built.items():
        data = canon(obj)
        with open(os.path.join(FIXDIR, name), "wb") as fh:
            fh.write(data)
        json_hashes[name] = sha256(data)
    index["json"] = json_hashes
    with open(os.path.join(FIXDIR, "FIXTURES_INDEX.json"), "wb") as fh:
        fh.write(canon(index))


def verify_fixtures() -> list:
    """Return a list of problems (empty = committed fixtures equal the deterministic regeneration)."""
    built = build_fixture_json()
    index = built.pop("_index_bytes")
    problems = []
    for name, obj in built.items():
        p = os.path.join(FIXDIR, name)
        if not os.path.isfile(p):
            problems.append(f"missing {name}")
        elif open(p, "rb").read() != canon(obj):
            problems.append(f"differs from regeneration: {name}")
    try:
        committed = load_fixture("FIXTURES_INDEX.json")
    except OSError:
        return problems + ["missing FIXTURES_INDEX.json"]
    if committed.get("bytes") != index["bytes"]:
        problems.append("byte-fixture hashes differ from the pinned index")
    return problems


# --------------------------------------------------------------------------------------------- isolated world


def tree_hash(path: str) -> dict:
    """{relative path: sha256 | 'DIR' | 'SYMLINK->target'} for everything under path (sorted, deterministic)."""
    out = {}
    if not os.path.lexists(path):
        return out
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            rel = os.path.relpath(full, path)
            if os.path.islink(full):
                out[rel] = "SYMLINK->" + os.readlink(full)
                dirnames.remove(d)
            else:
                out[rel] = "DIR"
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, path)
            if os.path.islink(full):
                out[rel] = "SYMLINK->" + os.readlink(full)
            else:
                with open(full, "rb") as fh:
                    out[rel] = sha256(fh.read())
    return dict(sorted(out.items()))


def digest_of(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


SENTINEL = """#!/bin/sh
# harness sentinel stub standing in for ffmpeg/ffprobe: records that it was launched and fails. Identical bytes in every world.
printf '%s %s\\n' "${0##*/}" "$*" >> "$VCC_SENTINEL_LOG"
echo "SENTINEL: a media tool was launched; the contract suite must never do that" >&2
exit 97
"""


class World:
    """One isolated temp world per case. Layout: assets/ (inputs, asset root), out/ (empty output root), bin/ (sentinel ffmpeg+ffprobe), docs/."""

    def __init__(self, tmp_parent: str | None = None, label: str = "w"):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix=f"vcc-{label}-", dir=tmp_parent))
        self.assets, self.out, self.bin, self.docs = (os.path.join(self.root, d) for d in ("assets", "out", "bin", "docs"))
        for d in (self.assets, self.out, self.bin, self.docs):
            os.mkdir(d)
        self.sentinel_log = os.path.join(self.root, "sentinel.log")
        for name in ("ffmpeg", "ffprobe"):
            p = os.path.join(self.bin, name)
            with open(p, "w") as fh:
                fh.write(SENTINEL)
            os.chmod(p, 0o755)
        self.t0 = time.monotonic()

    def put(self, rel: str, data: bytes, area: str = "assets") -> str:
        p = os.path.join(getattr(self, area), rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        return sha256(data)

    def put_json(self, rel: str, obj, area: str = "docs") -> str:
        return self.put(rel, canon(obj), area)

    def put_fixtures(self, names=None) -> dict:
        fb = fixture_bytes()
        hashes = {}
        for n, b in fb.items():
            if names is None or n in names:
                hashes[n] = self.put(n, b)
        return hashes

    def p(self, area: str, rel: str = "") -> str:
        return os.path.join(getattr(self, area), rel) if rel else getattr(self, area)

    def sentinel_hits(self) -> list:
        if not os.path.exists(self.sentinel_log):
            return []
        return open(self.sentinel_log).read().splitlines()

    def norm(self, text: str) -> str:
        return text.replace(self.root, "<WORLD>")

    def snapshot(self) -> dict:
        return {"assets": tree_hash(self.assets), "docs": tree_hash(self.docs), "out": tree_hash(self.out), "bin": tree_hash(self.bin)}

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# --------------------------------------------------------------------------------------------- the only launch point


class PolicyViolation(Exception):
    """The harness refused to launch something outside the job's boundaries."""


LAUNCHES: list = []   # harness-owned launches, in order: {"module","args"(normalised),"exit_code"}
RESULT_CACHE_LOG: list = []


def _policy_check(module: str, args: list) -> None:
    if module not in ALLOWED_MODULES:
        raise PolicyViolation(f"module {module!r} not allowed")
    for a in args:
        if a.startswith("--") and len(a) >= 3:
            flag = a.split("=", 1)[0]
            for forbidden in FORBIDDEN_FLAGS:
                if forbidden.startswith(flag):      # also catches argparse prefix abbreviations such as --ren / --mu
                    raise PolicyViolation(f"flag {a!r} could execute ffmpeg/mix/mux: refused by the harness")
    if args and args[0] in FORBIDDEN_SUBCOMMANDS.get(module, ()):
        raise PolicyViolation(f"subcommand {args[0]!r} launches ffprobe: refused by the harness")


# The plans estimate their own argv size from the REAL asset/output paths, so these two numbers depend on the length of the temp directory the
# harness happened to get. They are replaced by a placeholder so that recorded hashes are reproducible from any scratch location.
_PATH_LENGTH_DEPENDENT = re.compile(r'("estimated_(?:argv_bytes|windows_cmdline_utf16_units)":\s*)\d+')


class CliResult:
    def __init__(self, module, args, exit_code, stdout, stderr, log, world):
        self.module, self.args, self.exit_code, self.stderr = module, args, exit_code, stderr
        self.stdout = _PATH_LENGTH_DEPENDENT.sub(r'\1"<temp-path-length dependent>"', world.norm(stdout))
        self.log = log
        try:
            self.json = json.loads(self.stdout) if self.stdout.strip() else None
        except ValueError:
            self.json = None

    @property
    def issue_codes(self) -> list:
        if isinstance(self.json, dict) and isinstance(self.json.get("error"), dict):
            return [i["code"] for i in self.json["error"].get("issues", [])]
        return []

    @property
    def external_launches(self) -> list:
        return self.log["events"]["process"]

    def record(self) -> dict:
        # deterministic across Python versions: help text and tracebacks (version/path dependent) are not hashed; import-time read counts are omitted
        helpish = any(a in ("--help", "-h") for a in self.args)
        return {"module": self.module, "argv": self.args, "exit_code": self.exit_code, "stdout_sha256": None if helpish else sha256(self.stdout.encode("utf-8")),
                "runner_events": {k: len(self.log["events"][k]) for k in ("process", "network", "mutation", "foreign_reads")}}


def run_cli(world: World, module: str, args: list, *, timeout: float = PER_CALL_TIMEOUT, path_dir: str | None = None) -> CliResult:
    """Run one real public CLI under the instrumented runner. `args` may contain the tokens {assets} {out} {docs} which are expanded to world paths
    (and reported as <WORLD>/... in every record)."""
    exp = [a.replace("{assets}", world.assets).replace("{out}", world.out).replace("{docs}", world.docs) for a in args]
    _policy_check(module, exp)
    remaining = PER_CASE_BUDGET - (time.monotonic() - world.t0)
    if remaining <= 0.5:
        raise TimeoutError("per-case 10 s budget exhausted before this call")
    t = min(timeout, remaining)
    logpath = os.path.join(world.root, f"runner-{len(LAUNCHES):05d}.json")
    env = {"PATH": path_dir if path_dir is not None else world.bin, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "VCC_SENTINEL_LOG": world.sentinel_log}
    argv = [sys.executable, "-I", "-B", RUNNER, logpath, VC_DIR, module, "--"] + exp
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env, cwd=world.root,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=t)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)       # only the process group of OUR OWN child
        except ProcessLookupError:
            pass
        proc.communicate()
        raise TimeoutError(f"CLI call exceeded {t:.1f}s (killed its own process group)") from None
    if not os.path.isfile(logpath):
        raise RuntimeError(f"runner produced no log; stderr={err[-300:]!r}")
    with open(logpath, encoding="utf-8") as fh:
        log = json.load(fh)
    os.remove(logpath)
    res = CliResult(module, [world.norm(a) for a in exp], proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), log, world)
    LAUNCHES.append({"module": module, "args": res.args, "exit_code": res.exit_code, "child_PATH": world.norm(env["PATH"])})
    return res


# --------------------------------------------------------------------------------------------- source references


def api_ref(relfile: str, symbol: str) -> dict:
    """{"file","symbol","line","file_sha256"} resolved against the library source now. Raises if the symbol cannot be found."""
    path = os.path.join(VC_DIR, relfile)
    with open(path, "rb") as fh:
        raw = fh.read()
    lines = raw.decode("utf-8").splitlines()
    pat = re.compile(rf"^\s*(def|class)\s+{re.escape(symbol)}\b|^{re.escape(symbol)}\s*=")
    for i, ln in enumerate(lines, 1):
        if pat.search(ln):
            return {"file": f"tools/video-core/{relfile}", "symbol": symbol, "line": i, "file_sha256": sha256(raw)}
    raise LookupError(f"{symbol} not found in {relfile}")


def literal_ref(relfile: str, literal: str) -> dict:
    """Reference to the first line containing an exact literal (for behaviours that are a string/branch, not a symbol)."""
    path = os.path.join(VC_DIR, relfile)
    with open(path, "rb") as fh:
        raw = fh.read()
    for i, ln in enumerate(raw.decode("utf-8").splitlines(), 1):
        if literal in ln:
            return {"file": f"tools/video-core/{relfile}", "literal": literal, "line": i, "file_sha256": sha256(raw)}
    raise LookupError(f"literal {literal!r} not found in {relfile}")


CONTROL = os.path.join(HERE, "harness_positive_control.py")


def run_control(world: World) -> CliResult:
    """Run the instrument's positive control (launches the sentinel stub, makes a dir, writes a file). Not a library call."""
    logpath = os.path.join(world.root, "runner-control.json")
    env = {"PATH": world.bin, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "VCC_OUT": world.out, "VCC_SENTINEL_LOG": world.sentinel_log}
    argv = [sys.executable, "-I", "-B", RUNNER, logpath, VC_DIR, CONTROL, "--"]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env, cwd=world.root, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=PER_CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    with open(logpath, encoding="utf-8") as fh:
        log = json.load(fh)
    CONTROL_LAUNCHES.append({"module": "harness_positive_control.py", "exit_code": proc.returncode})
    return CliResult("harness_positive_control", [], proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), log, world)


CONTROL_LAUNCHES: list = []
