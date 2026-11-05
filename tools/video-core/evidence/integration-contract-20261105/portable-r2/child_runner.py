"""Portable isolated child wrapper (harness-owned; Python 3.11+ stdlib only; no POSIX-only calls).

Started by portable_harness.py as:   <sys.executable> -I -S -B -X utf8 child_runner.py <JOB-JSON>
It is NOT the runner under test: it only (1) installs a DENYING audit hook, (2) runs ONE public CLI of the library under test via runpy
(`python -m MODULE ARGS` semantics) or ONE mechanism control, (3) writes a JSON log through a file handle opened BEFORE the hook existed.

The hook raises HarnessDenied (a BaseException, so library `except Exception/OSError` blocks cannot swallow it) BEFORE the side effect for:
  process   subprocess.*, os.system/exec*/spawn*/posix_spawn/fork*/startfile, _winapi.CreateProcess, ctypes.*, webbrowser.open
  network   socket.* connect/bind/send/resolve events, urllib.Request, http.client.*, ftplib/smtplib/telnetlib
  write     open() for writing/appending/creating, os.mkdir/rmdir/remove/rename/replace/symlink/link/truncate/chmod/chown/utime/mkfifo/mknod,
            shutil.*, tempfile.mkstemp/mkdtemp
  env/registry  os.putenv/unsetenv, winreg.*
Reads are never denied; reads outside {library root, stdlib, the owned world} are only RECORDED (informational).
Job JSON keys: mode ("cli"|"control"), library_root, module, args, world_root, control, marker, flood_bytes, sleep_seconds.
"""
import json
import os
import runpy
import sys
import sysconfig

PROCESS_PREFIXES = ("subprocess.", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "ctypes.", "_winapi.CreateProcess", "webbrowser.")
PROCESS_EXACT = {"os.system", "os.startfile"}
NETWORK_PREFIXES = ("socket.connect", "socket.bind", "socket.send", "socket.getaddrinfo", "socket.gethostby", "socket.getnameinfo", "socket.sethostname",
                    "urllib.Request", "http.client.", "ftplib.", "smtplib.", "telnetlib.", "imaplib.", "poplib.", "nntplib.")
WRITE_EXACT = {"os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.replace", "os.symlink", "os.link", "os.truncate", "os.chmod", "os.chown", "os.lchown",
               "os.utime", "os.mkfifo", "os.mknod", "os.setxattr", "os.removexattr", "tempfile.mkstemp", "tempfile.mkdtemp", "os.chflags", "os.lchmod"}
WRITE_PREFIXES = ("shutil.",)
ENV_EXACT = {"os.putenv", "os.unsetenv"}
ENV_PREFIXES = ("winreg.",)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class HarnessDenied(BaseException):
    """Raised by the audit hook before a forbidden side effect. BaseException on purpose."""


STATE = {"active": True, "denials": [], "foreign_reads": [], "allowed_read": [], "read_count": 0}


def _short(x):
    try:
        return repr(x)[:160]
    except Exception:  # noqa: BLE001
        return "<unrepr>"


def _category(event, args):
    if event in PROCESS_EXACT or event.startswith(PROCESS_PREFIXES):
        return "process"
    if event.startswith(NETWORK_PREFIXES):
        return "network"
    if event in WRITE_EXACT or event.startswith(WRITE_PREFIXES):
        return "write"
    if event in ENV_EXACT or event.startswith(ENV_PREFIXES):
        return "env_or_registry"
    if event == "open":
        path, mode, flags = args
        mode = mode if isinstance(mode, str) else ""
        if any(c in mode for c in "wax+") or (isinstance(flags, int) and flags & WRITE_FLAGS):
            return "write"
    return None


def _hook(event, args):
    if not STATE["active"]:
        return
    cat = _category(event, args)
    if cat is not None:
        if len(STATE["denials"]) < 100:
            STATE["denials"].append({"category": cat, "event": event, "args": _short(args)})
        raise HarnessDenied(f"HARNESS-DENIED:{cat}:{event}")
    if event == "open":
        STATE["read_count"] += 1
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            ps = os.fsdecode(path)
            if ps and not any(ps == a or ps.startswith(a.rstrip("\\/") + os.sep) for a in STATE["allowed_read"]):
                if ps not in STATE["foreign_reads"] and len(STATE["foreign_reads"]) < 50:
                    STATE["foreign_reads"].append(ps)


