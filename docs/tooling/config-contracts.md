<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Configuration Contracts

Repository policy files that have custom structure use immutable, versioned
JSON Schema contracts. The schema is the structural and editor-facing contract;
the owning command remains responsible for semantic checks such as sorted
values, tracked paths, cross-file references, and runtime capability rules.

## Managed contracts

| Instance | Schema | Semantic consumer |
| :--- | :--- | :--- |
| `configs/automation-provenance.json` | `configs/schemas/automation-provenance.v1.schema.json` | `assets/check-automation-provenance.py` |
| `configs/automation-test-inventory.json` | `configs/schemas/automation-test-inventory.v2.schema.json` | `assets/check-automation-test-inventory.py` |
| `configs/gitlab-pipeline-guard.json` | `configs/schemas/gitlab-pipeline-guard.v1.schema.json` | `assets/check-gitlab-pipeline.py` |
| `configs/test-suites.json` | `configs/schemas/test-suites.v1.schema.json` | `assets/run-tests.py` |

Each instance has a repository-relative `$schema` link and an integer
`schema_version`. Its consumer checks both before processing the rest of the
file. The schemas use JSON Schema Draft 2020-12 and reject undeclared fields.

## Versioning

Published `configs/schemas/*.vN.schema.json` files are immutable. A breaking
shape change gets a new schema file, a new `$id` suffix, and a matching
`schema_version`. Update the instance, consumer, tests, and this catalog in the
same change. Retain published schemas as historical contracts; the automation
inventory's v1 schema remains immutable while v2 adds behavioral requirements.

Compatible semantic tightening can remain within the consumer when the JSON
shape is unchanged. Document the new rule and add a consumer test rather than
silently broadening the schema.

## Validation

The repository does not require a JSON Schema runtime package. Standard-library
consumers fail closed on the fields they use, while contract tests verify schema
links and the common immutable-version rules:

```sh
python3 -m unittest tests.test_config_contracts
./assets/run-tests.sh provenance
```

Editors and external validators may follow each instance's `$schema` link for
the full structural contract. Older custom manifests are migrated separately;
`dots-4jy.10.1.4.1` tracks that work.
