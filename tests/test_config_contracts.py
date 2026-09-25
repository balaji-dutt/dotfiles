from __future__ import annotations

import json
import re
import runpy
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BEADS_KANBAN_INSTALLS = {
    ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.sh.tmpl":
        'FORK_VERSION="2.2.0"',
    ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.ps1.tmpl":
        '$ForkVersion             = "2.2.0"',
    "private_Documents/development/container-dotfiles/devcontainers/gitlab.com/"
    "servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh":
        '    fork_version="2.2.0"',
}
CONFIGS = REPO_ROOT / "configs"
LOAD_JSON = runpy.run_path(str(REPO_ROOT / "assets/check-ai-tooling.py"))["load_json"]
CONTRACTS = {
    "automation-provenance.json": {
        "schema": "schemas/automation-provenance.v2.schema.json",
        "ref": "./schemas/automation-provenance.v2.schema.json",
        "version": 2,
        "consumers": ("assets/check-automation-provenance.py",),
    },
    "automation-test-inventory.json": {
        "schema": "schemas/automation-test-inventory.v3.schema.json",
        "ref": "./schemas/automation-test-inventory.v3.schema.json",
        "version": 3,
        "consumers": ("assets/check-automation-test-inventory.py",),
    },
    "gitlab-pipeline-guard.json": {
        "schema": "schemas/gitlab-pipeline-guard.v1.schema.json",
        "ref": "./schemas/gitlab-pipeline-guard.v1.schema.json",
        "version": 1,
        "consumers": (
            "assets/check-gitlab-pipeline.py",
            "assets/gitlab_pipeline_runtime.py",
        ),
    },
    "test-suites.json": {
        "schema": "schemas/test-suites.v1.schema.json",
        "ref": "./schemas/test-suites.v1.schema.json",
        "version": 1,
        "consumers": ("assets/run-tests.py",),
    },
    "ai-tooling-support.json": {
        "schema": "schemas/ai-tooling-support.v1.schema.json",
        "ref": "./schemas/ai-tooling-support.v1.schema.json",
        "version": 1,
        "consumers": ("assets/check-ai-tooling.py",),
    },
    "claude-mcp.json": {
        "schema": "schemas/claude-mcp.v1.schema.json",
        "ref": "./schemas/claude-mcp.v1.schema.json",
        "version": 1,
        "consumers": (
            "assets/check-ai-tooling.py",
            "assets/claude-mcp-apply.py",
            ".chezmoiscripts/run_onchange_after_claude_mcp_servers.ps1.tmpl",
        ),
    },
    "devcontainer-sync.jsonc": {
        "schema": "schemas/devcontainer-sync.v1.schema.json",
        "ref": "./schemas/devcontainer-sync.v1.schema.json",
        "version": 1,
        "jsonc": True,
        "consumers": (
            "assets/check-automation-provenance.py",
            "assets/sync-devcontainer-assets.sh",
            "bin/executable_devcontainer-launch.tmpl",
        ),
    },
    "host-ai-plugin-refresh.jsonc": {
        "schema": "schemas/host-ai-plugin-refresh.v1.schema.json",
        "ref": "./schemas/host-ai-plugin-refresh.v1.schema.json",
        "version": 1,
        "jsonc": True,
        "consumers": (
            ".chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.sh.tmpl",
            ".chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.ps1.tmpl",
        ),
    },
    "browser-policies/justthebrowser/manifest.json": {
        "schema": "schemas/justthebrowser-manifest.v1.schema.json",
        "ref": "../../schemas/justthebrowser-manifest.v1.schema.json",
        "version": 1,
        "consumers": ("assets/sync-browser-policies.py",),
    },
    "plannotator-assets.json": {
        "schema": "schemas/plannotator-assets.v1.schema.json",
        "ref": "./schemas/plannotator-assets.v1.schema.json",
        "version": 1,
        "consumers": ("assets/sync-plannotator-assets.py",),
    },
}
YAML_CONTRACTS = {
    "packages.yaml": {
        "schema": "schemas/packages.v1.schema.json",
        "ref": "./schemas/packages.v1.schema.json",
        "consumers": ("ansible/wsl-playbook.yml",),
    }
}
RETAINED_SCHEMAS = {
    "schemas/automation-provenance.v1.schema.json",
    "schemas/automation-test-inventory.v1.schema.json",
    "schemas/automation-test-inventory.v2.schema.json",
}
STANDALONE_SCHEMAS = {
    "schemas/pipeline-guard.v1.schema.json": {
        "id": "urn:dotfiles:schema:pipeline-guard:v1",
        "version": 1,
        "authority": "docs/tooling/continuous-integration.md",
    },
    "schemas/ai-attestation-handoff.v1.schema.json": {
        "id": "urn:dotfiles:schema:ai-attestation-handoff:v1",
        "version": 1,
        "authority": "docs/git-agent-attestation.md",
    }
}


