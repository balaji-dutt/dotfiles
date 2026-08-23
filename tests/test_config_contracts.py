from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS = REPO_ROOT / "configs"
CONTRACTS = {
    "automation-provenance.json": (
        "schemas/automation-provenance.v1.schema.json",
        "assets/check-automation-provenance.py",
    ),
    "automation-test-inventory.json": (
        "schemas/automation-test-inventory.v1.schema.json",
        "assets/check-automation-test-inventory.py",
    ),
    "gitlab-pipeline-guard.json": (
        "schemas/gitlab-pipeline-guard.v1.schema.json",
        "assets/check-gitlab-pipeline.py",
    ),
    "test-suites.json": (
        "schemas/test-suites.v1.schema.json",
        "assets/run-tests.py",
    ),
}


class ConfigContractTests(unittest.TestCase):
    def test_instances_link_to_immutable_draft_2020_12_schemas(self) -> None:
        for instance_name, (schema_name, _) in CONTRACTS.items():
            with self.subTest(instance=instance_name):
                instance = json.loads((CONFIGS / instance_name).read_text(encoding="utf-8"))
                schema = json.loads((CONFIGS / schema_name).read_text(encoding="utf-8"))
                expected_ref = "./" + schema_name
                self.assertEqual(instance["$schema"], expected_ref)
                self.assertEqual(instance["schema_version"], 1)
                self.assertEqual(
                    schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
                )
                self.assertTrue(schema["$id"].endswith(":v1"))
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["$schema"]["const"], expected_ref)
                self.assertEqual(schema["properties"]["schema_version"]["const"], 1)

    def test_runtime_consumers_pin_the_same_schema_reference(self) -> None:
        for instance_name, (schema_name, consumer_name) in CONTRACTS.items():
            with self.subTest(instance=instance_name):
                consumer = (REPO_ROOT / consumer_name).read_text(encoding="utf-8")
                self.assertIn(f'SCHEMA_REF = "./{schema_name}"', consumer)

    def test_contract_catalog_lists_every_managed_instance_and_schema(self) -> None:
        catalog = (REPO_ROOT / "docs/tooling/config-contracts.md").read_text(
            encoding="utf-8"
        )
        for instance_name, (schema_name, consumer_name) in CONTRACTS.items():
            with self.subTest(instance=instance_name):
                self.assertIn(f"`configs/{instance_name}`", catalog)
                self.assertIn(f"`configs/{schema_name}`", catalog)
                self.assertIn(f"`{consumer_name}`", catalog)

    def test_schema_directory_contains_only_cataloged_versioned_contracts(self) -> None:
        expected = {schema for schema, _ in CONTRACTS.values()}
        actual = {
            path.relative_to(CONFIGS).as_posix()
            for path in (CONFIGS / "schemas").glob("*.schema.json")
        }
        self.assertEqual(actual, expected)
        for path in actual:
            self.assertRegex(path, r"\.v\d+\.schema\.json$")


if __name__ == "__main__":
    unittest.main()
