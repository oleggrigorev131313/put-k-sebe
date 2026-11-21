#!/usr/bin/env python3
"""Ensure backend/deploy.sh ships every module index.py imports locally."""

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
    stdlibish = {
        "base64",
        "hashlib",
        "hmac",
        "json",
        "logging",
        "os",
        "re",
        "time",
        "uuid",
        "threading",
        "urllib",
        "datetime",
        "http",
        "socket",
        "zoneinfo",
        "random",
        "subprocess",
        "types",
        "io",
        "argparse",
    }
    backend_modules = {
        p.stem
        for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_")
        and p.name not in DEPLOY_EXCLUDE
    }
    return sorted(names & backend_modules)


def _deploy_copy_glob():
    """Files deploy.sh would copy (mirrors the shell loop)."""
    copied = []
    for py in sorted(BACKEND.glob("*.py")):
        if py.name.startswith("test_"):
            continue
        if py.name in DEPLOY_EXCLUDE:
            continue
        copied.append(py.name)
    return copied


class DeployPackageTest(unittest.TestCase):
    def test_index_local_imports_in_deploy_set(self):
        needed = _local_index_imports()
        packaged = _deploy_copy_glob()
        for name in needed:
            self.assertIn(
                f"{name}.py",
                packaged,
                f"index.py imports {name}; deploy package must include {name}.py",
            )

    def test_deploy_script_copies_wildcard_not_hardcoded_only(self):
        script = DEPLOY_SH.read_text(encoding="utf-8")
        self.assertIn('for py in "$WORKDIR"/backend/*.py', script)
        self.assertNotIn('cp "$WORKDIR/backend/assistant_quota.py"', script)


if __name__ == "__main__":
    unittest.main()