class ConfigContractTests(unittest.TestCase):
    def test_devcontainer_identity_contract_and_platform_defaults(self):
        schema = LOAD_JSON(CONFIGS / "schemas/devcontainer-sync.v1.schema.json")
        platform = schema["$defs"]["platform"]
        self.assertNotIn("identity_labels", platform["required"])
        labels = platform["properties"]["identity_labels"]
        self.assertEqual(set(labels["required"]), {"devcontainer.local_folder", "devcontainer.config_file"})
        key_pattern = labels["propertyNames"]["pattern"]
        for key in ("devcontainer.local_folder", "custom-owner_1"):
            self.assertIsNotNone(re.search(key_pattern, key))
        for key in ("", "bad=key", "bad\n", "bad key"):
            self.assertIsNone(re.search(key_pattern, key))
        value_contract = labels["additionalProperties"]
        self.assertEqual(value_contract["type"], "string")
        self.assertEqual(value_contract["minLength"], 1)
        self.assertIsNotNone(re.search(value_contract["not"]["pattern"], "unsafe\n"))
        self.assertIsNone(re.search(value_contract["not"]["pattern"], r"\\wsl.localhost\Debian\space = one"))
        manifest = LOAD_JSON(CONFIGS / "devcontainer-sync.jsonc", jsonc=True)
        platforms = manifest["devcontainers"]["homelab-IaC"]["launcher"]["platforms"]
        self.assertEqual(platforms["darwin"]["identity_labels"]["devcontainer.local_folder"], "{workspace}")
        self.assertEqual(platforms["wsl2-debian"]["identity_labels"]["devcontainer.local_folder"],
                         r"\\wsl.localhost\Debian{workspace_backslashes}")
        for spec in platforms.values():
            self.assertEqual(spec["identity_labels"]["devcontainer.config_file"], "{config}")

    def beads_kanban_manager(self) -> tuple[dict, re.Pattern]:
        config = LOAD_JSON(REPO_ROOT / "renovate.json5", jsonc=True)
        managers = [
            manager for manager in config["customManagers"]
            if manager.get("depNameTemplate") == "balajidutt/better-beads-kanban"
        ]
        self.assertEqual(len(managers), 1)
        manager = managers[0]
        self.assertEqual(len(manager["matchStrings"]), 1)
        # Python's named-group spelling differs from Renovate's RE2 syntax.
        pattern = manager["matchStrings"][0].replace("(?<currentValue>", "(?P<currentValue>")
        return manager, re.compile(pattern)

    def test_beads_kanban_manager_covers_each_install_once(self) -> None:
        manager, pattern = self.beads_kanban_manager()
        self.assertEqual(len(manager["managerFilePatterns"]), 3)
        for relative_path in BEADS_KANBAN_INSTALLS:
            with self.subTest(path=relative_path):
                self.assertEqual(sum(
                    bool(re.search(file_pattern[1:-1], relative_path))
                    for file_pattern in manager["managerFilePatterns"]
                ), 1)
                source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                matches = list(pattern.finditer(source))
                self.assertEqual(len(matches), 1)
                self.assertRegex(matches[0]["currentValue"], r"^\d+\.\d+\.\d+$")

    def test_beads_kanban_checkout_match_replaces_only_the_lf_blob_pin(self) -> None:
        _, pattern = self.beads_kanban_manager()
        for relative_path, assignment in BEADS_KANBAN_INSTALLS.items():
            for newline in ("\n", "\r\n"):
                for prefix in ("", 'unrelated="2.2.0"\n'):
                    with self.subTest(path=relative_path, newline=repr(newline), prefix=prefix):
                        suffix = '\nunrelated="2.2.0"\n'
                        blob = prefix + assignment + suffix
                        checkout = blob.replace("\n", newline)
                        matches = list(pattern.finditer(checkout))
                        self.assertEqual(len(matches), 1)
                        match = matches[0]
                        self.assertEqual(match["currentValue"], "2.2.0")
                        replace_string = match[0]
                        self.assertEqual(blob.count(replace_string), 1)
                        updated = blob.replace(
                            replace_string,
                            replace_string.replace(match["currentValue"], "2.2.2", 1),
                            1,
                        )
                        self.assertEqual(
                            updated, prefix + assignment.replace("2.2.0", "2.2.2") + suffix
                        )

    def test_beads_kanban_manager_ignores_comments_and_unrelated_assignments(self) -> None:
        _, pattern = self.beads_kanban_manager()
        for assignment in BEADS_KANBAN_INSTALLS.values():
            with self.subTest(assignment=assignment):
                self.assertIsNone(pattern.search("# " + assignment))
                self.assertIsNone(pattern.search("unrelated=" + assignment))

    def test_instances_link_to_immutable_draft_2020_12_schemas(self) -> None:
        for instance_name, contract in CONTRACTS.items():
            with self.subTest(instance=instance_name):
                instance = LOAD_JSON(
                    CONFIGS / instance_name, jsonc=contract.get("jsonc", False)
                )
                schema = json.loads(
                    (CONFIGS / contract["schema"]).read_text(encoding="utf-8")
                )
                self.assertEqual(instance["$schema"], contract["ref"])
                self.assertEqual(instance["schema_version"], contract["version"])
                self.assert_schema_metadata(schema, contract["version"])
                self.assertEqual(
                    schema["properties"]["$schema"]["const"], contract["ref"]
                )
                self.assertEqual(
                    schema["properties"]["schema_version"]["const"],
                    contract["version"],
                )

    def test_yaml_contract_uses_editor_association_without_ansible_variables(self) -> None:
        for instance_name, contract in YAML_CONTRACTS.items():
            with self.subTest(instance=instance_name):
                text = (CONFIGS / instance_name).read_text(encoding="utf-8")
                schema = json.loads(
                    (CONFIGS / contract["schema"]).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    text.splitlines()[0],
                    f"# yaml-language-server: $schema={contract['ref']}",
                )
                self.assertNotRegex(text, r"(?m)^schema_version:")
                self.assert_schema_metadata(schema, 1)

    def assert_schema_metadata(self, schema: dict, version: int) -> None:
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertTrue(schema["$id"].endswith(f":v{version}"))
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])

    def test_runtime_consumers_pin_the_same_schema_reference(self) -> None:
        for instance_name, contract in CONTRACTS.items():
            for consumer_name in contract["consumers"]:
                with self.subTest(instance=instance_name, consumer=consumer_name):
                    consumer = (REPO_ROOT / consumer_name).read_text(encoding="utf-8")
                    self.assertIn(contract["ref"], consumer)

    def test_standalone_schemas_have_metadata_and_catalog_authority(self) -> None:
        catalog = (REPO_ROOT / "docs/tooling/config-contracts.md").read_text(
            encoding="utf-8"
        )
        for relative_path, contract in STANDALONE_SCHEMAS.items():
            with self.subTest(schema=relative_path):
                schema = json.loads(
                    (CONFIGS / relative_path).read_text(encoding="utf-8")
                )
                self.assert_schema_metadata(schema, contract["version"])
                self.assertEqual(schema["$id"], contract["id"])
                self.assertIn(f"`configs/{relative_path}`", catalog)
                self.assertIn(f"`{contract['authority']}`", catalog)

    def test_ai_attestation_handoff_contract_is_closed_and_paired(self) -> None:
        schema = json.loads(
            (
                CONFIGS / "schemas/ai-attestation-handoff.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(schema["required"]), {"participants", "schemaVersion"})
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], 1)
        participants = schema["properties"]["participants"]
        self.assertEqual(participants["minItems"], 1)
        self.assertEqual(participants["maxItems"], 8)
        self.assertEqual(schema["$defs"]["identifier"]["maxLength"], 128)
        self.assertEqual(schema["$defs"]["sourceDefinition"]["maxLength"], 512)

        participant = schema["$defs"]["participant"]
        self.assertFalse(participant["additionalProperties"])
        self.assertEqual(participant["required"], ["tool"])
        self.assertEqual(
            participant["dependentRequired"],
            {
                "sourceDefinition": ["sourceDigest"],
                "sourceDigest": ["sourceDefinition"],
            },
        )
        self.assertEqual(
            schema["$defs"]["sourceDigest"]["pattern"],
            "^sha256:[0-9a-f]{64}$",
        )

    def test_contract_catalog_lists_every_managed_instance_schema_and_consumer(self) -> None:
        catalog = (REPO_ROOT / "docs/tooling/config-contracts.md").read_text(
            encoding="utf-8"
        )
        for instance_name, contract in {**CONTRACTS, **YAML_CONTRACTS}.items():
            with self.subTest(instance=instance_name):
                self.assertIn(f"`configs/{instance_name}`", catalog)
                self.assertIn(f"`configs/{contract['schema']}`", catalog)
                for consumer_name in contract["consumers"]:
                    self.assertIn(f"`{consumer_name}`", catalog)

    def test_catalog_records_migration_and_validator_decisions(self) -> None:
        catalog = (REPO_ROOT / "docs/tooling/config-contracts.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("first formal version", catalog)
        self.assertIn("Breaking changes", catalog)
        self.assertIn("check-jsonschema", catalog)
        self.assertIn("SourceMeta", catalog)
        self.assertIn("Structural schemas", catalog)

    def test_derived_manifest_fields_are_not_stored(self) -> None:
        for instance_name in (
            "browser-policies/justthebrowser/manifest.json",
            "plannotator-assets.json",
        ):
            with self.subTest(instance=instance_name):
                instance = json.loads((CONFIGS / instance_name).read_text(encoding="utf-8"))
                upstream = instance["upstream"]
                self.assertNotIn("release_url", upstream)
                self.assertNotIn("source_base_url", upstream)
        plannotator = json.loads(
            (CONFIGS / "plannotator-assets.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("version", plannotator["upstream"])

        renovate = (REPO_ROOT / "renovate.json5").read_text(encoding="utf-8")
        browser_manager = renovate.split(
            '"depNameTemplate": "corbindavenport/just-the-browser"', 1
        )[1].split("    },", 1)[0]
        self.assertIn("currentValue", browser_manager)
        self.assertNotIn("autoReplaceStringTemplate", browser_manager)
        self.assertNotIn("release_url", browser_manager)
        self.assertNotIn("source_base_url", browser_manager)

    def test_manifest_source_urls_are_derived_from_the_authoritative_pins(self) -> None:
        browser = runpy.run_path(str(REPO_ROOT / "assets/sync-browser-policies.py"))
        browser_manifest = json.loads(
            (CONFIGS / "browser-policies/justthebrowser/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        browser_version = browser_manifest["upstream"]["version"]
        self.assertEqual(
            browser["expected_source_base_url"](browser_version),
            f"https://raw.githubusercontent.com/corbindavenport/just-the-browser/{browser_version}/",
        )

        plannotator_sync = runpy.run_path(
            str(REPO_ROOT / "assets/sync-plannotator-assets.py")
        )
        pinned_version = plannotator_sync["pinned_version"](REPO_ROOT)
        self.assertEqual(
            plannotator_sync["expected_source_base_url"](pinned_version),
            f"https://raw.githubusercontent.com/backnotprop/plannotator/{pinned_version}/",
        )

    def test_schema_directory_contains_only_cataloged_versioned_contracts(self) -> None:
        expected = {
            contract["schema"] for contract in (*CONTRACTS.values(), *YAML_CONTRACTS.values())
        } | RETAINED_SCHEMAS | set(STANDALONE_SCHEMAS)
        actual = {
            path.relative_to(CONFIGS).as_posix()
            for path in (CONFIGS / "schemas").glob("*.schema.json")
        }
        self.assertEqual(actual, expected)
        for path in actual:
            self.assertRegex(path, r"\.v\d+\.schema\.json$")

        retained_ids = {
            "schemas/automation-provenance.v1.schema.json": (
                "urn:dotfiles:schema:automation-provenance:v1",
                1,
            ),
            "schemas/automation-test-inventory.v1.schema.json": (
                "urn:dotfiles:schema:automation-test-inventory:v1",
                1,
            ),
            "schemas/automation-test-inventory.v2.schema.json": (
                "urn:dotfiles:schema:automation-test-inventory:v2",
                2,
            ),
        }
        for relative_path, (schema_id, version) in retained_ids.items():
            with self.subTest(schema=relative_path):
                retained = json.loads(
                    (CONFIGS / relative_path).read_text(encoding="utf-8")
                )
                self.assertEqual(retained["$id"], schema_id)
                self.assertEqual(retained["properties"]["schema_version"]["const"], version)


if __name__ == "__main__":
    unittest.main()
