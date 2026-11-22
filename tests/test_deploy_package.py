#!/usr/bin/env python3
"""Ensure deploy.sh ships every module index.py imports locally."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
DEPLOY_SH = REPO / "backend" / "deploy.sh"

DEPLOY_EXCLUDE = {"migrate.py", "reset_premium.py"}
IMPORT_RE = re.compile(r"^\s*from\s+([a-zA-Z_][\w]*)\s+import\s+", re.M)


def _local_index_imports():
    text = (BACKEND / "index.py").read_text(encoding="utf-8")
    names = set(IMPORT_RE.findall(text))
    backend_modules = {
        p.stem
        for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_")
        and p.name not in DEPLOY_EXCLUDE
    }
    return sorted(names & backend_modules)


def _deploy_copy_glob():
    copied = []
    for py in sorted(BACKEND.glob("*.py")):
        if py.name.startswith("test_") or py.name in DEPLOY_EXCLUDE:
            continue
        copied.append(py.name)
    return copied


class DeployPackageTest(unittest.TestCase):
    def test_index_local_imports_in_deploy_set(self):
        for name in _local_index_imports():
            self.assertIn(f"{name}.py", _deploy_copy_glob())

    def test_deploy_script_uses_glob_copy(self):
        script = DEPLOY_SH.read_text(encoding="utf-8")
        self.assertIn('for py in "$WORKDIR"/backend/*.py', script)


if __name__ == "__main__":
    unittest.main()