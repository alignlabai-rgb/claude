"""portable-r2 core. DIFFERENCES from ../portable/portable_core.py (see R2_CHANGES.md and the stored diff): (1) every comparison uses json_equiv.equivalent (precise JSON-compatible
structural rule; tuple == list; bool != int != float; unsupported/non-finite values raise instead of being stringified); (2) profiles are loaded through a strict validating loader;
(3) reviewed entries need a non-empty reviewed_by_ref and NOT-A-REVIEW entries can never produce a green run; (4) the result writer no longer stringifies unsupported values.

Portable acceptance harness — core (Python 3.11+ stdlib only; no shell=True, no signals/setsid/fork, no chmod-executable, no global env changes).

Responsibilities kept OUTSIDE the library under test and outside the child wrapper:
  * every child is started by THIS driver with a hard wall limit (<= 10 s), bounded capture (<= 128 KiB per stream retained = 256 KiB per child),
    a flood kill, serial execution, a finite child count and a documented total wall budget;
  * the safety envelope (deny hook) lives in child_runner.py, which is harness code, not the runner under test;
  * results are written only to a NEW caller-selected directory (exclusive create, no overwrite).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import json_equiv

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD_RUNNER = os.path.join(HERE, "child_runner.py")
HARNESS_VERSION = "portable-contract-harness/2-r2"

PER_CHILD_TIMEOUT = 10.0            # seconds, hard
STREAM_CAP = 128 * 1024             # bytes retained per stream -> <= 256 KiB per child
FLOOD_LIMIT = 1024 * 1024           # total bytes read from a child before it is killed
MAX_CHILDREN = 700                  # finite child count per run (contract + controls + mutation)
DEFAULT_BUDGET_S = 420.0
MAX_BUDGET_S = 900.0

FORBIDDEN_FLAGS = ("--render", "--mix", "--mux", "--ffmpeg", "--ffprobe")     # execute media tools / name tool paths: never passed; abbreviations too
FORBIDDEN_SUBCOMMANDS = {"video_core": ("inspect",)}                          # launches ffprobe
ALLOWED_MODULES = ("video_core", "video_core.audiomix", "video_core.avmux")


class PolicyViolation(Exception):
    """The harness refused to launch something outside its read-only boundaries."""


class BudgetExhausted(Exception):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def policy_check(module: str, args: list) -> None:
    if module not in ALLOWED_MODULES:
        raise PolicyViolation(f"module {module!r} is not allowed")
    for a in args:
        if isinstance(a, str) and a.startswith("--") and len(a) >= 3:
            flag = a.split("=", 1)[0]
            for forbidden in FORBIDDEN_FLAGS:
                if forbidden.startswith(flag):          # also argparse prefix abbreviations such as --ren / --mu / --ff
                    raise PolicyViolation(f"flag {a!r} could execute a media tool or name a tool path: refused before any child exists")
    if args and args[0] in FORBIDDEN_SUBCOMMANDS.get(module, ()):
        raise PolicyViolation(f"subcommand {args[0]!r} launches ffprobe: refused before any child exists")


# ---------------------------------------------------------------------------------------------------------------- source inventory / pin

def _eol(b: bytes) -> bytes:
    return b if b"\x00" in b else b.replace(b"\r\n", b"\n")


def _hash_file(path: str):
    """Constant-memory sha256 of the raw bytes and of the CRLF->LF normalised bytes (binary files, i.e. containing NUL, are not normalised). Returns (size, raw, eol)."""
    raw, size, has_nul, has_crlf = hashlib.sha256(), 0, False, False
    prev = b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            raw.update(chunk)
            size += len(chunk)
            has_nul = has_nul or b"\x00" in chunk
            has_crlf = has_crlf or b"\r\n" in (prev[-1:] + chunk)
            prev = chunk
    if has_nul or not has_crlf:
        return size, raw.hexdigest(), raw.hexdigest()
    eol, carry = hashlib.sha256(), b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            data = carry + chunk
            carry = b"\r" if data.endswith(b"\r") else b""
            if carry:
                data = data[:-1]
            eol.update(data.replace(b"\r\n", b"\n"))
    eol.update(carry)
    return size, raw.hexdigest(), eol.hexdigest()


def inventory(root: str) -> dict:
    """Complete actual hash inventory of the library root. Excluded (and listed): __pycache__, .git, and the top-level `evidence` directory."""
    root = os.path.realpath(root)
    files, excluded, skipped = [], [], []
    for dp, dn, fn in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dp, root)
        keep = []
        for d in sorted(dn):
            full = os.path.join(dp, d)
            relp = (d if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/" + d)
            if d in ("__pycache__", ".git") or relp == "evidence":
                excluded.append(relp)
            elif os.path.islink(full):
                skipped.append({"path": relp, "reason": "symlinked directory not followed"})
            else:
                keep.append(d)
        dn[:] = keep
        for f in sorted(fn):
            full = os.path.join(dp, f)
            relp = (f if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/" + f)
            if os.path.islink(full) or not os.path.isfile(full):
                skipped.append({"path": relp, "reason": "not a regular file"})
                continue
            if f.endswith((".pyc", ".pyo")):
                excluded.append(relp)
                continue
            size, raw, eol = _hash_file(full)
            files.append({"path": relp, "bytes": size, "sha256": raw, "sha256_eol_normalized": eol})
    files.sort(key=lambda x: x["path"])

    def digest(items, key):
        return sha256_bytes("".join(f"{x['path']}\t{x[key]}\n" for x in items).encode("utf-8"))
    pkg = [x for x in files if x["path"].startswith("video_core/")]
    return {"root_basename": os.path.basename(root), "files": files, "file_count": len(files), "package_file_count": len(pkg), "excluded": sorted(excluded), "skipped": skipped,
            "package_digest_raw": digest(pkg, "sha256"), "package_digest_eol": digest(pkg, "sha256_eol_normalized"),
            "all_digest_raw": digest(files, "sha256"), "all_digest_eol": digest(files, "sha256_eol_normalized"),
            "digest_definition": "sha256 over sorted lines 'posix-relative-path<TAB>file-sha256<LF>'; package = files under video_core/; all = every file not excluded"}


def pin_status(inv: dict, pin: dict | None) -> dict:
    """Compare an inventory with a profile's source pin. Line-ending-only differences are LABELLED, never silently normalised."""
    if not pin:
        return {"status": "NO_PIN", "detail": "no profile pin supplied"}
    raw_pkg, raw_all = inv["package_digest_raw"] == pin.get("package_digest_raw"), inv["all_digest_raw"] == pin.get("all_digest_raw")
    if raw_pkg and raw_all:
        return {"status": "PINNED_EXACT", "detail": "package and all files byte-identical to the pin"}
    if raw_pkg:
        return {"status": "PACKAGE_PINNED_OTHER_FILES_DIFFER", "detail": "video_core/ is byte-identical to the pin; other inventoried files (tests/docs/schemas) differ"}
    if inv["package_digest_eol"] == pin.get("package_digest_eol"):
        return {"status": "PINNED_MODULO_LINE_ENDINGS", "detail": "video_core/ differs from the pin only by CRLF/LF; this is a label, not an equivalence claim for other differences"}
    return {"status": "UNPINNED", "detail": "video_core/ differs from the pin (a candidate or modified source)"}


