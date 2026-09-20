import shutil
import subprocess
import unittest
from pathlib import Path


class AgentAttestationTests(unittest.TestCase):
    def test_packaging(self):
        root = Path(__file__).resolve().parents[1]
        canonical = root / 'dot_claude/skills/agent-attestation/lib/shared'
        container = root / 'private_Documents/development/container-dotfiles/dotfiles'
        for source in canonical.glob('*.mjs'):
            with self.subTest(module=source.name):
                self.assertEqual(source.read_bytes(), (root / 'private_dot_config/opencode/attestation/shared' / source.name).read_bytes())
        for relative in ['dot_claude/skills/agent-attestation', 'private_dot_config/opencode/attestation']:
            for source in (root / relative).rglob('*'):
                if source.is_file():
                    self.assertEqual(source.read_bytes(), (container / source.relative_to(root)).read_bytes())
        rules = (root / '.chezmoiignore').read_text()
        self.assertIn('!/.config/opencode/attestation/**', rules)
        mirrors = (root / 'configs/devcontainer-sync.jsonc').read_text()
        self.assertLess(mirrors.index('agent-attestation-shared'), mirrors.index('opencode-user-config'))

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_behavior(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "tests/support/test_agent_attestation.mjs"],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
