from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_TESTS = (
    REPO_ROOT / "tests" / "support" / "test_repo_opencode_review_plugins.mjs",
    REPO_ROOT / "tests" / "support" / "test_repo_opencode_editor_plugins.mjs",
)


class RepoOpenCodePluginTests(unittest.TestCase):
    def test_node_contract_suite(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required")
        with isolated_environment(prefix="repo-opencode-plugins-") as isolated:
            env = {
                name: value
                for name, value in isolated.env.items()
                if not name.startswith("OPENCODE_") and not name.startswith("DOTFILES_REVIEW_")
            }
            env["DOTFILES_TEST_REPO"] = str(REPO_ROOT)
            result = subprocess.run(
                [node, "--test", *(str(test_path) for test_path in NODE_TESTS)],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")
        self.assertRegex(result.stdout, r"pass [1-9][0-9]*")
        self.assertIn("fail 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