# ---------------------------------------------------------------------------------------------------------------- profile + check classification

NOT_A_REVIEW_MARKERS = ("NOT A REVIEW", "SELF-TEST ONLY")
_HEX64 = re.compile(r"[0-9a-f]{64}")
PIN_DIGESTS = ("package_digest_raw", "package_digest_eol", "all_digest_raw", "all_digest_eol")


class ProfileError(ValueError):
    """The profile file is not acceptable. Never repaired, never partially applied."""


def _no_dup_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ProfileError(f"duplicate key {k!r} in the profile")
        d[k] = v
    return d


def _no_const(name):
    raise ProfileError(f"non-finite number {name} in the profile")


def validate_profile_doc(doc) -> dict:
    """Strict validation of an already-parsed profile (raises ProfileError). Unknown extra top-level keys are allowed (descriptions, proposals); the three used parts are strict."""
    if not isinstance(doc, dict):
        raise ProfileError("profile must be a JSON object")
    if type(doc.get("profile_version")) is not int or doc["profile_version"] != 1:
        raise ProfileError("profile_version must be the integer 1")
    pin = doc.get("source_pin")
    if not isinstance(pin, dict):
        raise ProfileError("source_pin must be an object")
    for k in PIN_DIGESTS:
        if not isinstance(pin.get(k), str) or not _HEX64.fullmatch(pin[k]):
            raise ProfileError(f"source_pin.{k} must be a 64-digit lowercase hex digest")
    rev = doc.get("reviewed_expectations")
    if not isinstance(rev, dict):
        raise ProfileError("reviewed_expectations must be an object (empty when nothing is reviewed)")
    for key, ent in rev.items():
        if not isinstance(key, str) or "::" not in key:
            raise ProfileError(f"reviewed key {key!r} must look like '<case>::<check name>'")
        if not isinstance(ent, dict) or "observed" not in ent:
            raise ProfileError(f"reviewed entry {key!r} must be an object with an 'observed' value")
        extra = set(ent) - {"observed", "reviewed_by_ref", "note"}
        if extra:
            raise ProfileError(f"reviewed entry {key!r} has unknown fields {sorted(extra)}")
        ref = ent.get("reviewed_by_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ProfileError(f"reviewed entry {key!r} needs a non-empty reviewed_by_ref (who reviewed it); a missing reference is never assumed")
        if "note" in ent and not isinstance(ent["note"], str):
            raise ProfileError(f"reviewed entry {key!r}: note must be a string")
        try:
            json_equiv.canon(ent["observed"])
        except json_equiv.UnsupportedValue as exc:
            raise ProfileError(f"reviewed entry {key!r}: observed value is unsupported ({exc})") from None
    return doc


def parse_profile_bytes(data: bytes) -> dict:
    if len(data) > 1_000_000:
        raise ProfileError("profile larger than 1 MB")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ProfileError("profile has a UTF-8 BOM")
    try:
        text = data.decode("utf-8")
        doc = json.loads(text, object_pairs_hook=_no_dup_pairs, parse_constant=_no_const)
    except UnicodeDecodeError:
        raise ProfileError("profile is not valid UTF-8") from None
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile is not valid JSON: {exc}") from None
    return validate_profile_doc(doc)


def load_profile_file(path: str) -> tuple:
    """-> (doc, raw_bytes). Reads at most 1 MB + 1 byte."""
    with open(path, "rb") as fh:
        raw = fh.read(1_000_001)
    return parse_profile_bytes(raw), raw


class Profile:
    def __init__(self, doc: dict | None, inv: dict):
        self.doc = doc or {}
        self.pin = pin_status(inv, self.doc.get("source_pin"))
        self.applicable = self.pin["status"] in ("PINNED_EXACT", "PACKAGE_PINNED_OTHER_FILES_DIFFER", "PINNED_MODULO_LINE_ENDINGS")
        self.reviewed = self.doc.get("reviewed_expectations", {}) if self.applicable else {}
        self.reviewed_ignored = (self.doc.get("reviewed_expectations", {}) if not self.applicable else {})
        self.applied_not_a_review = []          # keys whose applied entry carries a NOT-A-REVIEW marker (a dry run can never be a green run)

    def reviewed_for(self, key: str):
        return self.reviewed.get(key)


def _is_not_a_review(ref) -> bool:
    return isinstance(ref, str) and any(m in ref.upper() for m in NOT_A_REVIEW_MARKERS)


def classify_check(layer: str, key: str, expected, observed, profile: Profile | None) -> dict:
    """layer in {LIBRARY_ENFORCED, LIBRARY_PERMISSIVE, CALLER_GUARD_DEMO, HARNESS_CONTROL}. Every comparison is json_equiv.equivalent.
    An unsupported/non-finite value on either side is an ERROR (never stringified, never rescued). Reviewed expectations can only change a LIBRARY_* outcome; guard demos and
    harness controls are never rescued or failed by a profile."""
    try:
        same = json_equiv.equivalent(expected, observed)
    except json_equiv.UnsupportedValue as exc:
        return {"outcome": "ERROR", "error": f"unsupported value in a comparison: {exc}"}

    def reviewed_match():
        rev = profile.reviewed_for(key) if profile else None
        if rev is None:
            return None
        try:
            if json_equiv.equivalent(rev.get("observed"), observed):
                out = {"outcome": "REVIEWED_CHANGE", "reviewed_by_ref": rev.get("reviewed_by_ref"), "note": rev.get("note")}
                if _is_not_a_review(rev.get("reviewed_by_ref")):
                    out["review_marker"] = "NOT_A_REVIEW"
                    profile.applied_not_a_review.append(key)
                return out
        except json_equiv.UnsupportedValue as exc:
            return {"outcome": "ERROR", "error": f"unsupported value in a reviewed comparison: {exc}"}
        return None
    if layer == "LIBRARY_ENFORCED":
        if same:
            return {"outcome": "PASS"}
        return reviewed_match() or {"outcome": "FAIL"}
    if layer == "LIBRARY_PERMISSIVE":
        if same:
            return {"outcome": "OBSERVED_OPEN_GAP"}
        return reviewed_match() or {"outcome": "REVIEW_REQUIRED", "why": "observed behaviour differs from the pinned OPEN GAP; not adopted automatically"}
    if layer == "CALLER_GUARD_DEMO":
        return {"outcome": "GUARD_OK" if same else "GUARD_MISMATCH"}
    if layer == "HARNESS_CONTROL":
        return {"outcome": "CONTROL_OK" if same else "CONTROL_FAILED"}
    raise ValueError(layer)


BAD_OUTCOMES = {"FAIL", "ERROR", "GUARD_MISMATCH", "CONTROL_FAILED", "BUDGET_EXHAUSTED", "POLICY_VIOLATION"}


# ---------------------------------------------------------------------------------------------------------------- output directory (exclusive create)

class OutDir:
    def __init__(self, path: str):
        self.path = path

    @classmethod
    def create_new(cls, path: str, forbidden_inside: list) -> "OutDir":
        p = os.path.abspath(path)
        for f in forbidden_inside:
            if inside(p, f):
                raise SystemExit(f"refused: output directory {p} is inside {f} (never write into the repository or library under test)")
        if os.path.lexists(p):
            raise SystemExit(f"refused: output directory {p} already exists (no overwrite)")
        parent = os.path.dirname(p)
        if not os.path.isdir(parent):
            raise SystemExit(f"refused: parent directory {parent} does not exist")
        os.mkdir(p)
        return cls(p)

    def write(self, rel: str, data: bytes) -> str:
        full = os.path.join(self.path, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "xb") as fh:           # 'x': refuse to overwrite anything
            fh.write(data)
        return full

    def write_json(self, rel: str, obj) -> str:
        return self.write(rel, (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))      # strict: no default=, so nothing is stringified


def inside(path: str, parent: str) -> bool:
    try:
        a, b = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(parent))
        return os.path.commonpath([a, b]) == b
    except ValueError:        # different drives on Windows
        return False


