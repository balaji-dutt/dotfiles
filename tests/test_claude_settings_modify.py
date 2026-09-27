from __future__ import annotations

import json
import unittest

from tests.test_chezmoi_lifecycle_render import CHEZMOI, PLATFORMS, render_template


TEMPLATE = "dot_claude/modify_private_settings.json"
OPUS = "claude-opus-5-5"
AOE_GROUP = {
    "matcher": "*",
    "hooks": [{"type": "command", "command": "aoe notify stop # aoe-hooks"}],
}


def render(platform: str, live: dict[str, object] | None = None) -> dict[str, object]:
    def configure(data: dict[str, object]) -> None:
        data["chezmoi"]["stdin"] = "" if live is None else json.dumps(live)

    return json.loads(render_template(TEMPLATE, platform, configure))


@unittest.skipUnless(CHEZMOI, "requires chezmoi")
class ClaudeSettingsModifyTests(unittest.TestCase):
    def test_fresh_machine_defaults_opus_5_5_to_high(self) -> None:
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                settings = render(platform)
                self.assertEqual(settings["modelSettings"], {OPUS: {"effortLevel": "high"}})
                self.assertEqual(settings["effortLevel"], "high")

    def test_saved_levels_for_other_models_survive_and_the_base_wins_for_its_own(self) -> None:
        live = {
            "effortLevel": "low",
            "modelSettings": {
                "claude-sonnet-5": {"effortLevel": "low"},
                OPUS: {"effortLevel": "medium"},
            },
        }
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                settings = render(platform, live)
                self.assertEqual(
                    settings["modelSettings"],
                    {"claude-sonnet-5": {"effortLevel": "low"}, OPUS: {"effortLevel": "high"}},
                )
                self.assertEqual(settings["effortLevel"], "high")

    def test_malformed_live_model_settings_do_not_break_apply(self) -> None:
        for malformed in ("high", ["claude-sonnet-5"], 3):
            with self.subTest(malformed=malformed):
                settings = render("macos", {"modelSettings": malformed})
                self.assertEqual(settings["modelSettings"], {OPUS: {"effortLevel": "high"}})

    def test_aoe_hook_groups_are_still_adopted(self) -> None:
        live = {"hooks": {"Stop": [AOE_GROUP]}, "modelSettings": {"claude-sonnet-5": {"effortLevel": "low"}}}
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                settings = render(platform, live)
                self.assertIn(AOE_GROUP, settings["hooks"]["Stop"])
                self.assertIn("claude-sonnet-5", settings["modelSettings"])


if __name__ == "__main__":
    unittest.main()
