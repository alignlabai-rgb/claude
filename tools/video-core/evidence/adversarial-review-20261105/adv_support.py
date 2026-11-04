"""Shared harness for the adversarial review tests (stdlib only; does not modify any production module).

* import path:   puts tools/video-core on sys.path so `video_core` is the code under test (never copied, never patched).
* Sandbox:       one fresh temporary directory per bounded synthetic test, OUTSIDE the source tree; selected small text evidence
                 (json/log/txt) is copied to runs/<label>/<test id>/ before the directory is removed (ADV_KEEP_TMP=1 keeps it).
* record():      appends machine-readable observations to runs/<label>/observations.jsonl
* known_defect:  marks a test that asserts the CORRECT behaviour and currently FAILS because the defect is real. It is
                 `unittest.expectedFailure` normally (suite stays interpretable) and a hard failure with ADV_STRICT=1.
                 The assertion is never weakened: when the implementation is fixed the test reports UNEXPECTED SUCCESS.
* child():       bounded owned child process (<= 10 s hard limit, own process group, only that group is ever signalled).
* audited():     runs a snippet in a fresh interpreter under sys.addaudithook and reports every process launch / exec /
                 file creation / mutation attempted, independent of how the code spells it (catches what mock.patch cannot).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))  # tools/video-core
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
sys.dont_write_bytecode = True

RUN_LABEL = os.environ.get("ADV_RUN_LABEL", "adhoc")
RUN_DIR = os.environ.get("ADV_RUN_DIR") or os.path.join(HERE, "runs", RUN_LABEL)
TMP_ROOT = os.environ.get("ADV_TMP_ROOT") or tempfile.gettempdir()
STRICT = os.environ.get("ADV_STRICT") == "1"
CHILD_HARD_LIMIT = 10.0
POSIX = os.name == "posix"
PY = sys.executable


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def slurp(p: str, mode: str = 'r') -> str:
    with open(p, mode, **({'encoding': 'utf-8'} if 'b' not in mode else {})) as fh:
        return fh.read()


def sha256_path(p: str) -> str:
    with open(p, "rb") as fh:
        return sha256(fh.read())


# ------------------------------------------------------------------------------------------------------------------ evidence
def record(case: str, **data) -> None:
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(os.path.join(RUN_DIR, "observations.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"case": case, **data}, sort_keys=True, ensure_ascii=True, default=str) + "\n")


class HarnessError(Exception):
    """The scenario itself did not run as designed. NEVER an expected failure: surfaces as an ERROR even inside a known-defect test."""


HARNESS_ERRORS: dict = {}


def known_defect(finding_id: str):
    """Mark a reproducer for a confirmed defect. The test asserts the CORRECT behaviour and fails today (AssertionError).
    Normal mode: unittest.expectedFailure (the defect is reported as 'expected failure', never as a pass).
    ADV_STRICT=1: no wrapper at all, so the defect is a hard FAIL. When the implementation is fixed the test reports UNEXPECTED SUCCESS.
    Any exception that is not an AssertionError (harness problem, HarnessError, IndentationError in a child, ...) is additionally reported as an
    ERROR, so a broken scenario can never hide behind 'expected failure'."""
    def deco(fn):
        fn.__finding__ = finding_id
        if STRICT:
            return fn

        @functools.wraps(fn)
        def guarded(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except AssertionError:
                raise
            except BaseException:
                HARNESS_ERRORS[self.id()] = sys.exc_info()
                raise
        guarded.__finding__ = finding_id
        return unittest.expectedFailure(guarded)
    return deco


def tree_listing(root: str) -> list:
    out = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for n in sorted(files + [d for d in dirs if os.path.islink(os.path.join(base, d))]):
            p = os.path.join(base, n)
            rel = os.path.relpath(p, root)
            try:
                st = os.lstat(p)
                out.append({"path": rel, "size": st.st_size, "link": os.path.islink(p),
                            "sha256": sha256_path(p) if os.path.isfile(p) and not os.path.islink(p) and st.st_size < 64 * 1024 * 1024 else None})
            except OSError as exc:
                out.append({"path": rel, "error": type(exc).__name__})
    return out


class Sandbox:
    """Fresh temp dir outside the source tree; evidence preserved under runs/<label>/<case>/ at finish."""

    def __init__(self, case: str):
        self.case = case
        slug = "".join(ch if ch.isalnum() else "_" for ch in case)[-40:]
        self.root = tempfile.mkdtemp(prefix="advrev-" + slug + "-", dir=TMP_ROOT)
        assert not os.path.realpath(self.root).startswith(os.path.realpath(TOOLS) + os.sep), "sandbox must be outside the source tree"
        self.finished = False

    def p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def mkdir(self, *parts: str) -> str:
        path = self.p(*parts)
        os.makedirs(path, exist_ok=True)
        return path

    def write(self, rel: str, data: bytes) -> str:
        path = self.p(*rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        dest = os.path.join(RUN_DIR, "cases", "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in self.case))
        try:
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, "tree.json"), "w", encoding="utf-8") as fh:
                json.dump(tree_listing(self.root), fh, indent=1, sort_keys=True)
            for base, _dirs, files in os.walk(self.root):
                for n in files:
                    src = os.path.join(base, n)
                    if n.endswith((".json", ".log", ".txt", ".out", ".err")) and not os.path.islink(src) and os.path.getsize(src) <= 65536:
                        rel = os.path.relpath(src, self.root).replace(os.sep, "__")
                        shutil.copyfile(src, os.path.join(dest, rel))
        finally:
            if os.environ.get("ADV_KEEP_TMP") != "1":
                shutil.rmtree(self.root, ignore_errors=True)


class AuditCase(unittest.TestCase):
    """Base class: self.sb is a fresh Sandbox per test."""

    def setUp(self):
        self.sb = Sandbox(self.id().replace("test_adv_", ""))
        self.addCleanup(self.sb.finish)

    def harness_check(self, cond, msg="harness precondition failed"):
        """Precondition of a scenario (NOT a claim about the code under test). Raises HarnessError, which is never an expected failure."""
        if not cond:
            raise HarnessError(msg)

    def run(self, result=None):
        out = super().run(result)
        err = HARNESS_ERRORS.pop(self.id(), None)
        if err is not None and result is not None:
            result.addError(self, err)
        return out


# ------------------------------------------------------------------------------------------------------------------ bounded children
def child(argv, *, timeout: float = 8.0, env=None, cwd=None, input_bytes: bytes | None = None, extra_env=None):
    """Run an owned child with a hard wall limit (<= 10 s). Returns (returncode, stdout bytes, stderr bytes, timed_out).
    Only the child's own process group is ever signalled."""
    timeout = min(timeout, CHILD_HARD_LIMIT)
    e = dict(os.environ if env is None else env)
    e.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if extra_env:
        e.update(extra_env)
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd or TOOLS, env=e, start_new_session=POSIX)
    try:
        out, err = proc.communicate(input_bytes, timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        try:
            if POSIX:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        out, err = proc.communicate()
        return proc.returncode, out, err, True


def cli(module: str, args, *, timeout: float = 8.0, extra_env=None):
    rc, out, err, to = child([PY, "-m", module, *args], timeout=timeout, extra_env=extra_env)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), to


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # zombies still answer kill(0); treat them as gone
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except OSError:
        return True


