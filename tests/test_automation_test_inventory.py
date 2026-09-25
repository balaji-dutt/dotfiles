from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "assets/check-automation-test-inventory.py"
CANDIDATES = "configs/automation-candidates.txt"
ISOLATED_GIT = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "commit.gpgsign=false",
    "-c", "user.name=Fixture",
    "-c", "user.email=fixture@example.invalid",
    "-c", "init.defaultBranch=main",
)
ISOLATED_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def owned_entry(entry_id: str, paths: list[str]) -> dict:
    return {
        "id": entry_id,
        "classification": "owned",
        "paths": sorted(paths),
        "owner": {"kind": "repository", "name": "dotfiles"},
        "languages": ["python"],
        "platforms": ["linux"],
        "risk": "low",
        "side_effects": ["none"],
        "test_layers": ["unit"],
        "coverage": {
            "suite_id": f"{entry_id}-suite",
            "status": "planned",
            "work_item": "dots-fixture",
            "test_paths": [],
            "behavior_requirements": [],
        },
        "rationale": "Fixture-owned automation.",
    }


def excluded_entry(entry_id: str, paths: list[str]) -> dict:
    return {
        "id": entry_id,
        "classification": "excluded",
        "paths": sorted(paths),
        "owner": {"kind": "repository", "name": "fixture support"},
        "languages": ["python"],
        "platforms": ["linux"],
        "risk": "low",
        "side_effects": ["none"],
        "test_layers": [],
        "coverage": {
            "suite_id": None,
            "status": "not-applicable",
            "work_item": None,
            "test_paths": [],
            "behavior_requirements": [],
        },
        "rationale": "Test support is not production automation under test.",
    }


def behavior_requirement(
    requirement_id: str,
    kind: str,
    *,
    status: str = "planned",
    test_paths: list[str] | None = None,
) -> dict:
    return {
        "id": requirement_id,
        "kind": kind,
        "description": f"Exercise the {kind} contract.",
        "status": status,
        "test_paths": test_paths or [],
    }


class InventoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        subprocess.run(["git", *ISOLATED_GIT, "init", "-q", str(root)], check=True, env=ISOLATED_ENV)
        self.write("scripts/tool.py", "#!/usr/bin/env python3\nprint('ok')\n")
        self.entries = [owned_entry("tool", ["scripts/tool.py"])]
        self.write_registry()
        self.write_manifest()

    @property
    def manifest_path(self) -> Path:
        return self.root / "configs/automation-test-inventory.json"

    def write(self, relative: str, content: str, *, executable: bool = False) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "--", relative)
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.git("update-index", "--chmod=+x", "--", relative)

    @property
    def candidates_path(self) -> Path:
        return self.root / CANDIDATES

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *ISOLATED_GIT, "-C", str(self.root), *args],
            check=check,
            env=ISOLATED_ENV,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def update_candidates(self) -> subprocess.CompletedProcess[str]:
        result = self.run("--update-candidates")
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        self.git("add", "--", CANDIDATES)
        return result

    def write_manifest(self, *, extra: dict | None = None) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/automation-test-inventory.v3.schema.json",
                    "schema_version": 3,
                    **(extra or {}),
                    "entries": self.entries,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.git("add", "--", "configs/automation-test-inventory.json")
        self.update_candidates()

    def commit(self, message: str) -> None:
        self.git("commit", "-q", "--no-verify", "-m", message)

    def write_registry(
        self,
        *,
        covers: list[str] | None = None,
        suites: list[str] | None = None,
    ) -> None:
        path = self.root / "configs/test-suites.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "id": "tool-suite",
                            "suites": ["fast"] if suites is None else suites,
                            "covers": ["tests/test_tool.py"] if covers is None else covers,
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.git("add", "--", "configs/test-suites.json")

    def run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(self.root), *extra],
            check=False,
            env=ISOLATED_ENV,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


class AutomationInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = InventoryFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_minimal_inventory_passes(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 candidates", result.stdout)

    def test_candidate_listing_is_sorted_and_explains_signals(self) -> None:
        candidates = {
            ".claude/hooks/check.js": "export const hook = true;\n",
            ".chezmoiscripts/run_after_setup.ps1.tmpl": "Write-Output 'ok'\n",
            ".envrc": "#!/bin/sh\nexport FIXTURE=1\n",
            ".gitlab-ci.yml": "job:\n  script: echo ok\n",
            "ansible/tasks/tool.yml": "- ansible.builtin.shell: echo ok\n",
            "dot_bashrc.tmpl": "source ~/.local/share/helper.bash\n",
            "private_dot_config/opencode/plugins/tool.ts": "export const tool = true;\n",
            "tool.ps1": "Write-Output 'no shebang needed'\n",
            "tool.cmd": "@echo off\r\n",
            "templates/extensionless": "{{ if true }}\n#!/bin/sh\necho ok\n",
            "workspace/devcontainer.json": '{"postCreateCommand": "echo ok"}\n',
        }
        for path, content in candidates.items():
            self.fixture.write(path, content)
        self.fixture.write("data/arbitrary.yml", "name: not automation\n")

        result = self.fixture.run("--list-candidates")
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = [line.split("\t", 1)[0] for line in result.stdout.splitlines() if "\t" in line]
        self.assertEqual(listed, sorted(listed))
        for path in candidates:
            self.assertIn(path, listed)
        self.assertNotIn("data/arbitrary.yml", listed)
        self.assertIn("tool.ps1\tscript-extension", result.stdout)
        self.assertIn("templates/extensionless\tshebang", result.stdout)

    def test_executable_mode_discovers_extensionless_file(self) -> None:
        self.fixture.write("tools/no-suffix", "plain contents\n", executable=True)
        result = self.fixture.run("--list-candidates")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tools/no-suffix\texecutable-mode", result.stdout)

    def test_new_tracked_candidate_fails_drift_check(self) -> None:
        self.fixture.write("scripts/new.sh", "#!/bin/sh\n")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "unreviewed candidate 'scripts/new.sh\\tscript-extension,shebang'", result.stderr
        )
        self.assertIn("--update-candidates", result.stderr)
        self.assertIn("unclassified automation candidate 'scripts/new.sh'", result.stderr)

    def test_snapshot_catches_new_candidate_matched_by_existing_glob(self) -> None:
        self.fixture.entries[0]["paths"] = ["scripts/*.py"]
        self.fixture.write_manifest()
        self.fixture.write("scripts/new.py", "print('new')\n")

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreviewed candidate 'scripts/new.py\\tscript-extension'", result.stderr)
        self.assertNotIn("unclassified automation candidate", result.stderr)

    def test_reason_change_on_reviewed_path_fails(self) -> None:
        self.fixture.git("update-index", "--chmod=+x", "--", "scripts/tool.py")

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "unreviewed candidate 'scripts/tool.py\\texecutable-mode,script-extension,shebang'",
            result.stderr,
        )
        self.assertIn(
            "stale reviewed candidate 'scripts/tool.py\\tscript-extension,shebang'", result.stderr
        )

    def test_removed_candidate_reports_stale_line(self) -> None:
        self.fixture.write("scripts/extra.sh", "#!/bin/sh\n")
        self.fixture.entries.append(owned_entry("extra", ["scripts/extra.sh"]))
        self.fixture.entries.sort(key=lambda entry: entry["id"])
        self.fixture.write_manifest()
        self.fixture.entries = [entry for entry in self.fixture.entries if entry["id"] != "extra"]
        self.fixture.git("rm", "-q", "--cached", "--", "scripts/extra.sh")
        manifest = json.loads(self.fixture.manifest_path.read_text(encoding="utf-8"))
        manifest["entries"] = self.fixture.entries
        self.fixture.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "stale reviewed candidate 'scripts/extra.sh\\tscript-extension,shebang'", result.stderr
        )
        self.assertNotIn("unreviewed candidate", result.stderr)

    def test_missing_candidate_list_fails(self) -> None:
        self.fixture.candidates_path.unlink()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("automation-candidates.txt: cannot read file", result.stderr)

    def test_malformed_candidate_list_fails(self) -> None:
        cases = {
            "blank": ("scripts/tool.py\tscript-extension,shebang\n\n", "line 2 is blank"),
            "missing-tab": ("scripts/tool.py script-extension\n", "line 1 must be '<path><TAB><reasons>'"),
            "empty-reasons": ("scripts/tool.py\t\n", "line 1 must be '<path><TAB><reasons>'"),
            "duplicate": (
                "scripts/tool.py\tscript-extension,shebang\n" * 2,
                "line 2 duplicates",
            ),
            "unsorted": (
                "scripts/z.py\tscript-extension\nscripts/a.py\tscript-extension\n",
                "line 2 must be sorted after",
            ),
        }
        for name, (content, message) in cases.items():
            with self.subTest(case=name):
                self.fixture.candidates_path.write_text(content, encoding="utf-8")
                result = self.fixture.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_crlf_candidate_list_passes(self) -> None:
        text = self.fixture.candidates_path.read_text(encoding="utf-8")
        self.fixture.candidates_path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_with_candidate_digest_fails(self) -> None:
        self.fixture.write_manifest(extra={"candidate_digest": "sha256:" + "0" * 64})

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidate_digest is not part of schema v3", result.stderr)

    def test_update_candidates_reports_changes_and_clears_drift(self) -> None:
        self.fixture.entries[0]["paths"] = ["scripts/*.py"]
        self.fixture.write_manifest()
        self.fixture.write("scripts/new.py", "print('new')\n")
        self.assertNotEqual(self.fixture.run().returncode, 0)

        update = self.fixture.run("--update-candidates")

        self.assertEqual(update.returncode, 0, update.stderr)
        self.assertIn("+ scripts/new.py\tscript-extension", update.stdout)
        self.assertIn("2 total, 1 added, 0 removed", update.stdout)
        self.assertEqual(
            self.fixture.candidates_path.read_bytes(),
            b"scripts/new.py\tscript-extension\nscripts/tool.py\tscript-extension,shebang\n",
        )
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_listing_and_update_are_mutually_exclusive(self) -> None:
        result = self.fixture.run("--list-candidates", "--update-candidates")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with argument", result.stderr)

    def test_duplicate_candidate_fails(self) -> None:
        self.fixture.entries.append(owned_entry("tool-copy", ["scripts/tool.py"]))
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicates candidate 'scripts/tool.py'", result.stderr)

    def test_untracked_and_non_candidate_paths_fail(self) -> None:
        self.fixture.write("data/value.txt", "not code\n")
        self.fixture.entries[0]["paths"].extend(["data/value.txt", "missing.py"])
        self.fixture.entries[0]["paths"].sort()
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-candidate path 'data/value.txt'", result.stderr)
        self.assertIn("untracked path 'missing.py'", result.stderr)

    def test_invalid_schema_enum_and_order_fail(self) -> None:
        self.fixture.entries[0]["risk"] = "surprising"
        self.fixture.entries[0]["platforms"] = ["wsl2", "linux"]
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            "platforms must be sorted" in result.stderr or "risk is unsupported" in result.stderr,
            result.stderr,
        )

    def test_owned_entry_requires_suite_and_work_item(self) -> None:
        self.fixture.entries[0]["coverage"]["suite_id"] = None
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("coverage.suite_id must be a non-empty string", result.stderr)

    def test_critical_entry_requires_success_failure_and_safety_matrix(self) -> None:
        entry = self.fixture.entries[0]
        entry["risk"] = "critical"
        entry["test_layers"] = ["integration"]
        entry["coverage"]["behavior_requirements"] = [
            behavior_requirement("success-case", "success")
        ]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing critical kind(s): failure, safety", result.stderr)

    def test_critical_covered_matrix_requires_registered_behavior_evidence(self) -> None:
        self.fixture.write("tests/test_tool.py", "import unittest\n")
        self.fixture.entries.append(excluded_entry("test-support", ["tests/test_tool.py"]))
        self.fixture.entries.sort(key=lambda entry: entry["id"])
        entry = next(item for item in self.fixture.entries if item["id"] == "tool")
        entry["risk"] = "critical"
        entry["test_layers"] = ["integration"]
        entry["coverage"].update(
            {
                "status": "covered",
                "test_paths": ["tests/test_tool.py"],
                "behavior_requirements": [
                    behavior_requirement(
                        "failure-case", "failure", status="covered", test_paths=["tests/test_tool.py"]
                    ),
                    behavior_requirement(
                        "safety-case", "safety", status="covered", test_paths=["tests/test_tool.py"]
                    ),
                    behavior_requirement(
                        "success-case", "success", status="covered", test_paths=["tests/test_tool.py"]
                    ),
                ],
            }
        )
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_critical_status_and_evidence_must_match_requirement_states(self) -> None:
        entry = self.fixture.entries[0]
        entry["risk"] = "critical"
        entry["test_layers"] = ["integration"]
        entry["coverage"]["status"] = "partial"
        entry["coverage"]["test_paths"] = ["tests/test_tool.py"]
        entry["coverage"]["behavior_requirements"] = [
            behavior_requirement("failure-case", "failure"),
            behavior_requirement("safety-case", "safety"),
            behavior_requirement("success-case", "success"),
        ]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("coverage.status must be 'planned'", result.stderr)

    def test_covered_test_must_be_registered(self) -> None:
        self.fixture.write("tests/test_tool.py", "import unittest\n")
        self.fixture.entries.append(excluded_entry("test-support", ["tests/test_tool.py"]))
        self.fixture.entries.sort(key=lambda entry: entry["id"])
        entry = next(item for item in self.fixture.entries if item["id"] == "tool")
        entry["coverage"]["status"] = "covered"
        entry["coverage"]["test_paths"] = ["tests/test_tool.py"]
        self.fixture.write_registry(covers=["tests/test_other.py"])
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references unregistered test 'tests/test_tool.py'", result.stderr)

    def test_owned_evidence_requires_behavioral_suite_registration(self) -> None:
        self.fixture.write("tests/test_tool.py", "import unittest\n")
        self.fixture.entries.append(excluded_entry("test-support", ["tests/test_tool.py"]))
        self.fixture.entries.sort(key=lambda entry: entry["id"])
        entry = next(item for item in self.fixture.entries if item["id"] == "tool")
        entry["coverage"]["status"] = "covered"
        entry["coverage"]["test_paths"] = ["tests/test_tool.py"]
        self.fixture.write_registry(suites=["provenance"])
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires behavioral suite registration", result.stderr)

    def test_registry_step_without_coverage_is_ignored(self) -> None:
        self.fixture.write_registry(covers=[])

        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_planned_entry_cannot_claim_test_evidence(self) -> None:
        entry = self.fixture.entries[0]
        entry["coverage"]["test_paths"] = ["tests/test_tool.py"]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test_paths must be empty for planned coverage", result.stderr)

    def test_platform_only_entry_requires_platform_smoke(self) -> None:
        entry = self.fixture.entries[0]
        entry["platforms"] = ["windows"]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must include platform-smoke for platform-only automation", result.stderr)

    def test_high_risk_entry_requires_contract_or_integration(self) -> None:
        entry = self.fixture.entries[0]
        entry["risk"] = "high"
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must include contract or integration for high risk", result.stderr)

    def test_behavior_requirement_ids_must_be_sorted(self) -> None:
        entry = self.fixture.entries[0]
        entry["risk"] = "critical"
        entry["test_layers"] = ["integration"]
        entry["coverage"]["behavior_requirements"] = [
            behavior_requirement("success-case", "success"),
            behavior_requirement("failure-case", "failure"),
            behavior_requirement("safety-case", "safety"),
        ]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be unique and sorted", result.stderr)

    def test_exclusion_contract_rejects_side_effects_and_missing_repository_owner(self) -> None:
        entry = excluded_entry("tool", ["scripts/tool.py"])
        entry["owner"]["kind"] = "none"
        entry["side_effects"] = ["filesystem"]
        self.fixture.entries = [entry]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owner.kind must be repository for exclusions", result.stderr)

    def test_generated_entry_requires_covered_provenance(self) -> None:
        entry = excluded_entry("tool", ["scripts/tool.py"])
        entry.update(
            {
                "classification": "generated",
                "owner": {"kind": "generator", "name": "fixture generator"},
                "risk": "medium",
            }
        )
        entry["coverage"].update(
            {
                "suite_id": "automation-provenance",
                "status": "planned",
                "work_item": "dots-fixture",
            }
        )
        self.fixture.entries = [entry]
        self.fixture.write_manifest()

        result = self.fixture.run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must include provenance for generated automation", result.stderr)

    def test_non_owned_entry_requires_rationale(self) -> None:
        self.fixture.entries = [excluded_entry("tool", ["scripts/tool.py"])]
        self.fixture.entries[0]["rationale"] = ""
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rationale must be a non-empty string", result.stderr)

    def test_covered_suite_requires_tracked_test_path(self) -> None:
        coverage = self.fixture.entries[0]["coverage"]
        coverage["status"] = "covered"
        coverage["test_paths"] = ["tests/test_tool.py"]
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references untracked path 'tests/test_tool.py'", result.stderr)

    def test_current_repository_and_agent_merge_coverage_pass(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(REPO_ROOT)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        inventory = json.loads(
            (REPO_ROOT / "configs/automation-test-inventory.json").read_text(encoding="utf-8")
        )
        matches = [
            entry
            for entry in inventory["entries"]
            if "assets/agent-wt-merge" in entry["paths"]
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["coverage"]["status"], "covered")
        self.assertIn("tests/test_agent_wt_merge.py", matches[0]["coverage"]["test_paths"])

        registry = json.loads((REPO_ROOT / "configs/test-suites.json").read_text(encoding="utf-8"))
        inventory_step = next(
            step for step in registry["steps"] if step["id"] == "automation-test-inventory"
        )
        self.assertEqual(inventory_step["suites"], ["fast", "provenance"])

        for reviewer_path in (
            REPO_ROOT / ".claude/agents/dotfiles-reviewer.md",
            REPO_ROOT / ".opencode/agents/dotfiles-reviewer.md",
        ):
            reviewer = reviewer_path.read_text(encoding="utf-8")
            self.assertIn("## Automation coverage review", reviewer)
            self.assertIn("configs/automation-test-inventory.json", reviewer)
            for term in ("failure", "owner", "rationale", "safety", "success"):
                self.assertIn(term, reviewer)


class ConcurrentBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = InventoryFixture(Path(self.temporary.name))
        self.fixture.entries[0]["paths"] = ["scripts/*.py"]
        self.fixture.write_manifest()
        self.fixture.commit("base")
        self.fixture.git("branch", "feature")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def land(self, branch: str, path: str, *, review: bool = True) -> None:
        self.fixture.git("checkout", "-q", branch)
        self.fixture.write(path, "print('added')\n")
        if review:
            self.fixture.update_candidates()
        self.fixture.commit(f"add {path}")

    def merge_feature(self) -> subprocess.CompletedProcess[str]:
        self.fixture.git("checkout", "-q", "main")
        return self.fixture.git("merge", "--no-ff", "--no-edit", "--no-verify", "feature", check=False)

    def test_reviewed_additions_on_both_branches_merge_cleanly_and_pass(self) -> None:
        self.land("feature", "scripts/a.py")
        self.land("main", "scripts/z.py")

        merge = self.merge_feature()

        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.fixture.candidates_path.read_text(encoding="utf-8").splitlines(),
            [
                "scripts/a.py\tscript-extension",
                "scripts/tool.py\tscript-extension,shebang",
                "scripts/z.py\tscript-extension",
            ],
        )

    def test_additions_in_the_same_slot_conflict_at_merge_time(self) -> None:
        self.land("feature", "scripts/b.py")
        self.land("main", "scripts/c.py")

        merge = self.merge_feature()

        self.assertNotEqual(merge.returncode, 0)
        unmerged = self.fixture.git("diff", "--name-only", "--diff-filter=U").stdout.split()
        self.assertEqual(unmerged, [CANDIDATES])
        conflicted = self.fixture.run()
        self.assertNotEqual(conflicted.returncode, 0)
        self.assertIn("merge conflict marker", conflicted.stderr)

        update = self.fixture.run("--update-candidates")

        self.assertEqual(update.returncode, 0, update.stderr)
        self.assertIn("3 total, 0 added, 0 removed", update.stdout)
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreviewed_branch_fails_after_clean_merge(self) -> None:
        self.land("feature", "scripts/a.py", review=False)
        self.land("main", "scripts/z.py")

        merge = self.merge_feature()

        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreviewed candidate 'scripts/a.py\\tscript-extension'", result.stderr)
        self.assertNotIn("scripts/z.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
