"""External tool discovery and capability probing.

Discovery (`locate`) only looks at the filesystem/PATH and never executes anything,
so `plan` can report it without launching a process. Probing executes the tool with
fixed, bounded arguments (`-version`, `-encoders`, `-filters`, `-demuxers`).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass

from . import capabilities, runner
from .limits import Limits

_VERSION = re.compile(r"^(ffmpeg|ffprobe) version (\S+)")


def locate(name: str, explicit: str | None = None) -> tuple[str | None, str]:
    """Return (absolute path or None, how). `explicit` wins over PATH lookup."""
    if explicit:
        if os.sep in explicit or (os.altsep and os.altsep in explicit):
            path = os.path.abspath(explicit)
            how = "explicit"
        else:
            path = shutil.which(explicit)
            how = "explicit-name-on-PATH"
    else:
        path = shutil.which(name)
        how = "PATH"
    if not path:
        return None, how
    try:
        st = os.stat(path)
    except OSError:
        return None, how
    if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
        return None, how
    return path, how


def sha256_file(path: str, max_bytes: int | None = None) -> str:
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("file grew beyond the byte limit while hashing")
            h.update(chunk)
    return h.hexdigest()


def probe_env() -> dict:
    env = {"PATH": os.environ.get("PATH", "")}
    if os.name == "nt":
        for key in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


@dataclass
class ToolRecord:
    name: str
    path: str
    how: str
    version: str | None = None
    version_line: str | None = None
    executable_sha256: str | None = None
    executions: list | None = None
    problem: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "discovery": self.how, "version": self.version,
            "version_line": self.version_line, "executable_sha256": self.executable_sha256, "problem": self.problem,
        }


def probe_version(name: str, path: str, how: str, limits: Limits, cwd: str) -> tuple[ToolRecord, list]:
    rec = ToolRecord(name=name, path=path, how=how)
    runs = []
    res = runner.run_bounded(
        [path, "-hide_banner", "-version"],
        phase=f"probe_version:{name}", timeout_s=limits.probe_timeout_seconds,
        stdout_limits=(65536, 0), stderr_limits=(2048, 2048), max_drain_bytes=limits.max_drain_bytes,
        cwd=cwd, env=probe_env(),
    )
    runs.append(res)
    runner.raise_if_interrupted(res)  # a cancel is not "tool unavailable": stop here, launch nothing further
    if not res.ok:
        rec.problem = f"version probe did not exit cleanly: {res.describe()}"
        return rec, runs
    first = res.stdout_bytes().decode("utf-8", "replace").splitlines()[:1]
    m = _VERSION.match(first[0]) if first else None
    if not m or m.group(1) != name:
        rec.problem = "version output not recognised"
        return rec, runs
    rec.version, rec.version_line = m.group(2), first[0][:200]
    try:
        rec.executable_sha256 = sha256_file(path)
    except OSError as exc:
        rec.problem = f"cannot hash executable: {exc.__class__.__name__}"
    return rec, runs


def _listing(path: str, flag: str, limits: Limits, cwd: str):
    """Run one listing command. Returns (RunResult, Listing or None)."""
    res = runner.run_bounded(
        [path, "-hide_banner", flag], phase=f"probe_capability:{flag}", timeout_s=limits.probe_timeout_seconds,
        stdout_limits=(1024 * 1024, 0), stderr_limits=(2048, 2048), max_drain_bytes=limits.max_drain_bytes, cwd=cwd, env=probe_env(),
    )
    runner.raise_if_interrupted(res)
    if not res.ok or res.stdout is None or res.stdout.summary()["truncated"]:
        return res, None
    return res, capabilities.PARSERS[flag](res.stdout_bytes().decode("utf-8", "replace"))


def missing_capabilities(path: str, requirements: dict, limits: Limits, cwd: str) -> tuple[dict, list]:
    """Check requirements {'encoders'|'filters'|'demuxers'|'muxers'|'decoders': [...]}. Fail-closed.

    Returns ({'missing': {kind: [names]}, 'probe_failed': [flags], 'details': {flag: {...}}}, run results).
    * missing      -- the listing was understood and the name is truly absent
    * probe_failed -- the listing could not be obtained or understood (non-zero exit, truncated output,
                      or exit 0 but zero recognisable entries, i.e. an unknown layout). Never treated as success.
    """
    flags = {"encoders": "-encoders", "filters": "-filters", "demuxers": "-demuxers", "muxers": "-muxers", "decoders": "-decoders"}
    runs, missing, failed, details = [], {}, [], {}
    for key, flag in flags.items():
        wanted = requirements.get(key) or []
        if not wanted:
            continue
        res, listing = _listing(path, flag, limits, cwd)
        runs.append(res)
        if listing is None:
            failed.append(flag)
            details[flag] = {"reason": f"listing unavailable: {res.describe()}" + (" (output truncated)" if res.ok else "")}
            continue
        details[flag] = listing.summary()
        if listing.recognised == 0:
            failed.append(flag)
            details[flag]["reason"] = "listing format not recognised (no entry line matched a known layout)"
            continue
        gone = [w for w in wanted if w not in listing.names]
        if gone:
            missing[key] = gone
    return {"missing": missing, "probe_failed": failed, "details": details}, runs