# ------------------------------------------------------------------------------------------------------------------ audit-hook harness
_AUDIT_PRELUDE = r'''
import sys, os, json
_events = []
_WATCH = json.loads(os.environ.get("ADV_WATCH", "[]"))
def _hook(ev, args):
    try:
        if ev in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "pty.spawn"):
            _events.append([ev, repr(args)[:200]])
        elif ev == "open":
            path, mode, flags = args
            m = mode if isinstance(mode, str) else ""
            wr = any(c in m for c in "wax+") or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if wr:
                _events.append([ev, repr(path)[:200] + " mode=" + repr(mode)])
        elif ev in ("os.mkdir", "os.rename", "os.replace", "os.remove", "os.rmdir", "os.symlink", "os.link", "os.truncate", "os.chmod", "os.chown", "shutil.rmtree", "shutil.copyfile", "shutil.move", "os.mkfifo", "os.utime"):
            _events.append([ev, repr(args)[:200]])
        elif ev == "socket.connect" or ev == "socket.getaddrinfo":
            _events.append([ev, repr(args)[:120]])
    except Exception:
        pass
sys.addaudithook(_hook)
'''

_AUDIT_EPILOGUE = r'''
sys.stdout.flush()
sys.stderr.write("\n@@ADV_EVENTS@@" + json.dumps(_events) + "\n")
'''


def audited(body: str, *, timeout: float = 8.0, env=None):
    """Run `body` (python source) in a fresh interpreter under an audit hook. The body may print results to stdout.
    Returns (rc, stdout, stderr_without_marker, events) where events lists launches / exec / file writes / mutations / sockets."""
    code = _AUDIT_PRELUDE + "\nsys.path.insert(0, %r)\n" % TOOLS + textwrap.dedent(body) + "\n" + _AUDIT_EPILOGUE
    rc, out, err, to = child([PY, "-B", "-c", code], timeout=timeout, extra_env=env)
    err_s = err.decode("utf-8", "replace")
    events = []
    if "@@ADV_EVENTS@@" in err_s:
        err_s, _, tail = err_s.partition("\n@@ADV_EVENTS@@")
        events = json.loads(tail.strip() or "[]")
    return rc, out.decode("utf-8", "replace"), err_s, events, to


# ------------------------------------------------------------------------------------------------------------------ fixtures
def pcm_tone(n: int, channels: int = 1, seed: int = 1, amp: int = 8000) -> list:
    """n frames of deterministic pseudo-random int16 (tuples of `channels`)."""
    from adv_oracle import Rng
    r = Rng(seed)
    return [tuple(r.between(-amp, amp) for _ in range(channels)) for _ in range(n)]


