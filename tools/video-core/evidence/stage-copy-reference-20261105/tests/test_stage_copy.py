"""12 focused cases for the SIT-06 / GAP-12 stage-copy reference (Python 3.11+ stdlib only).  Synthetic dummy bytes written by each case into its OWN temp directory; nothing else is touched.
The PINS are supplied before the copy, from the generator bytes and from literal constants - never from the copied destination.
Run:  python -B -W error::ResourceWarning -m unittest discover -s tests -v"""
import contextlib
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("stage_copy_reference", os.path.join(os.environ.get("SCR_MODULE_DIR") or os.path.dirname(HERE), "stage_copy_reference.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

DUMMY = b"SYNTHETIC-STAGE-COPY-DUMMY\n" * 40            # 1080 bytes
OTHER = b"X" + DUMMY[1:]                                 # same size, one byte different
PIN = {"bytes": 1080, "sha256": "b0ac46ac6eb1c93fab077f7028c9d94f16fd69136b5149e6c9def9a61ce3a76a"}                  # literal, supplied in advance
PIN_OTHER = {"bytes": 1080, "sha256": "21289070128be35cf66b15cc238390c23f97a4e73ba9affd575bccaa569e1d9c"}
PIN_EMPTY = {"bytes": 0, "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def put(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def get(path):
    with open(path, "rb") as fh:
        return fh.read()


@contextlib.contextmanager
def world(data=DUMMY, name="stem.wav"):
    with tempfile.TemporaryDirectory() as t:
        src, dst = os.path.join(t, "assets"), os.path.join(t, "mux_inputs")
        os.mkdir(src)
        os.mkdir(dst)
        if data is not None:
            put(os.path.join(src, name), data)
        yield t, src, dst


def facts(res, dst):
    return {"outcome": res["outcome"], "reason": res["reason"], "completed": res["completed"], "dest_listing": sorted(os.listdir(dst))}


@case
def c01_normal_copy_and_zero_byte(t=None):
    out = {}
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
        assert get(os.path.join(dst, "stem_copy.wav")) == DUMMY
        out["normal"] = (facts(res, dst), res)
    with world(b"", "empty.bin") as (_, src, dst):
        res = M.stage_copy(src, "empty.bin", PIN_EMPTY, dst, "empty_copy.bin")
        out["zero_byte"] = (facts(res, dst), res)
    return out


@case
def c02_source_size_differs_from_pin(t=None):
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", dict(PIN, bytes=1081), dst, "stem_copy.wav")
        return {"r": (facts(res, dst), res)}


@case
def c03_source_sha_differs_same_size(t=None):
    with world(OTHER) as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav")          # the file on disk is OTHER (same size); the caller's pin is for DUMMY
        return {"r": (facts(res, dst), res)}


@case
def c04_copy_interrupted_midway(t=None):
    def hook(event, info):
        if event == "chunk_written" and info["index"] == 3:
            raise OSError("simulated device error")
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64, hook=hook)
        assert get(os.path.join(dst, "stem_copy.wav.partial")) == DUMMY[:192]
        return {"r": (facts(res, dst), res)}


@case
def c05_source_replaced_while_reading(t=None):
    def hook(event, info):
        if event == "chunk_written" and info["index"] == 2:
            with open(info["source_path"], "r+b") as fh:                       # same size, one byte changed AFTER the first chunks were read
                fh.seek(len(DUMMY) - 1)
                fh.write(b"!")
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64, hook=hook)
        return {"r": (facts(res, dst), res)}


@case
def c06_destination_corrupted_before_verification(t=None):
    def hook(event, info):
        if event == "before_destination_verify":
            with open(info["partial_path"], "r+b") as fh:
                fh.seek(5)
                fh.write(b"#")
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64, hook=hook)
        return {"r": (facts(res, dst), res)}


@case
def c07_existing_final_name_is_refused_and_untouched(t=None):
    with world() as (_, src, dst):
        put(os.path.join(dst, "stem_copy.wav"), b"EARLIER-OUTPUT")
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav")
        assert get(os.path.join(dst, "stem_copy.wav")) == b"EARLIER-OUTPUT"
        return {"r": (facts(res, dst), res)}