def _real(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _control(job):
    """Mechanism controls: each tries a forbidden action INSIDE the hooked interpreter and reports what happened."""
    name, marker = job["control"], job.get("marker")
    out = {"control": name, "attempted": True, "exception": None, "denied_by_hook": False, "effect_present": None}

    def attempt(fn):
        try:
            fn()
        except HarnessDenied as exc:
            out["exception"], out["denied_by_hook"] = type(exc).__name__, True
            out["message"] = str(exc)
        except BaseException as exc:  # noqa: BLE001 - anything else is NOT a denial by the hook
            out["exception"] = type(exc).__name__
            out["message"] = str(exc)[:200]
    if name == "popen_inert_python_writes_marker":
        import subprocess
        attempt(lambda: subprocess.Popen([sys.executable, "-I", "-c", "open(%r,'w').write('launched')" % marker]))
    elif name == "popen_media_tool_name":
        import subprocess
        attempt(lambda: subprocess.run(["ffmpeg", "-version"], capture_output=True))
    elif name == "popen_ffprobe_name":
        import subprocess
        attempt(lambda: subprocess.run(["ffprobe", "-version"], capture_output=True))
    elif name == "os_system":
        attempt(lambda: os.system("echo harness-control > " + json.dumps(marker)))
    elif name == "os_startfile":
        if not hasattr(os, "startfile"):
            out.update(attempted=False, skip_reason="os.startfile does not exist on this platform")
        else:
            attempt(lambda: os.startfile(marker))
    elif name == "os_exec_or_spawn":
        if hasattr(os, "execv"):
            attempt(lambda: os.execv(sys.executable, [sys.executable, "-I", "-c", "open(%r,'w').write('exec')" % marker]))
    elif name == "network_connect_loopback":
        import socket

        def f():
            s = socket.socket()
            try:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", 9))
            finally:
                s.close()
        attempt(f)
    elif name == "network_resolve":
        import socket
        attempt(lambda: socket.getaddrinfo("example.invalid", 80))
    elif name == "write_new_file":
        attempt(lambda: open(marker, "w").write("x"))
    elif name == "write_append_existing":
        attempt(lambda: open(marker, "a").write("x"))
    elif name == "mkdir":
        attempt(lambda: os.mkdir(marker))
    elif name == "makedirs_nested":
        attempt(lambda: os.makedirs(os.path.join(marker, "a", "b")))
    elif name == "remove_file":
        attempt(lambda: os.remove(marker))
    elif name == "rename_file":
        attempt(lambda: os.rename(marker, marker + ".renamed"))
    elif name == "shutil_copyfile":
        import shutil
        attempt(lambda: shutil.copyfile(marker, marker + ".copy"))
    elif name == "env_putenv":
        attempt(lambda: os.putenv("HARNESS_CONTROL", "1"))
    elif name == "tempfile_mkstemp":
        import tempfile
        attempt(lambda: tempfile.mkstemp(dir=os.path.dirname(marker)))
    elif name == "sleep":
        import time
        time.sleep(float(job.get("sleep_seconds", 30)))
    elif name == "flood_stdout":
        chunk = b"x" * 65536
        total = int(job.get("flood_bytes", 3 * 1024 * 1024))
        written = 0
        while written < total:
            try:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except (BrokenPipeError, OSError):
                break
            written += len(chunk)
    elif name == "flood_stderr":
        total = int(job.get("flood_bytes", 300 * 1024))
        sys.stderr.write("e" * total)
        sys.stderr.flush()
    elif name == "read_is_allowed":
        def f():
            with open(marker, "rb") as fh:
                out["read_bytes"] = len(fh.read())
        attempt(f)
    else:
        raise SystemExit("unknown control " + name)
    return out


def main():
    job = json.loads(sys.argv[1])
    log_path = job["log_path"]
    log_fh = open(log_path, "w", encoding="utf-8")        # opened BEFORE the hook exists; writing through it later raises no 'open' event
    lib = _real(job["library_root"])
    allowed = {_real(lib), _real(job["world_root"]), _real(os.path.dirname(os.path.abspath(__file__)))}
    for k in ("stdlib", "platstdlib", "purelib", "platlib"):
        v = sysconfig.get_paths().get(k)
        if v:
            allowed.add(_real(v))
    allowed.add(_real(os.path.dirname(os.__file__)))
    dlls = os.path.join(sys.base_prefix, "DLLs")
    if os.path.isdir(dlls):
        allowed.add(_real(dlls))
    STATE["allowed_read"] = sorted(allowed)
    sys.dont_write_bytecode = True
    sys.path.insert(0, lib)
    result = {"mode": job["mode"], "exit_code": None, "uncaught": None, "control": None}
    code = 0
    sys.addaudithook(_hook)
    try:
        if job["mode"] == "cli":
            sys.argv = [job["module"]] + list(job["args"])
            try:
                runpy.run_module(job["module"], run_name="__main__", alter_sys=True)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        else:
            result["control"] = _control(job)
    except HarnessDenied as exc:
        result["uncaught"] = "HarnessDenied: " + str(exc)
        code = 97
        STATE["active"] = False
        sys.stderr.write("HARNESS-DENIED (child aborted before the side effect): %s\n" % exc)
    except BaseException as exc:  # noqa: BLE001 - library crash: keep the library's own traceback behaviour (exit 1) but record it
        result["uncaught"] = type(exc).__name__
        STATE["active"] = False
        result["exit_code"] = 1
        result["denials"], result["foreign_reads"], result["read_count"] = STATE["denials"], STATE["foreign_reads"], STATE["read_count"]
        json.dump(result, log_fh)
        log_fh.flush()
        raise
    finally:
        STATE["active"] = False
    result["exit_code"] = code
    result["denials"], result["foreign_reads"], result["read_count"] = STATE["denials"], STATE["foreign_reads"], STATE["read_count"]
    json.dump(result, log_fh)
    log_fh.flush()
    sys.stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