def pack_pcm(frames) -> bytes:
    return b"".join(struct.pack("<h", v) for fr in frames for v in fr)


def fmt_chunk(tag=1, channels=1, rate=48000, byte_rate=None, align=None, bits=16, cb=None) -> bytes:
    align = channels * bits // 8 if align is None else align
    byte_rate = rate * align if byte_rate is None else byte_rate
    body = struct.pack("<HHIIHH", tag, channels, rate, byte_rate, align, bits)
    if cb is not None:
        body += struct.pack("<H", cb)
    return chunk(b"fmt ", body, pad=False)


def chunk(tag: bytes, body: bytes, size=None, pad=True) -> bytes:
    size = len(body) if size is None else size
    out = tag + struct.pack("<I", size) + body
    if pad and len(body) % 2:
        out += b"\x00"
    return out


def riff(chunks: list, riff_size=None, trailing: bytes = b"") -> bytes:
    body = b"WAVE" + b"".join(chunks)
    size = len(body) if riff_size is None else riff_size
    return b"RIFF" + struct.pack("<I", size) + body + trailing


def wav_from_frames(frames, channels: int = 1, **kw) -> bytes:
    return riff([fmt_chunk(channels=channels), chunk(b"data", pack_pcm(frames))], **kw)


def manifest(*, fps=(30, 1), total=30, channels=1, gap="ALLOW_ZERO_FILL", overlap=None, clipping=None, assets=(), events=(), scope=None, episode_sha=None) -> dict:
    pol = {"gap": gap}
    if overlap:
        pol["overlap"] = overlap
    if clipping:
        pol["clipping"] = clipping
    tl = {"fps": {"n": fps[0], "d": fps[1]}, "total_frames": total}
    if episode_sha:
        tl["episode_sha256"] = episode_sha
    return {"manifest_version": 1, "timeline": tl, "output": {"sample_rate": 48000, "channels": channels, "bits": 16},
            "scope": {"event_ids": list(scope) if scope is not None else [e["id"] for e in events]},
            "policy": pol, "assets": list(assets), "events": list(events)}


def jb(doc) -> bytes:
    try:
        return json.dumps(doc, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(doc, ensure_ascii=True).encode("ascii")


def avmux_manifest(*, route="MOV_PCM_S16LE", video_path="v.mp4", video_bytes=b"synthetic-not-a-video", wav_path="a.wav", wav_bytes_=b"", width=64, height=64,
                   fps=(30, 1), frames=3, channels=1, samples=None, receipt=None, ref="adv-selection") -> dict:
    d = {"avmux_version": 1, "route": route, "selection": {"ref": ref},
         "video": {"state": "USE", "path": video_path, "sha256": sha256(video_bytes),
                   "declared": {"codec": "h264", "width": width, "height": height, "fps": {"n": fps[0], "d": fps[1]}, "total_frames": frames}},
         "audio": {"state": "USE", "path": wav_path, "sha256": sha256(wav_bytes_),
                   "declared": {"sample_rate": 48000, "channels": channels, "sample_count": samples}}}
    if receipt is not None:
        d["audio"]["receipt"] = receipt
    return d


def write_exec(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o755)


# ------------------------------------------------------------------------------------------------------------------ minimal sfnt (cmap only)
def sfnt_font(codepoints, *, fmt=4, magic=b"\x00\x01\x00\x00", groups=None) -> bytes:
    """Smallest sfnt that video_core.fontcheck accepts: one 'cmap' table with one Unicode subtable (format 4 or 12) mapping every
    listed code point to glyph 1. Independent of tests/fontbuilder.py."""
    cps = sorted(set(codepoints))
    if fmt == 4:
        segs = [(c, c) for c in cps if c <= 0xFFFE] + [(0xFFFF, 0xFFFF)]
        n = len(segs)
        ends = b"".join(struct.pack(">H", e) for _s, e in segs)
        starts = b"".join(struct.pack(">H", s) for s, _e in segs)
        deltas = b"".join(struct.pack(">H", (1 - s) & 0xFFFF if s != 0xFFFF else 1) for s, _e in segs)
        ro = b"\x00\x00" * n
        sub = struct.pack(">HHHHHHH", 4, 16 + 8 * n, 0, 2 * n, 0, 0, 0) + ends + b"\x00\x00" + starts + deltas + ro
        sub = sub[:2] + struct.pack(">H", len(sub)) + sub[4:]
    else:
        gl = groups if groups is not None else [(c, c, 1) for c in cps]
        body = b"".join(struct.pack(">III", s, e, g) for s, e, g in gl)
        sub = struct.pack(">HHIII", 12, 0, 16 + len(body), 0, len(gl)) + body
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1 if fmt == 4 else 10, 12) + sub
    head = magic + struct.pack(">HHHH", 1, 0, 0, 0)
    rec = b"cmap" + struct.pack(">III", 0, 12 + 16, len(cmap))
    return head + rec + cmap
