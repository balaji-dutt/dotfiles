import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "dot_claude/skills/no-email"
CLAUDE = shutil.which("claude")


class NoEmailPluginContractTests(unittest.TestCase):
    def test_manifest_and_hook_module(self):
        manifest = json.loads((PLUGIN / "dot_claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "no-email")
        hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(hooks, {"modules": ["./register.ts"]})
        register = (PLUGIN / "hooks/register.ts").read_text(encoding="utf-8")
        self.assertIn("on('prompt.context'", register)
        self.assertIn("block.name !== 'userEmail'", register)
        self.assertIn("MIT License", (PLUGIN / "LICENSE").read_text(encoding="utf-8"))


class NoEmailPluginBehaviorTests(unittest.TestCase):
    @unittest.skipUnless(CLAUDE, "Claude Code CLI is required")
    def test_validate_and_mod_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "no-email"
            shutil.copytree(PLUGIN, target)
            (target / "dot_claude-plugin").rename(target / ".claude-plugin")
            for argv in ([CLAUDE, "plugin", "validate", "."], [CLAUDE, "plugin", "test", "."]):
                with self.subTest(command=argv[2]):
                    result = subprocess.run(
                        argv, cwd=target, capture_output=True, text=True, encoding="utf-8", timeout=120,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