@case
def c08_existing_partial_name_is_refused_and_untouched(t=None):
    with world() as (_, src, dst):
        put(os.path.join(dst, "stem_copy.wav.partial"), b"EARLIER-PARTIAL")
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav")
        assert get(os.path.join(dst, "stem_copy.wav.partial")) == b"EARLIER-PARTIAL"
        out = {"r": (facts(res, dst), res)}
    def racing(event, info):                                       # a file appears at the partial name AFTER the freshness check: only exclusive create ('xb') can still refuse
        if event == "before_partial_create":
            put(info["partial_path"], b"RACING-PARTIAL")
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", hook=racing)
        assert get(os.path.join(dst, "stem_copy.wav.partial")) == b"RACING-PARTIAL"
        out["race"] = (facts(res, dst), res)
    return out


@case
def c09_destination_root_is_an_alias_of_the_source_root(t=None):
    with world() as (_, src, dst):
        res = M.stage_copy(src, "stem.wav", PIN, os.path.join(src, "."), "stem_copy.wav")                  # same directory, spelled differently
        out = {"alias_dot": (facts(res, src), res)}
        res2 = M.stage_copy(src, "stem.wav", PIN, src, "stem_copy.wav")
        out["same_path"] = (facts(res2, src), res2)
        return out


@case
def c10_destination_inside_or_around_the_source_root(t=None):
    with world() as (t, src, dst):
        inner = os.path.join(src, "inner")
        os.mkdir(inner)
        res = M.stage_copy(src, "stem.wav", PIN, inner, "stem_copy.wav")                                   # descendant of the source root
        out = {"dest_inside_source": (facts(res, inner), res)}
        put(os.path.join(t, "top.bin"), DUMMY)
        res2 = M.stage_copy(src, "stem.wav", PIN, t, "stem_copy.wav")                                       # ancestor of the source root
        out["source_inside_dest"] = (facts(res2, t), res2)
        res3 = M.stage_copy(dst, "nothing.wav", PIN, src, "x.wav")                                          # disjoint roots, missing source
        out["disjoint_missing_source"] = (facts(res3, src), res3)
        return out


@case
def c11_invalid_arguments(t=None):
    out = {}
    with world() as (_, src, dst):
        bad = {"bool size": dict(PIN, bytes=True), "float size": dict(PIN, bytes=1080.0), "upper sha": dict(PIN, sha256=PIN["sha256"].upper()), "short sha": dict(PIN, sha256="a" * 63),
               "extra key": dict(PIN, extra=1), "negative size": dict(PIN, bytes=-1), "over limit": dict(PIN, bytes=M.MAX_BYTES + 1), "pin is None": None}
        for k, pin in bad.items():
            res = M.stage_copy(src, "stem.wav", pin, dst, "stem_copy.wav")
            out["pin: " + k] = (facts(res, dst), res)
        for k, nm in {"separator": "a/b.wav", "backslash": "a\\b.wav", "parent": "..", "empty": "", "leading dot": ".hidden", "reserved": "CON", "partial suffix": "x.wav.partial", "drive": "C:x.wav", "trailing dot": "x."}.items():
            res = M.stage_copy(src, "stem.wav", PIN, dst, nm)
            out["name: " + k] = (facts(res, dst), res)
        res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=0)
        out["chunk_size 0"] = (facts(res, dst), res)
    return out


@case
def c12_symlink_source_is_refused(t=None):
    if os.name == "nt":
        return {"skipped": "symlink creation is not attempted on Windows (privilege); no OS evidence is claimed"}
    with world() as (t, src, dst):
        real = os.path.join(t, "elsewhere.bin")
        put(real, DUMMY)
        try:
            os.symlink(real, os.path.join(src, "link.wav"))
        except (OSError, NotImplementedError) as exc:
            return {"skipped": "cannot create a symlink here (" + type(exc).__name__ + ")"}
        res = M.stage_copy(src, "link.wav", PIN, dst, "stem_copy.wav")
        return {"r": (facts(res, dst), res)}


