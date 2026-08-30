from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets/devcontainer-smoke.py"


@unittest.skipUnless(
    os.environ.get("DEVCONTAINER_SMOKE") == "1",
    "set DEVCONTAINER_SMOKE=1 to run the disposable devcontainer smoke",
)
class DevcontainerSmokeLiveTests(unittest.TestCase):
    def test_disposable_devcontainer_lifecycle(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--run"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=900,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: disposable devcontainer smoke", result.stdout)


if __name__ == "__main__":
    unittest.main()
