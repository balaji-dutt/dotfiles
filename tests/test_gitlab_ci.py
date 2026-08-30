from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".gitlab-ci.yml"


def top_level_block(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line == f"{name}:"
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"[A-Za-z0-9_.-]+:", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end]) + "\n"


def job_rules(block: str) -> list[tuple[str, str]]:
    lines = block.splitlines()
    rules_index = lines.index("  rules:")
    rules: list[tuple[str, str]] = []
    for line in lines[rules_index + 1 :]:
        match = re.fullmatch(r"    - if: '(.*)'", line)
        if match:
            rules.append((match.group(1), "on_success"))
            continue
        when = re.fullmatch(r"      when: ([a-z_]+)", line)
        if when and rules:
            expression, _ = rules[-1]
            rules[-1] = (expression, when.group(1))
    return rules


@dataclass(frozen=True)
class PipelineContext:
    source: str
    branch: str = ""
    default_branch: str = "main"
    mr_source_branch: str = ""
    mr_labels: str = ""
    open_merge_requests: bool = False


RENOVATE_MR = (
    '$CI_PIPELINE_SOURCE == "merge_request_event" && '
    "$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME =~ /^renovate\\//"
)
RENOVATE_LABEL = (
    '$CI_PIPELINE_SOURCE == "merge_request_event" && '
    "$CI_MERGE_REQUEST_LABELS =~ /(^|,)renovate(,|$)/i"
)
RENOVATE_PUSH = (
    '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH =~ /^renovate\\//'
)
ANY_MR = '$CI_PIPELINE_SOURCE == "merge_request_event"'
PUSH_WITH_OPEN_MR = (
    '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH && '
    "$CI_COMMIT_BRANCH != $CI_DEFAULT_BRANCH && $CI_OPEN_MERGE_REQUESTS"
)
FEATURE_PUSH = (
    '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH && '
    "$CI_COMMIT_BRANCH != $CI_DEFAULT_BRANCH"
)
BRANCH_PUSH = '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH'
SCHEDULE = '$CI_PIPELINE_SOURCE == "schedule"'
WEB = '$CI_PIPELINE_SOURCE == "web"'


def rule_matches(expression: str, context: PipelineContext) -> bool:
    matchers = {
        RENOVATE_MR: context.source == "merge_request_event"
        and context.mr_source_branch.startswith("renovate/"),
        RENOVATE_LABEL: context.source == "merge_request_event"
        and "renovate" in {label.casefold() for label in context.mr_labels.split(",")},
        RENOVATE_PUSH: context.source == "push"
        and context.branch.startswith("renovate/"),
        ANY_MR: context.source == "merge_request_event",
        PUSH_WITH_OPEN_MR: context.source == "push"
        and bool(context.branch)
        and context.branch != context.default_branch
        and context.open_merge_requests,
        FEATURE_PUSH: context.source == "push"
        and bool(context.branch)
        and context.branch != context.default_branch,
        BRANCH_PUSH: context.source == "push" and bool(context.branch),
        SCHEDULE: context.source == "schedule",
        WEB: context.source == "web",
    }
    if expression not in matchers:
        raise AssertionError(f"unsupported CI-rule expression in contract test: {expression}")
    return matchers[expression]


def evaluate(rules: list[tuple[str, str]], context: PipelineContext) -> str | None:
    for expression, when in rules:
        if rule_matches(expression, context):
            return when
    return None


class GitLabCiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CI_PATH.read_text(encoding="utf-8")

    def test_linux_fast_has_exact_ordered_policy(self) -> None:
        rules = job_rules(top_level_block(self.text, "linux-fast"))
        self.assertEqual(
            rules,
            [
                (RENOVATE_MR, "never"),
                (RENOVATE_LABEL, "never"),
                (RENOVATE_PUSH, "never"),
                (ANY_MR, "on_success"),
                (PUSH_WITH_OPEN_MR, "never"),
                (FEATURE_PUSH, "on_success"),
            ],
        )
        cases = (
            ("feature push", PipelineContext("push", branch="feature/test"), "on_success"),
            (
                "feature push with MR",
                PipelineContext("push", branch="feature/test", open_merge_requests=True),
                "never",
            ),
            (
                "merge request",
                PipelineContext("merge_request_event", mr_source_branch="feature/test"),
                "on_success",
            ),
            ("renovate push", PipelineContext("push", branch="renovate/tool"), "never"),
            (
                "renovate MR branch",
                PipelineContext("merge_request_event", mr_source_branch="renovate/tool"),
                "never",
            ),
            (
                "renovate MR label",
                PipelineContext(
                    "merge_request_event",
                    mr_source_branch="feature/test",
                    mr_labels="dependencies,Renovate",
                ),
                "never",
            ),
            ("main push", PipelineContext("push", branch="main"), None),
            ("schedule", PipelineContext("schedule", branch="main"), None),
            ("web", PipelineContext("web", branch="feature/test"), None),
            ("tag", PipelineContext("push"), None),
            ("api", PipelineContext("api", branch="feature/test"), None),
        )
        for label, context, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(evaluate(rules, context), expected)

    def test_manual_jobs_have_exact_opt_in_policy(self) -> None:
        expected_rules = [
            (RENOVATE_MR, "never"),
            (RENOVATE_LABEL, "never"),
            (RENOVATE_PUSH, "never"),
            (ANY_MR, "manual"),
            (BRANCH_PUSH, "manual"),
            (SCHEDULE, "manual"),
            (WEB, "manual"),
        ]
        for name in ("devcontainer-smoke", "linux-all", "windows-all"):
            with self.subTest(job=name):
                rules = job_rules(top_level_block(self.text, name))
                self.assertEqual(rules, expected_rules)
                self.assertEqual(evaluate(rules, PipelineContext("schedule")), "manual")
                self.assertEqual(evaluate(rules, PipelineContext("web")), "manual")
                self.assertEqual(
                    evaluate(rules, PipelineContext("push", branch="main")), "manual"
                )
                self.assertEqual(
                    evaluate(rules, PipelineContext("push", branch="renovate/tool")),
                    "never",
                )
                self.assertIsNone(evaluate(rules, PipelineContext("push")))

    def test_new_jobs_are_independent_and_publish_registration_reports(self) -> None:
        linux_fast = top_level_block(self.text, "linux-fast")
        linux_all = top_level_block(self.text, "linux-all")
        devcontainer_smoke = top_level_block(self.text, "devcontainer-smoke")
        windows_all = top_level_block(self.text, "windows-all")
        for block in (linux_fast, linux_all, devcontainer_smoke, windows_all):
            self.assertIn("  stage: test\n", block)
            self.assertIn("  needs: []\n", block)
            self.assertIn("  interruptible: true\n", block)
            self.assertIn("  artifacts:\n", block)
            self.assertIn("    when: always\n", block)
            self.assertIn("    expire_in: 7 days\n", block)
        for block in (linux_fast, linux_all, windows_all):
            self.assertIn("--report-file", block)

        self.assertIn("  image: python:3.13.7-bookworm\n", linux_fast)
        self.assertIn("./assets/run-tests.sh fast", linux_fast)
        self.assertIn("--require-capability git --require-capability sh", linux_fast)
        self.assertIn("  allow_failure: true\n", linux_all)
        self.assertIn("./assets/run-tests.sh all", linux_all)
        self.assertIn("  allow_failure: true\n", devcontainer_smoke)
        self.assertIn("    - devcontainer-smoke\n", devcontainer_smoke)
        self.assertNotIn("  image:", devcontainer_smoke)
        self.assertIn('    DEVCONTAINER_SMOKE: "1"\n', devcontainer_smoke)
        self.assertIn("python3 assets/devcontainer-smoke.py --run", devcontainer_smoke)
        self.assertIn("      - ci-artifacts/devcontainer-smoke/\n", devcontainer_smoke)
        self.assertIn("  allow_failure: true\n", windows_all)
        self.assertIn("    - saas-windows-medium-amd64\n", windows_all)
        self.assertNotIn("  image:", windows_all)
        self.assertIn("assets/run-tests.ps1 all", windows_all)

    def test_existing_sync_job_dispatch_contract_is_unchanged(self) -> None:
        expected_rules = [
            ('$CI_PIPELINE_SOURCE == "merge_request_event"', "on_success"),
            ('$CI_PIPELINE_SOURCE == "push"', "on_success"),
        ]
        sentinels = {
            "statusline-sync": "bash assets/sync-statusline.sh --check",
            "beads-kanban-pin-sync": "bash assets/sync-beads-kanban-pin.sh --check",
            "browser-policy-sync": "python3 assets/sync-browser-policies.py --check",
        }
        for name, sentinel in sentinels.items():
            with self.subTest(job=name):
                block = top_level_block(self.text, name)
                self.assertIn("  stage: sync\n", block)
                self.assertEqual(job_rules(block), expected_rules)
                self.assertIn(sentinel, block)


if __name__ == "__main__":
    unittest.main()