class T(unittest.TestCase):
    def test_pins_are_literals_matching_the_generator_not_the_destination(self):
        self.assertEqual(hashlib.sha256(DUMMY).hexdigest(), PIN["sha256"])
        self.assertEqual((len(DUMMY), len(OTHER)), (1080, 1080))
        self.assertEqual(hashlib.sha256(OTHER).hexdigest(), PIN_OTHER["sha256"])
        self.assertNotEqual(PIN["sha256"], PIN_OTHER["sha256"])

    def test_c01_normal_and_zero_byte(self):
        o = c01_normal_copy_and_zero_byte()
        for k, pin in (("normal", PIN), ("zero_byte", PIN_EMPTY)):
            f, r = o[k]
            self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("INTEGRITY_MATCH", "ALL_PINNED_COMPARISONS_EQUAL", True), k)
            self.assertEqual(r["root_relationship"], "DISJOINT")
            self.assertEqual(r["residue"]["partial_exists"], False)
            self.assertEqual(r["residue"]["final_exists"], True)
            self.assertEqual([c["outcome"] for c in r["checks"]], ["MATCH"] * 7)
            self.assertEqual(r["pin_supplied_in_advance"], pin)
            for stage in ("source_before", "source_after", "destination"):
                self.assertEqual({"bytes": r["observed"][stage]["bytes"], "sha256": r["observed"][stage]["sha256"]}, pin, stage)
            self.assertEqual(r["observed"]["copy_stream"], pin)
        self.assertEqual(o["normal"][0]["dest_listing"], ["stem_copy.wav"])
        self.assertIn("authenticity", o["normal"][1]["not_asserted"])
        self.assertNotIn("ready", json.dumps(o["normal"][1]["checks"]).lower())

    def refused_nothing_created(self, o, reason, key="r"):
        f, r = o[key]
        self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("REFUSED", reason, False))
        self.assertEqual(r["created_by_this_call"], False)
        return f, r

    def test_c02_size_pin_mismatch(self):
        f, r = self.refused_nothing_created(c02_source_size_differs_from_pin(), "SOURCE_PIN_MISMATCH")
        self.assertEqual(f["dest_listing"], [])
        self.assertEqual([c["outcome"] for c in r["checks"]], ["MISMATCH", "MATCH"])        # size differs, hash of the actual bytes equals the DUMMY pin hash: the size comparison was reached

    def test_c03_sha_pin_mismatch_same_size(self):
        f, r = self.refused_nothing_created(c03_source_sha_differs_same_size(), "SOURCE_PIN_MISMATCH")
        self.assertEqual(f["dest_listing"], [])
        self.assertEqual([c["outcome"] for c in r["checks"]], ["MATCH", "MISMATCH"])        # same size, different bytes: the SHA-256 comparison alone caught it
        self.assertEqual(r["observed"]["source_before"]["sha256"], PIN_OTHER["sha256"])

    def test_c04_interrupted(self):
        f, r = c04_copy_interrupted_midway()["r"]
        self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("INTERRUPTED", "COPY_INTERRUPTED", False))
        self.assertEqual(f["dest_listing"], ["stem_copy.wav.partial"])                       # exactly what remains: the partial, never the final name
        self.assertEqual((r["residue"]["final_exists"], r["residue"]["partial_exists"], r["residue"]["partial_bytes"]), (False, True, 192))
        self.assertEqual(r["residue"]["partial_sha256"], hashlib.sha256(DUMMY[:192]).hexdigest())
        self.assertEqual(r["observed"]["interruption"], "OSError")

    def test_c05_source_replaced_during_read(self):
        f, r = c05_source_replaced_while_reading()["r"]
        self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("FAILED_VERIFICATION", "SOURCE_CHANGED_DURING_COPY", False))
        out = {c["name"]: c["outcome"] for c in r["checks"]}
        self.assertEqual(out["source_before_sha256_equal_pin"], "MATCH")                     # the swap happened after the pre-check: only the post-copy re-hash can see it
        self.assertEqual(out["source_after_sha256_equal_pin"], "MISMATCH")
        self.assertEqual(f["dest_listing"], ["stem_copy.wav.partial"])
        self.assertEqual(r["residue"]["final_exists"], False)

    def test_c06_destination_corrupted(self):
        f, r = c06_destination_corrupted_before_verification()["r"]
        self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("FAILED_VERIFICATION", "DESTINATION_MISMATCH", False))
        out = {c["name"]: c["outcome"] for c in r["checks"]}
        self.assertEqual((out["source_after_sha256_equal_pin"], out["destination_bytes_equal_pin"], out["destination_sha256_equal_pin"]), ("MATCH", "MATCH", "MISMATCH"))
        self.assertEqual(f["dest_listing"], ["stem_copy.wav.partial"])

    def test_c07_c08_existing_outputs_refused(self):
        f, r = self.refused_nothing_created(c07_existing_final_name_is_refused_and_untouched(), "DESTINATION_EXISTS")
        self.assertEqual(f["dest_listing"], ["stem_copy.wav"])
        o = c08_existing_partial_name_is_refused_and_untouched()
        f, r = self.refused_nothing_created(o, "PARTIAL_EXISTS")
        self.assertEqual(f["dest_listing"], ["stem_copy.wav.partial"])
        f, r = o["race"]
        self.assertEqual((f["outcome"], f["reason"], f["completed"], f["dest_listing"]), ("REFUSED", "PARTIAL_EXISTS", False, ["stem_copy.wav.partial"]))
        self.assertEqual(r["created_by_this_call"], False)

    def test_c09_root_alias(self):
        o = c09_destination_root_is_an_alias_of_the_source_root()
        for k in ("alias_dot", "same_path"):
            f, r = self.refused_nothing_created(o, "ROOT_SAME_ROOT", k)
            self.assertEqual(r["root_relationship"], "SAME_ROOT")
            self.assertEqual(f["dest_listing"], ["stem.wav"])                                # nothing was added next to the source

    def test_c10_descendant_and_ancestor_are_distinguished_from_the_alias(self):
        o = c10_destination_inside_or_around_the_source_root()
        _, r = self.refused_nothing_created(o, "ROOT_DEST_INSIDE_SOURCE_ROOT", "dest_inside_source")
        self.assertEqual(r["root_relationship"], "DEST_INSIDE_SOURCE_ROOT")
        _, r = self.refused_nothing_created(o, "ROOT_SOURCE_INSIDE_DEST_ROOT", "source_inside_dest")
        self.assertEqual(r["root_relationship"], "SOURCE_INSIDE_DEST_ROOT")
        _, r = self.refused_nothing_created(o, "SOURCE_MISSING", "disjoint_missing_source")
        self.assertEqual(r["root_relationship"], "DISJOINT")

    def test_c11_invalid_arguments_all_refused_before_any_filesystem_action(self):
        o = c11_invalid_arguments()
        self.assertEqual(len(o), 18)
        for k, (f, r) in o.items():
            self.assertEqual((f["outcome"], f["reason"], f["completed"]), ("REFUSED", "INVALID_ARGUMENT", False), k)
            self.assertEqual((f["dest_listing"], r["created_by_this_call"], r["root_relationship"], r["checks"]), ([], False, None, []), k)

    def test_c12_symlink_source(self):
        o = c12_symlink_source_is_refused()
        if "skipped" in o:
            self.skipTest(o["skipped"])
        f, r = self.refused_nothing_created(o, "SOURCE_NOT_REGULAR_FILE")
        self.assertEqual(f["dest_listing"], [])

    def test_only_owned_temp_paths_are_opened_and_nothing_else_happens(self):
        events = []
        armed = []

        def hook(event, args):
            if not armed:
                return
            if event.startswith(("subprocess", "socket", "os.system", "os.exec", "os.spawn", "urllib", "shutil", "os.putenv", "os.remove", "os.unlink", "os.rmdir", "os.symlink", "os.link")):
                events.append((event, "forbidden"))
            elif event in ("open", "os.rename", "os.mkdir", "os.listdir", "os.scandir"):
                p = args[0]
                events.append((event, os.fspath(p) if isinstance(p, (str, bytes, os.PathLike)) else repr(p)))
        sys.addaudithook(hook)
        with world() as (t, src, dst):
            armed.append(1)
            try:
                res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            finally:
                armed.clear()
            self.assertEqual(res["outcome"], "INTEGRITY_MATCH")
            root = os.path.realpath(t)
            bad = [e for e in events if e[1] == "forbidden" or not os.path.realpath(e[1]).startswith(root)]
            self.assertEqual(bad, [], bad)
            self.assertTrue(any(e[0] == "os.rename" for e in events))

    def test_helper_imports_and_result_is_json_safe(self):
        import ast
        with open(os.path.join(os.path.dirname(M.__file__), "stage_copy_reference.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertEqual(imported, {"__future__", "hashlib", "os", "re", "stat"})
        for k, (f, r) in c01_normal_copy_and_zero_byte().items():
            json.dumps(r, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
