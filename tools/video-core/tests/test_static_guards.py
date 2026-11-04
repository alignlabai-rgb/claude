"""Static guards for the 'offline, stdlib-only, no shell' contract."""

import ast
import os
import sys
import unittest

from tests.helpers import ROOT

PKG = os.path.join(ROOT, "video_core")
FORBIDDEN = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio", "requests", "httpx", "ctypes", "pickle", "webbrowser"}


def sources():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            with open(os.path.join(PKG, name), encoding="utf-8") as fh:
                yield name, ast.parse(fh.read(), filename=name)


class Guards(unittest.TestCase):
    def test_imports_are_stdlib_and_network_free(self):
        std = set(sys.stdlib_module_names)
        for name, tree in sources():
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    self.assertIn(m, std, f"{name} imports non-stdlib {m}")
                    self.assertNotIn(m, FORBIDDEN, f"{name} imports {m}")

    def test_no_shell_invocation_anywhere(self):
        for name, tree in sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "shell":
                    self.assertIsInstance(node.value, ast.Constant)
                    self.assertIs(node.value.value, False, f"{name}: shell must be False")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if (node.func.value.id, node.func.attr) in {("os", "system"), ("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "check_output")}:
                        self.fail(f"{name}: forbidden call {node.func.value.id}.{node.func.attr}")

    def test_only_runner_launches_processes(self):
        for name, tree in sources():
            if name == "runner.py":
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("Popen", "run", "call"):
                    base = node.value
                    if isinstance(base, ast.Name) and base.id == "subprocess":
                        self.fail(f"{name} uses subprocess directly")

    def test_python_311_syntax_floor(self):
        for name in sorted(os.listdir(PKG)) + ["../tests/" + n for n in sorted(os.listdir(os.path.join(ROOT, "tests"))) if n.endswith(".py")]:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(PKG, name), encoding="utf-8") as fh:
                ast.parse(fh.read(), feature_version=(3, 11))


if __name__ == "__main__":
    unittest.main()
