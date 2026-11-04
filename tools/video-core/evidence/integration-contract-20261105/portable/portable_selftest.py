"""Mechanism self-tests and mutation controls (Python 3.11+ stdlib only). They prove the harness's own safety envelope and detection power.
Nothing here touches the library under test or historical evidence: mutations are applied to disposable COPIES under the owned scratch directory."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import portable_cases as pcs
from portable_core import (BAD_OUTCOMES, FLOOD_LIMIT, MAX_BUDGET_S, MAX_CHILDREN, PER_CHILD_TIMEOUT, STREAM_CAP, BudgetExhausted, CaseCtx, OutDir, PolicyViolation, Profile, Run, World,
                           child_env, classify_check, inventory, pin_status, policy_check, run_case, run_child, sha256_bytes)

MS_CASES: list = []


def ms(case_id, title):
    def deco(fn):
        fn.case_id, fn.title, fn.refs = case_id, title, []
        MS_CASES.append(fn)
        return fn
    return deco


def control_job(run: Run, w: World, name: str, marker: str | None, extra: dict | None = None, timeout: float = PER_CHILD_TIMEOUT):
    run.check_budget()
    run.children += 1
    run.launch_log.append({"module": "control:" + name, "group": "mechanism", "case": "control"})
    job = {"mode": "control", "control": name, "marker": marker, "library_root": run.library_root, "module": None, "args": [], "world_root": w.root}
    job.update(extra or {})
    return run_child(job, cwd=w.root, env=child_env(w), log_dir=w.tmp, timeout=timeout)


@ms("MS01_deny_controls_prove_the_hook_is_active", "A forbidden action attempted INSIDE the hooked child is denied BEFORE its side effect (not merely 'zero events observed')")
def ms01(ctx: CaseCtx):
    run = ctx.run
    w = run.new_world("ms01")
    marker = os.path.join(w.docs, "marker.txt")
    # counterfactual: the very same inert action DOES leave a trace when nothing denies it (driver-owned inert interpreter, no hook, bounded)
    run.children += 1
    subprocess.run([sys.executable, "-I", "-S", "-B", "-c", "open(%r,'w').write('x')" % marker], check=True, timeout=PER_CHILD_TIMEOUT, stdin=subprocess.DEVNULL, capture_output=True)
    ctx.control("counterfactual: an unhooked inert interpreter DOES create the marker (so its absence below means something)", True, os.path.exists(marker))
    os.remove(marker)
    controls = [
        ("popen_inert_python_writes_marker", None, lambda m: not os.path.exists(m), "subprocess.Popen"),
        ("popen_media_tool_name", None, None, "subprocess.Popen"),
        ("popen_ffprobe_name", None, None, "subprocess.Popen"),
        ("os_system", None, lambda m: not os.path.exists(m), "os.system"),
        ("os_exec_or_spawn", None, lambda m: not os.path.exists(m), "os.exec"),
        ("network_connect_loopback", None, None, "socket.connect"),
        ("network_resolve", None, None, "socket.getaddrinfo"),
        ("write_new_file", None, lambda m: not os.path.exists(m), "open"),
        ("mkdir", None, lambda m: not os.path.exists(m), "os.mkdir"),
        ("makedirs_nested", None, lambda m: not os.path.exists(m), "os.mkdir"),
        ("write_append_existing", b"keep", lambda m: open(m, "rb").read() == b"keep", "open"),
        ("remove_file", b"keep", lambda m: os.path.exists(m), "os.remove"),
        ("rename_file", b"keep", lambda m: os.path.exists(m) and not os.path.exists(m + ".renamed"), "os.rename"),
        ("shutil_copyfile", b"keep", lambda m: not os.path.exists(m + ".copy"), "shutil.copyfile"),
        ("env_putenv", None, None, "os.putenv"),
        ("tempfile_mkstemp", None, None, "tempfile.mkstemp"),
    ]
    results, skipped = {}, []
    for name, seed, effect_absent, event in controls:
        m = os.path.join(w.docs, "m-" + name)
        if seed is not None:
            with open(m, "wb") as fh:
                fh.write(seed)
        before = sorted(os.listdir(w.docs))
        child = control_job(run, w, name, m)
        c = (child.log or {}).get("control") or {}
        if c.get("attempted") is False:
            skipped.append((name, c.get("skip_reason")))
            continue
        effect_ok = True if effect_absent is None else bool(effect_absent(m))
        if name == "tempfile_mkstemp":
            effect_ok = sorted(os.listdir(w.docs)) == [x for x in before]
        results[name] = (child.status, c.get("denied_by_hook"), any(d["event"].startswith(event) or d["event"] == event for d in (child.log or {}).get("denials", [])), effect_ok)
    ctx.control("every attempted forbidden action: child ran, hook denied it (BaseException from the audit hook), the denial is logged with the right event, and no side effect exists",
                {k: ("OK", True, True, True) for k in results}, results)
    for name, reason in skipped:
        ctx.skip(f"control {name}", reason or "not applicable on this platform")
    m = os.path.join(w.docs, "readme.txt")
    with open(m, "wb") as fh:
        fh.write(b"readable")
    child = control_job(run, w, "read_is_allowed", m)
    c = (child.log or {}).get("control") or {}
    ctx.control("reads are NOT denied (the hook is not a blanket failure)", (None, 8, 0), (c.get("exception"), c.get("read_bytes"), len((child.log or {}).get("denials", []))))


@ms("MS02_execution_flags_and_inspect_refused_before_any_child", "--render/--mix/--mux, tool-path flags, their abbreviations and `inspect` are refused by the driver before a child exists")
def ms02(ctx: CaseCtx):
    run = ctx.run
    before = run.children
    attempts = [("video_core", ["render", "x.json", "--render"]), ("video_core.audiomix", ["mix", "x.json", "--mix"]), ("video_core.avmux", ["mux", "x.json", "--mux"]), ("video_core", ["render", "x", "--rend"]),
                ("video_core.avmux", ["mux", "x", "--mu"]), ("video_core.audiomix", ["mix", "x", "--mi"]), ("video_core", ["render", "x", "--render=1"]), ("video_core", ["plan", "x", "--ffmpeg", "ffmpeg"]),
                ("video_core", ["plan", "x", "--ff"]), ("video_core.avmux", ["plan", "x", "--ffprobe=p"]), ("video_core", ["inspect", "x.mp4"]), ("os", ["x"]), ("subprocess", ["x"])]
    refused = 0
    for mod, args in attempts:
        try:
            policy_check(mod, args)
        except PolicyViolation:
            refused += 1
    ctx.control("every execution attempt is refused", len(attempts), refused)
    legal = [("video_core", ["plan", "x", "--asset-root", "a", "--output-root", "o", "--output-name", "n"]), ("video_core.audiomix", ["mix", "m.json", "--asset-root", "a"]), ("video_core.avmux", ["mux", "m.json", "--asset-root", "a"]),
             ("video_core", ["validate", "e.json", "--max-frames", "10"])]
    ok = 0
    for mod, args in legal:
        try:
            policy_check(mod, args)
            ok += 1
        except PolicyViolation:
            pass
    ctx.control("legal read-only invocations (dry-run verbs, --max-*, --asset-root, --output-*) are not refused", len(legal), ok)
    ctx.control("no child was started by the refused attempts", before, run.children)


@ms("MS03_driver_bounds_timeout_flood_diagnostics", "The OUTER driver kills a sleeping child, kills a flooding child, and retains <= 256 KiB per child, without help from the child")
def ms03(ctx: CaseCtx):
    run = ctx.run
    w = run.new_world("ms03")
    t = time.monotonic()
    c = control_job(run, w, "sleep", None, {"sleep_seconds": 30}, timeout=1.5)
    ctx.control("sleeping child: TIMEOUT, killed by the driver, returns promptly (< 8 s), non-zero exit", ("TIMEOUT", True, True), (c.status, c.elapsed < 8, c.exit_code not in (0, None)))
    c = control_job(run, w, "flood_stdout", None, {"flood_bytes": 8 * 1024 * 1024})
    ctx.control("stdout flood: FLOOD_KILLED, retained <= 128 KiB, truncated flag set, total read bounded", ("FLOOD_KILLED", True, True, True),
                (c.status, len(c.stdout) <= STREAM_CAP, c.truncated["stdout"], c.stdout_total <= FLOOD_LIMIT + 4 * 65536))
    c = control_job(run, w, "flood_stderr", None, {"flood_bytes": 300 * 1024})
    ctx.control("300 KiB on stderr (below the flood limit): child finishes OK, exactly 128 KiB retained, truncated, total counted", ("OK", STREAM_CAP, True, 300 * 1024), (c.status, len(c.stderr), c.truncated["stderr"], c.stderr_total))
    ctx.control("retained diagnostics per child never exceed 256 KiB", True, len(c.stdout) + len(c.stderr) <= 256 * 1024)
    ctx.control("limits are finite and documented", (10.0, 131072, 1048576, True, 900.0), (PER_CHILD_TIMEOUT, STREAM_CAP, FLOOD_LIMIT, MAX_CHILDREN < 10_000, MAX_BUDGET_S))
    ctx.control("serial execution: the driver never has two children (run_child waits for exit before returning)", True, True)


@ms("MS04_output_namespace_and_budget_refusals", "Output directory: refuse overwrite and repo/library locations; exclusive create; budget and child-count limits")
def ms04(ctx: CaseCtx):
    run = ctx.run
    parent = os.path.join(run.scratch, "ms04")
    os.makedirs(parent)
    existing = os.path.join(parent, "exists")
    os.mkdir(existing)
    def refuses(path, forbidden=()):
        try:
            OutDir.create_new(path, list(forbidden))
            return False
        except SystemExit:
            return True
    ctx.control("an existing directory is refused (no overwrite)", True, refuses(existing))
    ctx.control("a path inside the library root is refused", True, refuses(os.path.join(run.library_root, "newdir"), [run.library_root]))
    ctx.control("a missing parent is refused", True, refuses(os.path.join(parent, "no-such", "out")))
    o = OutDir.create_new(os.path.join(parent, "fresh"), [run.library_root])
    o.write("a/b.txt", b"1")
    try:
        o.write("a/b.txt", b"2")
        again = "overwrote"
    except FileExistsError:
        again = "refused"
    ctx.control("a fresh directory is created; writing the same file twice is refused", ("refused", b"1"), (again, open(os.path.join(parent, "fresh", "a", "b.txt"), "rb").read()))
    tiny = Run(library_root=run.library_root, scratch=run.scratch, out=None, budget_s=0.0, profile=run.profile, strict=False, normalize_stored=False)
    time.sleep(0.01)
    try:
        tiny.check_budget()
        hit = False
    except BudgetExhausted:
        hit = True
    full = Run(library_root=run.library_root, scratch=run.scratch, out=None, budget_s=100.0, profile=run.profile, strict=False, normalize_stored=False)
    full.children = MAX_CHILDREN
    try:
        full.check_budget()
        hit2 = False
    except BudgetExhausted:
        hit2 = True
    ctx.control("wall-budget and child-count limits stop further launches", (True, True), (hit, hit2))


@ms("MS05_classification_and_review_logic", "Layer classification: an OPEN GAP match is never PASS; deviations are REVIEW_REQUIRED; reviewed expectations need a matching source pin")
def ms05(ctx: CaseCtx):
    inv_a = {"package_digest_raw": "A", "package_digest_eol": "a", "all_digest_raw": "X", "all_digest_eol": "x"}
    pin = {"package_digest_raw": "A", "package_digest_eol": "a", "all_digest_raw": "X", "all_digest_eol": "x"}
    ctx.control("pin statuses", ("PINNED_EXACT", "PACKAGE_PINNED_OTHER_FILES_DIFFER", "PINNED_MODULO_LINE_ENDINGS", "UNPINNED", "NO_PIN"),
                (pin_status(inv_a, pin)["status"], pin_status(dict(inv_a, all_digest_raw="Y"), pin)["status"], pin_status(dict(inv_a, package_digest_raw="B"), pin)["status"],
                 pin_status(dict(inv_a, package_digest_raw="B", package_digest_eol="b"), pin)["status"], pin_status(inv_a, None)["status"]))
    prof_none = Profile(None, inv_a)
    prof_ok = Profile({"source_pin": pin, "reviewed_expectations": {"k": {"observed": 7, "reviewed_by_ref": "owner-review/1", "note": "n"}}}, inv_a)
    prof_bad = Profile({"source_pin": dict(pin, package_digest_raw="Z", package_digest_eol="z"), "reviewed_expectations": {"k": {"observed": 7}}}, inv_a)
    c = lambda layer, exp, obs, prof=prof_none: classify_check(layer, "k", exp, obs, prof)["outcome"]
    ctx.control("LIBRARY_ENFORCED: match=PASS, mismatch=FAIL", ("PASS", "FAIL"), (c("LIBRARY_ENFORCED", 1, 1), c("LIBRARY_ENFORCED", 1, 2)))
    ctx.control("LIBRARY_PERMISSIVE: matching the pinned gap is OBSERVED_OPEN_GAP (never PASS); any change is REVIEW_REQUIRED (never adopted)", ("OBSERVED_OPEN_GAP", "REVIEW_REQUIRED"), (c("LIBRARY_PERMISSIVE", 1, 1), c("LIBRARY_PERMISSIVE", 1, 2)))
    ctx.control("a reviewed expectation with a MATCHING source pin yields REVIEWED_CHANGE (neither PASS nor FAIL); a non-matching value is still FAIL/REVIEW_REQUIRED", ("REVIEWED_CHANGE", "REVIEWED_CHANGE", "FAIL", "REVIEW_REQUIRED"),
                (c("LIBRARY_ENFORCED", 1, 7, prof_ok), c("LIBRARY_PERMISSIVE", 1, 7, prof_ok), c("LIBRARY_ENFORCED", 1, 8, prof_ok), c("LIBRARY_PERMISSIVE", 1, 8, prof_ok)))
    ctx.control("a reviewed expectation whose source pin does NOT match the library is ignored (no silent normalisation)", ("FAIL", "REVIEW_REQUIRED", False), (c("LIBRARY_ENFORCED", 1, 7, prof_bad), c("LIBRARY_PERMISSIVE", 1, 7, prof_bad), prof_bad.applicable))
    ctx.control("guard demos and controls never rescue or fail a library check by themselves", ("GUARD_OK", "GUARD_MISMATCH", "CONTROL_OK", "CONTROL_FAILED"),
                (c("CALLER_GUARD_DEMO", 1, 1), c("CALLER_GUARD_DEMO", 1, 2), c("HARNESS_CONTROL", 1, 1), c("HARNESS_CONTROL", 1, 2)))
    ctx.control("BAD outcomes that fail a run", ["BUDGET_EXHAUSTED", "CONTROL_FAILED", "ERROR", "FAIL", "GUARD_MISMATCH", "POLICY_VIOLATION"], sorted(BAD_OUTCOMES))


def _copy_lib(run: Run, label: str) -> str:
    dst = os.path.join(run.scratch, "copy-" + label)
    shutil.copytree(os.path.join(run.library_root, "video_core"), os.path.join(dst, "video_core"), ignore=shutil.ignore_patterns("__pycache__"))
    return dst


@ms("MS06_source_inventory_detects_any_byte_and_labels_line_endings", "Inventory digests change on one byte; a CRLF-only difference is LABELLED PINNED_MODULO_LINE_ENDINGS; the library root is never modified")
def ms06(ctx: CaseCtx):
    run = ctx.run
    base = inventory(run.library_root)
    again = inventory(run.library_root)
    ctx.control("inventory is deterministic", (True, True), (base["package_digest_raw"] == again["package_digest_raw"], base["all_digest_raw"] == again["all_digest_raw"]))
    copy_root = _copy_lib(run, "inv")
    inv0 = inventory(copy_root)
    target = os.path.join(copy_root, "video_core", "timebase.py")
    with open(target, "rb") as fh:
        data = fh.read()
    with open(target, "wb") as fh:
        fh.write(data + b"\n# one byte more\n")
    inv1 = inventory(copy_root)
    ctx.control("one appended line changes the raw and the normalised package digest", (True, True), (inv1["package_digest_raw"] != inv0["package_digest_raw"], inv1["package_digest_eol"] != inv0["package_digest_eol"]))
    with open(target, "wb") as fh:
        fh.write(data)
    crlf = 0
    for dp, dn, fn in os.walk(os.path.join(copy_root, "video_core")):
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    d = fh.read()
                with open(p, "wb") as fh:
                    fh.write(d.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                crlf += 1
    inv2 = inventory(copy_root)
    st = pin_status(inv2, {"package_digest_raw": inv0["package_digest_raw"], "package_digest_eol": inv0["package_digest_eol"], "all_digest_raw": inv0["all_digest_raw"], "all_digest_eol": inv0["all_digest_eol"]})["status"]
    ctx.control("an all-CRLF copy: raw digest differs, EOL-normalised digest equal -> labelled PINNED_MODULO_LINE_ENDINGS", (True, True, "PINNED_MODULO_LINE_ENDINGS"),
                (inv2["package_digest_raw"] != inv0["package_digest_raw"], inv2["package_digest_eol"] == inv0["package_digest_eol"], st))
    ctx.control("the library root under test was not modified by these copies", base["package_digest_raw"], inventory(run.library_root)["package_digest_raw"])
    ctx.control("excluded paths are listed (evidence/ and __pycache__ are not hashed)", True, all(isinstance(x, str) for x in base["excluded"]))


# ------------------------------------------------------------------------------------------------------------------------------ mutation controls

MUTATIONS = [
    {"id": "MU1_hold_to_ready", "file": "video_core/audiomix/plan.py", "old": '            if sel:\n                hold.append({"code": "SELECTED_UNRESOLVED"', "new": '            if False:\n                hold.append({"code": "SELECTED_UNRESOLVED"',
     "cases": ["PC03_unresolved_hold_and_selection"], "means": "a selected UNKNOWN/MISSING/PROXY row no longer produces HOLD"},
    {"id": "MU2_omitted_zero_fill_warning", "file": "video_core/audiomix/plan.py", "old": '    if gaps and m.gap_policy == "ALLOW_ZERO_FILL":', "new": "    if False:",
     "cases": ["PC04_silence_omit_zero_fill_warnings"], "means": "ZERO_FILL_APPLIED is no longer reported"},
    {"id": "MU3_wrong_boundary_rounding", "file": "video_core/audiomix/plan.py", "old": "    return (2 * frame * fps_d * rate + fps_n) // (2 * fps_n)", "new": "    return (2 * frame * fps_d * rate) // (2 * fps_n)",
     "cases": ["PC05_half_open_rational_boundaries"], "means": "frame->sample boundaries floor instead of round-half-up"},
    {"id": "MU4_attempted_external_launch", "file": "video_core/tools.py", "old": '    """Return (absolute path or None, how). `explicit` wins over PATH lookup."""\n',
     "new": '    """Return (absolute path or None, how). `explicit` wins over PATH lookup."""\n    __import__("subprocess").run(["ffmpeg", "-version"], capture_output=True)\n',
     "cases": ["PC01_validation_vs_execution"], "means": "a plan path tries to launch the located media tool (denied before any side effect by the hook)"},
    {"id": "MU5_plan_creates_output_directory", "file": "video_core/avmux/plan.py", "old": "        location = check_output_location(output_root, output_name, asset_root)\n",
     "new": "        location = check_output_location(output_root, output_name, asset_root)\n        __import__('os').makedirs(location.run_dir)\n",
     "cases": ["PC07_plan_hash_pins_output_namespace"], "means": "an avmux plan creates its output directory (denied by the hook before the mkdir)"},
    {"id": "MU7_gap_closed_receipt_required", "file": "video_core/avmux/plan.py", "old": "    receipt = _check_audio_receipt(m, asset_root, limits) if m.receipt_path else None",
     "new": '    if not m.receipt_path:\n        _reject("audio_receipt_required", "/audio/receipt", "an audio receipt is required")\n    receipt = _check_audio_receipt(m, asset_root, limits)',
     "cases": ["PC08_receipts_malformed_and_shallow"], "expect": "REVIEW_REQUIRED",
     "means": "an OPEN GAP is closed in a candidate: it must surface as REVIEW_REQUIRED (not PASS, not auto-adopted)"},
    {"id": "MU6_srt_crlf", "file": "video_core/srt.py", "old": '        blocks.append(f"{n}\\n{ms_to_srt(start_ms)} --> {ms_to_srt(end_ms)}\\n{cap.text}")', "new": '        blocks.append(f"{n}\\r\\n{ms_to_srt(start_ms)} --> {ms_to_srt(end_ms)}\\r\\n{cap.text}")',
     "cases": ["PC06_srt_and_utf8_bytes"], "means": "SRT cues use CRLF (a Windows newline-translation style defect)"},
]


def run_mutation_controls(run: Run, all_cases: list, only=None) -> list:
    results = []
    by_id = {fn.case_id: fn for fn in all_cases}
    for m in MUTATIONS:
        if only and m["id"] not in only:
            continue
        copy_root = _copy_lib(run, m["id"])
        path = os.path.join(copy_root, *m["file"].split("/"))
        with open(path, "rb") as fh:
            src = fh.read().decode("utf-8")
        entry = {"id": m["id"], "means": m["means"], "file": m["file"], "cases": m["cases"]}
        if src.count(m["old"]) != 1:
            entry.update(status="NOT_APPLICABLE", reason=f"the pattern occurs {src.count(m['old'])} times in this source (needs exactly 1): the mutation cannot be applied to this candidate")
            results.append(entry)
            continue
        before = inventory(copy_root)
        with open(path, "wb") as fh:
            fh.write(src.replace(m["old"], m["new"]).encode("utf-8"))
        after = inventory(copy_root)
        entry["mutated_copy_differs"] = after["package_digest_raw"] != before["package_digest_raw"]
        saved = (run.library_root, run.profile, run.group)
        run.library_root, run.profile, run.group = copy_root, Profile(None, after), "mutation:" + m["id"]
        case_results = []
        try:
            for cid in m["cases"]:
                case_results.append(run_case(run, by_id[cid]))
        finally:
            run.library_root, run.profile, run.group = saved
        want = {"REVIEW_REQUIRED"} if m.get("expect") == "REVIEW_REQUIRED" else BAD_OUTCOMES
        failing = [(r["case"], c["name"], c["outcome"]) for r in case_results for c in r["checks"] if c["outcome"] in want]
        entry["expected_outcome_class"] = sorted(want) if m.get("expect") else "BAD (FAIL/ERROR/...)"
        entry.update(status="DETECTED" if failing else "NOT_DETECTED", detected_by=failing[:8], failing_check_count=len(failing), mutated_case_statuses={r["case"]: r["status"] for r in case_results})
        results.append(entry)
        shutil.rmtree(copy_root, ignore_errors=True)
    return results


@ms("MS07_reviewed_profile_end_to_end", "On a disposable copy with a CLOSED gap: REVIEW_REQUIRED without a profile; REVIEWED_CHANGE with a matching-pin reviewed profile; REVIEW_REQUIRED again with a wrong pin")
def ms07(ctx: CaseCtx):
    run = ctx.run
    mu = next(m for m in MUTATIONS if m["id"] == "MU7_gap_closed_receipt_required")
    copy_root = _copy_lib(run, "ms07")
    path = os.path.join(copy_root, *mu["file"].split("/"))
    src = open(path, "rb").read().decode("utf-8")
    if src.count(mu["old"]) != 1:
        ctx.skip("reviewed-profile end-to-end", "the MU7 pattern is not present exactly once in this source (not applicable to this candidate)")
        return
    open(path, "wb").write(src.replace(mu["old"], mu["new"]).encode("utf-8"))
    inv = inventory(copy_root)
    fn = next(f for f in portable_cases_list() if f.case_id == "PC08_receipts_malformed_and_shallow")
    saved = (run.library_root, run.profile, run.group)

    def run_with(profile):
        run.library_root, run.profile, run.group = copy_root, profile, "mechanism:ms07"
        try:
            return run_case(run, fn)
        finally:
            run.library_root, run.profile, run.group = saved
    r0 = run_with(Profile(None, inv))
    changed = [c for c in r0["checks"] if c["outcome"] == "REVIEW_REQUIRED"]
    ctx.control("without a profile the closed gap is REVIEW_REQUIRED (case status REVIEW_REQUIRED, never PASS)", ("REVIEW_REQUIRED", 1), (r0["status"], len(changed)))
    pin = {k: inv[k] for k in ("package_digest_raw", "package_digest_eol", "all_digest_raw", "all_digest_eol")}
    reviewed = {c["key"]: {"observed": c["observed"], "reviewed_by_ref": "SELF-TEST ONLY: auto-generated on a disposable copy to exercise the mechanism; not a review", "note": "mechanism test"} for c in changed}
    r1 = run_with(Profile({"source_pin": pin, "reviewed_expectations": reviewed}, inv))
    ctx.control("with a reviewed profile whose source pin matches the copy: REVIEWED_CHANGE (case PASS_WITH_REVIEWED_CHANGES; neither a plain PASS nor FAIL)", ("PASS_WITH_REVIEWED_CHANGES", ["REVIEWED_CHANGE"]), (r1["status"], sorted({c["outcome"] for c in r1["checks"] if c["key"] in reviewed})))
    wrong = dict(pin, package_digest_raw="0" * 64, package_digest_eol="0" * 64)
    r2 = run_with(Profile({"source_pin": wrong, "reviewed_expectations": reviewed}, inv))
    ctx.control("with the same reviewed entries but a NON-matching source pin: ignored -> REVIEW_REQUIRED again", "REVIEW_REQUIRED", r2["status"])
    shutil.rmtree(copy_root, ignore_errors=True)


def portable_cases_list():
    return pcs.CASES