def find_repo_root(start: str) -> str | None:
    p = os.path.realpath(start)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        q = os.path.dirname(p)
        if q == p:
            return None
        p = q


# ---------------------------------------------------------------------------------------------------------------- worlds + path normalisation

class World:
    """An owned temp world: root/{assets,docs,out,empty_bin,tmp}. Only copied synthetic fixture bytes are ever placed in it."""

    def __init__(self, parent: str, name: str):
        self.root = os.path.realpath(os.path.join(parent, name))
        os.makedirs(self.root)
        self.assets, self.docs, self.out, self.empty_bin, self.tmp = (os.path.join(self.root, d) for d in ("assets", "docs", "out", "empty_bin", "tmp"))
        for d in (self.assets, self.docs, self.out, self.empty_bin, self.tmp):
            os.mkdir(d)

    def put(self, rel: str, data: bytes, area: str = "assets") -> str:
        p = os.path.join(getattr(self, area), *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        return sha256_bytes(data)

    def put_json(self, rel: str, obj, area: str = "docs") -> str:
        return self.put(rel, (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"), area)

    def tree(self, area: str) -> dict:
        base = getattr(self, area)
        out = {}
        for dp, dn, fn in os.walk(base):
            dn.sort()
            for d in dn:
                out[os.path.relpath(os.path.join(dp, d), base).replace(os.sep, "/")] = "DIR"
            for f in sorted(fn):
                full = os.path.join(dp, f)
                with open(full, "rb") as fh:
                    out[os.path.relpath(full, base).replace(os.sep, "/")] = sha256_bytes(fh.read())
        return dict(sorted(out.items()))

    def snapshot(self) -> dict:
        return {a: self.tree(a) for a in ("assets", "docs", "out")}

    def expand(self, token: str) -> str:
        return token.replace("{assets}", self.assets).replace("{docs}", self.docs).replace("{out}", self.out).replace("{root}", self.root)


def make_normalizer(roots: list):
    """Replace every occurrence of the given absolute roots (backslash and slash forms, case-insensitive on Windows) by the paired placeholder."""
    pairs = []
    for real, ph in roots:
        for form in {real, real.replace("\\", "/"), real.replace("\\", "\\\\")}:
            pairs.append((form, ph))
    pairs.sort(key=lambda t: -len(t[0]))
    flags = re.IGNORECASE if os.name == "nt" else 0
    pats = [(re.compile(re.escape(a), flags), ph) for a, ph in pairs]

    def norm_text(s: str) -> str:
        for pat, ph in pats:
            s = pat.sub(ph, s)
        return s

    def norm_obj(o):
        if isinstance(o, str):
            return norm_text(o)
        if isinstance(o, list):
            return [norm_obj(x) for x in o]
        if isinstance(o, dict):
            return {norm_text(k) if isinstance(k, str) else k: norm_obj(v) for k, v in o.items()}
        return o
    return norm_text, norm_obj


# the plans estimate their own argv size from the REAL paths: these numbers depend on the scratch path length, so they are placeholdered for hashing
_LEN_DEP = re.compile(r'("estimated_(?:argv_bytes|windows_cmdline_utf16_units)":\s*)\d+')


# ---------------------------------------------------------------------------------------------------------------- bounded child driver

class ChildResult:
    def __init__(self):
        self.status = "NOT_RUN"
        self.exit_code = None
        self.stdout = b""
        self.stderr = b""
        self.stdout_total = 0
        self.stderr_total = 0
        self.truncated = {"stdout": False, "stderr": False}
        self.elapsed = 0.0
        self.log = None
        self.json = None
        self.stdout_text = ""

    @property
    def denials(self):
        return (self.log or {}).get("denials", [])


def _reader(stream, store: dict, key: str, cap: int, counter: dict, lock, flood: threading.Event, proc):
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            with lock:
                counter["total"] += len(chunk)
                store[key + "_total"] += len(chunk)
                if counter["total"] > FLOOD_LIMIT and not flood.is_set():
                    flood.set()
                    try:
                        proc.kill()          # killed by the reader itself: a fast flooder cannot finish before a polling loop notices
                    except OSError:
                        pass
            if len(store[key]) < cap:
                store[key] += chunk[: cap - len(store[key])]
            if store[key + "_total"] > cap:
                store["trunc_" + key] = True
    except (OSError, ValueError):
        pass


def run_child(job: dict, *, cwd: str, env: dict, log_dir: str, timeout: float = PER_CHILD_TIMEOUT) -> ChildResult:
    """Start ONE isolated child (sys.executable -I -S -B -X utf8 child_runner.py JOB). Serial, bounded, killed on timeout/flood. Never shell=True."""
    res = ChildResult()
    timeout = min(float(timeout), PER_CHILD_TIMEOUT)
    log_path = os.path.join(log_dir, "child-log-%d.json" % time.monotonic_ns())
    job = dict(job, log_path=log_path)
    argv = [sys.executable, "-I", "-S", "-B", "-X", "utf8", CHILD_RUNNER, json.dumps(job, ensure_ascii=True)]
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, shell=False, close_fds=True)
    except OSError as exc:
        res.status = "LAUNCH_FAILED"
        res.stderr = repr(exc).encode("utf-8", "replace")[:STREAM_CAP]
        return res
    store = {"stdout": b"", "stderr": b"", "stdout_total": 0, "stderr_total": 0, "trunc_stdout": False, "trunc_stderr": False}
    counter, lock, flood = {"total": 0}, threading.Lock(), threading.Event()
    threads = [threading.Thread(target=_reader, args=(proc.stdout, store, "stdout", STREAM_CAP, counter, lock, flood, proc), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, store, "stderr", STREAM_CAP, counter, lock, flood, proc), daemon=True)]
    for t in threads:
        t.start()
    status = "OK"
    deadline = t0 + timeout
    while True:
        try:
            proc.wait(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            pass
        if flood.is_set():
            status = "FLOOD_KILLED"
            proc.kill()
            break
        if time.monotonic() >= deadline:
            status = "TIMEOUT"
            proc.kill()
            break
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        status = status + "+UNREAPED"
    for t in threads:
        t.join(timeout=2)
    if flood.is_set() and not status.startswith("TIMEOUT"):
        status = "FLOOD_KILLED" + ("+UNREAPED" if status.endswith("+UNREAPED") else "")
    for s in (proc.stdout, proc.stderr):
        try:
            s.close()
        except OSError:
            pass
    res.status, res.exit_code, res.elapsed = status, proc.returncode, time.monotonic() - t0
    res.stdout, res.stderr = store["stdout"], store["stderr"]
    res.stdout_total, res.stderr_total = store["stdout_total"], store["stderr_total"]
    res.truncated = {"stdout": store["trunc_stdout"], "stderr": store["trunc_stderr"]}
    try:
        if os.path.getsize(log_path) <= 256 * 1024:
            with open(log_path, encoding="utf-8") as fh:
                res.log = json.load(fh)
        os.remove(log_path)
    except (OSError, ValueError):
        res.log = None
    return res


def child_env(world: World) -> dict:
    """Minimal environment for the CHILD only (the driver's own environment is never changed). PATH is an owned empty directory: no media tool is reachable."""
    env = {"PATH": world.empty_bin, "TEMP": world.tmp, "TMP": world.tmp, "TMPDIR": world.tmp}
    for k in ("SYSTEMROOT", "SystemRoot", "WINDIR"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


# ---------------------------------------------------------------------------------------------------------------- run context

class Run:
    def __init__(self, *, library_root: str, scratch: str, out: OutDir | None, budget_s: float, profile: Profile, strict: bool, normalize_stored: bool, group: str = "contract"):
        self.library_root, self.scratch, self.out = library_root, scratch, out
        self.budget_s, self.profile, self.strict, self.normalize_stored = budget_s, profile, strict, normalize_stored
        self.t0 = time.monotonic()
        self.children = 0
        self.group = group
        self.invocation_no = 0
        self.records: list = []
        self.launch_log: list = []        # every launch the driver attempted (after policy) - for accounting
        self.refused: list = []
        self.world_seq = 0

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def check_budget(self):
        if self.elapsed() > self.budget_s:
            raise BudgetExhausted(f"total wall budget {self.budget_s:.0f}s exhausted after {self.children} children")
        if self.children >= MAX_CHILDREN:
            raise BudgetExhausted(f"child count limit {MAX_CHILDREN} reached")

    def new_world(self, label: str, dirname: str | None = None) -> World:
        self.world_seq += 1
        parent = os.path.join(self.scratch, "w%03d-%s" % (self.world_seq, label))
        os.makedirs(parent)
        root_name = dirname or "w"
        return World(parent, root_name)


class Res:
    """What a case sees of one CLI call."""

    def __init__(self, child: ChildResult, norm_text, norm_obj, record_no):
        self.child = child
        self.exit_code = child.exit_code
        self.status = child.status
        self.stdout_bytes = child.stdout
        self.record_no = record_no
        text = child.stdout.decode("utf-8", "replace")
        self.text = _LEN_DEP.sub(r'\1"<temp-path-length dependent>"', norm_text(text))
        try:
            self.json = norm_obj(json.loads(text)) if text.strip() else None
        except ValueError:
            self.json = None
        self.stderr_text = child.stderr.decode("utf-8", "replace")

    @property
    def issue_codes(self) -> list:
        j = self.json
        if isinstance(j, dict) and isinstance(j.get("error"), dict):
            return [i["code"] for i in j["error"].get("issues", [])]
        return []

    @property
    def denials(self):
        return self.child.denials


def cli(run: Run, world: World, module: str, args: list, *, label: str, case_id: str, timeout: float = PER_CHILD_TIMEOUT, group: str | None = None) -> Res:
    expanded = [world.expand(a) if isinstance(a, str) else a for a in args]
    try:
        policy_check(module, expanded)
    except PolicyViolation:
        run.refused.append({"module": module, "args": [str(a) for a in args][:8]})
        raise
    run.check_budget()
    job = {"mode": "cli", "library_root": run.library_root, "module": module, "args": expanded, "world_root": world.root}
    run.children += 1
    run.launch_log.append({"module": module, "group": group or run.group, "case": case_id})
    child = run_child(job, cwd=world.root, env=child_env(world), log_dir=world.tmp, timeout=timeout)
    norm_text, norm_obj = make_normalizer([(world.root, "<WORLD>")])
    run.invocation_no += 1
    res = Res(child, norm_text, norm_obj, run.invocation_no)
    store_invocation(run, world, case_id, label, module, args, expanded, child, res, group or run.group)
    return res


def store_invocation(run: Run, world, case_id, label, module, args, expanded, child: ChildResult, res: Res, group: str):
    rec = {"no": res.record_no, "group": group, "case": case_id, "label": label, "module": module, "status": child.status, "exit_code": child.exit_code, "elapsed_seconds": round(child.elapsed, 3),
           "stdout_bytes_total": child.stdout_total, "stderr_bytes_total": child.stderr_total, "stdout_retained": len(child.stdout), "stderr_retained": len(child.stderr),
           "truncated": child.truncated, "denied_attempts": len(child.denials), "foreign_reads": len((child.log or {}).get("foreign_reads", [])),
           "stdout_sha256_raw_retained": sha256_bytes(child.stdout), "stdout_sha256_normalized": sha256_bytes(res.text.encode("utf-8"))}
    run.records.append(rec)
    if run.out is None:
        return
    norm_text, _ = make_normalizer([(world.root, "<WORLD>"), (run.scratch, "<SCRATCH>"), (run.out.path, "<OUT>"), (run.library_root, "<LIBRARY>")] if run.normalize_stored else [(world.root, "<WORLD>")])
    d = "invocations/%05d-%s" % (res.record_no, re.sub(r"[^A-Za-z0-9_.-]", "_", case_id)[:60])
    shown_argv = [norm_text(a) if isinstance(a, str) else a for a in expanded]
    run.out.write_json(d + "/argv.json", {"python_flags": ["-I", "-S", "-B", "-X", "utf8"], "module": module, "args_as_given": [a if not isinstance(a, str) else a for a in args],
                                          "args_expanded_normalized": shown_argv, "stored_text_normalized": run.normalize_stored})
    rec2 = dict(rec, stored_text_normalized=run.normalize_stored, child_log=(child.log if child.log is None else {k: v for k, v in child.log.items()}))
    so, se = child.stdout, child.stderr
    if run.normalize_stored:
        so = norm_text(so.decode("utf-8", "replace")).encode("utf-8")
        se = norm_text(se.decode("utf-8", "replace")).encode("utf-8")
        if child.log:
            rec2["child_log"] = json.loads(norm_text(json.dumps(child.log, ensure_ascii=False)))
        rec2["stdout_sha256_stored"], rec2["stderr_sha256_stored"] = sha256_bytes(so), sha256_bytes(se)
    run.out.write_json(d + "/status.json", rec2)
    run.out.write(d + "/stdout.bin", so)
    run.out.write(d + "/stderr.bin", se)


def record_control(run: Run, world: World, name: str, child: ChildResult) -> None:
    """r2: store a mechanism-control child like any invocation (bounded stdout/stderr/status, denial log) and count it."""
    norm_text, norm_obj = make_normalizer([(world.root, "<WORLD>")])
    run.invocation_no += 1
    res = Res(child, norm_text, norm_obj, run.invocation_no)
    store_invocation(run, world, "control-" + name, "control " + name, "control:" + name, [], [], child, res, run.group)


# ---------------------------------------------------------------------------------------------------------------- case context

class CaseCtx:
    def __init__(self, run: Run, case_id: str, title: str, refs: list | None = None):
        self.run, self.case_id, self.title = run, case_id, title
        self.checks: list = []
        self.invocations: list = []
        self.refs = refs or []
        self.world: World | None = None
        self.gaps: list = []

    def call(self, world: World, module: str, args: list, label: str, **kw) -> Res:
        r = cli(self.run, world, module, args, label=label, case_id=self.case_id, **kw)
        self.invocations.append({"no": r.record_no, "label": label, "exit_code": r.exit_code, "status": r.status, "denied_attempts": len(r.denials)})
        self._auto(r, label)
        return r

    def _auto(self, r: Res, label: str):
        # harness-level invariants on EVERY library call: the child ran normally, was not killed, and the deny hook saw no attempt
        if r.status != "OK":
            self.add("HARNESS_CONTROL", f"[{label}] child finished normally", "OK", r.status)
        if r.denials:
            self.add("LIBRARY_ENFORCED", f"[{label}] library attempted nothing forbidden (process/network/write/env)", [], [(d["category"], d["event"]) for d in r.denials])

    def add(self, layer: str, name: str, expected, observed, *, gap: str | None = None, hint: str | None = None):
        key = f"{self.case_id}::{name}"
        out = classify_check(layer, key, expected, observed, self.run.profile)
        entry = {"layer": layer, "name": name, "key": key, "expected": expected, "observed": observed, **out}
        if out["outcome"] == "ERROR" and "unsupported value" in out.get("error", ""):
            entry.pop("expected"), entry.pop("observed")       # the unsupported payload is not stored; the error names the path and type
        if gap:
            entry["gap"] = gap
            if gap not in self.gaps:
                self.gaps.append(gap)
        if hint:
            entry["intended_contract_hint"] = hint
        self.checks.append(entry)
        return out["outcome"]

    def observe(self, fn):
        """Evaluate an observation lazily; a candidate that changed the output shape yields a recorded observation error (-> REVIEW_REQUIRED for an open gap), not a crashed case."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return {"__observation_error__": type(exc).__name__ + ": " + str(exc)[:100]}

    # shorthands
    def enforced(self, name, expected, observed):
        return self.add("LIBRARY_ENFORCED", name, expected, observed)

    def open_gap(self, gap, name, pinned_observed, observed, hint):
        return self.add("LIBRARY_PERMISSIVE", name, pinned_observed, observed, gap=gap, hint=hint)

    def guard(self, name, expected, observed):
        return self.add("CALLER_GUARD_DEMO", name, expected, observed)

    def control(self, name, expected, observed):
        return self.add("HARNESS_CONTROL", name, expected, observed)

    def skip(self, name, reason):
        self.checks.append({"layer": "SKIPPED", "name": name, "key": f"{self.case_id}::{name}", "outcome": "SKIPPED", "reason": reason})

    def result(self) -> dict:
        outs = [c["outcome"] for c in self.checks]
        if any(o in BAD_OUTCOMES for o in outs):
            status = "FAIL"
        elif "REVIEW_REQUIRED" in outs:
            status = "REVIEW_REQUIRED"
        elif "REVIEWED_CHANGE" in outs:
            status = "PASS_WITH_REVIEWED_CHANGES"
        else:
            status = "PASS"
        return {"case": self.case_id, "title": self.title, "status": status, "checks": self.checks, "invocations": self.invocations, "gaps": self.gaps, "source_refs": self.refs}


def summarize(case_results: list) -> dict:
    by_layer: dict = {}
    for cr in case_results:
        for c in cr["checks"]:
            by_layer.setdefault(c["layer"], {}).setdefault(c["outcome"], 0)
            by_layer[c["layer"]][c["outcome"]] += 1
    cases = {}
    for cr in case_results:
        cases[cr["status"]] = cases.get(cr["status"], 0) + 1
    return {"cases_total": len(case_results), "cases_by_status": dict(sorted(cases.items())), "checks_by_layer_and_outcome": {k: dict(sorted(v.items())) for k, v in sorted(by_layer.items())}}


def run_case(run: Run, fn) -> dict:
    """Run one case function (ctx-taking). Exceptions become recorded outcomes; they are never swallowed silently."""
    ctx = CaseCtx(run, fn.case_id, fn.title, getattr(fn, "refs", []))
    t = time.monotonic()
    try:
        fn(ctx)
    except BudgetExhausted as exc:
        ctx.checks.append({"layer": "HARNESS_CONTROL", "name": "budget", "key": f"{ctx.case_id}::budget", "outcome": "BUDGET_EXHAUSTED", "error": str(exc)})
    except PolicyViolation as exc:
        ctx.checks.append({"layer": "HARNESS_CONTROL", "name": "policy", "key": f"{ctx.case_id}::policy", "outcome": "POLICY_VIOLATION", "error": str(exc)})
    except Exception:  # noqa: BLE001
        ctx.checks.append({"layer": "HARNESS_CONTROL", "name": "case raised", "key": f"{ctx.case_id}::raised", "outcome": "ERROR", "error": traceback.format_exc()[-1500:]})
    r = ctx.result()
    r["elapsed_seconds"] = round(time.monotonic() - t, 3)
    return r
