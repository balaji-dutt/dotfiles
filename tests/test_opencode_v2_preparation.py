from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.support.chezmoi_render import CHEZMOI, PLATFORMS, render_template


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "assets/check-opencode-v2-preparation.py"
SPEC = importlib.util.spec_from_file_location("opencode_v2_preparation_checker", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKER
with mock.patch.object(sys, "dont_write_bytecode", True):
    SPEC.loader.exec_module(CHECKER)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def write(root: Path, name: str, value: bytes) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value)


def tree_snapshot(root: Path) -> dict:
    result = {}
    for target in root.rglob("*"):
        name = target.relative_to(root).as_posix()
        if target.is_symlink():
            result[name] = ("link", os.readlink(target))
        elif target.is_file():
            result[name] = ("file", sha(target.read_bytes()))
    return result


class PreparationFixture:
    def __init__(self, root: Path):
        self.root = root
        self.environment = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        }
        self.git("init", "-q")
        self.payload = {}
        self.canonical = {}
        self.selection = []
        bundle_names = {
            "prompt-engineer": ["SKILL.md", "references/authoring-patterns.md",
                                "references/improvement-checklist.md", "references/model-specific.md"],
            "prompt-optimizer": ["SKILL.md", "references/domain-theories.md", "references/ears-syntax.md"],
            "skill-creator": ["SKILL.md", "references/skill-structure.md", "references/testing-guide.md"],
            "skill-reviewer": ["SKILL.md", "references/review-checklist.md"],
        }
        for name in ("agent-engineer", "special-builder"):
            source = f"agents/{name}.yaml"
            self.canonical[source] = f"role: synthetic-{name}\n".encode()
            self.add_row(f"agents/{name}.md", source, f"synthetic emitted {name}\n".encode(),
                         sha(self.canonical[source]))
        for bundle, names in bundle_names.items():
            for name in names:
                source = f"skills/{bundle}/{name}"
                content = f"synthetic retained {bundle}/{name}\n".encode()
                self.add_row(source, source, content, sha(content))
        self.owner = {
            "inputs": [{"path": row["source"], "digest": row["sourceDigest"]} for row in self.selection],
            "projections": [{"path": "projections/synthetic.json", "digest": "3" * 64}],
            "outputs": [{"path": row["ownerManifestPath"], "source": row["source"],
                         "sourceDigest": row["sourceDigest"], "digest": row["digest"]}
                        for row in self.selection],
            "overallReady": False,
            "dispositions": {"operational": {
                "agents": [{"id": f"synthetic-{number}", "status": "blocked", "reasonIds": ["unqualified"]}
                           for number in range(15)],
                "aggregate": {"status": "withheld-aggregate", "reasonIds": ["aggregate-withheld"]},
            }},
        }
        owner_bytes = json_bytes(self.owner)
        self.inventory = {
            "references": {"owner": {"commit": "a" * 40},
                           "nativeReference": {"version": "v2.2.3", "commit": "b" * 40}},
            "producerEvidence": {"manifest": {"path": "generated/metadata/preparation-manifest.json",
                                              "digest": sha(owner_bytes)}, "actualProducingBuild": None},
            "selection": {"files": self.selection, "withheldDependencies": [
                {"id": "plugin-developer", "status": "upstream-blocked"},
                {"id": "prompt-evaluator", "status": "upstream-blocked"},
            ]},
            "historicalConsumerSnapshot": {"sha256ByPath": {}},
            "additionalCurrentSha256ByPath": {}, "checkoutByteVariance": [],
        }
        self.host = "private_dot_config/opencode/opencode.jsonc"
        self.contract = {"surfaces": [{"id": "host", "configuration": self.host}]}
        baseline_bytes = {self.host: b'{"agent":{"plan":{"model":"openai/original"}}}\n',
                          CHECKER.LEDGER: b"# Synthetic historical ledger\n",
                          ".gitattributes": b"* text=auto\n"}
        baseline_bytes.update({name: b'{"entries":[{"id":"existing"}]}\n' if name.endswith(".json")
                               else b"existing-helper.py\n" for name in CHECKER.REGISTRIES})
        frozen = {CHECKER.INVENTORY: json_bytes(self.inventory), CHECKER.CONTRACT: json_bytes(self.contract)}
        self.context = CHECKER.TrustedContext(
            self.inventory, self.contract, frozen,
            {name: ("100644", value) for name, value in baseline_bytes.items()},
        )
        for name, value in {**baseline_bytes, **frozen, **self.payload}.items():
            write(root, name, value)
        write(root, CHECKER.OWNER_MANIFEST, owner_bytes)
        for name, value in self.canonical.items():
            write(root, f"{CHECKER.EVIDENCE}/{name}", value)
        self.descriptor = CHECKER.expected_descriptor(self.context)
        self.refresh_metadata()
        self.git("add", ".")

    def git(self, *arguments: str) -> bytes:
        result = subprocess.run(["git", "-C", str(self.root), *arguments], env=self.environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        if result.returncode:
            raise AssertionError(result.stderr.decode())
        return result.stdout

    def add_row(self, emitted: str, source: str, value: bytes, source_digest: str) -> None:
        self.selection.append({"ownerManifestPath": f"reference-v2.2.3/{emitted}", "source": source,
                               "sourceDigest": source_digest, "digest": sha(value)})
        self.payload[f"{CHECKER.PAYLOAD}/{emitted}"] = value

    def refresh_metadata(self) -> None:
        raw = json_bytes(self.descriptor)
        self.subset = CHECKER.expected_subset(self.context, self.owner, raw)
        write(self.root, CHECKER.DESCRIPTOR, raw)
        write(self.root, CHECKER.SUBSET, json_bytes(self.subset))

    def check(self) -> dict:
        with mock.patch.dict(os.environ, self.environment, clear=True):
            return CHECKER.check_repository(self.root, self.context)

    def refresh_context(self) -> None:
        value = json_bytes(self.inventory)
        self.context.frozen[CHECKER.INVENTORY] = value
        write(self.root, CHECKER.INVENTORY, value)
        self.descriptor = CHECKER.expected_descriptor(self.context)
        self.refresh_metadata()


@unittest.skipUnless(shutil.which("git"), "git is required")
@unittest.skipUnless(os.name == "posix", "POSIX checkout semantics are required")
class OpenCodeV2PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="opencode-v2-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.fixture = PreparationFixture(self.root)

    def test_default_cli_accepts_the_real_inactive_subset_without_owner_checkout(self):
        result = subprocess.run([sys.executable, "-B", str(CHECKER_PATH), "--root", str(REPO_ROOT)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "PASS")
        self.assertEqual((value["payloadFiles"], value["evidenceFiles"]), (14, 3))
        self.assertIs(value["operationalReady"], False)
        self.assertEqual(value["aggregate"], "withheld")

    def test_non_posix_checker_refuses_before_root_or_content_access(self):
        with mock.patch.object(CHECKER, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(CHECKER, "checked_root") as root_check, \
                mock.patch.object(CHECKER, "load_context") as context_load, \
                mock.patch.object(CHECKER, "read_bytes") as reader, \
                mock.patch.object(CHECKER, "git") as query:
            with self.assertRaisesRegex(CHECKER.CheckFailure, "requires a POSIX checkout"):
                CHECKER.check_repository(self.root)
        for boundary in (root_check, context_load, reader, query):
            boundary.assert_not_called()

    def test_selected_closure_has_independent_expected_counts_and_source_proof(self):
        self.assertEqual(self.fixture.check()["status"], "PASS")
        rows = self.fixture.subset["files"]
        self.assertEqual(len(rows), 14)
        self.assertEqual(self.fixture.subset["selectedBundleFiles"], {
            "prompt-engineer": 4, "prompt-optimizer": 3, "skill-creator": 3, "skill-reviewer": 2})
        self.assertEqual([row["source"] for row in rows[:2]],
                         ["agents/agent-engineer.yaml", "agents/special-builder.yaml"])
        self.assertNotEqual(rows[0]["sourceDigest"], rows[0]["digest"])
        self.assertTrue(all(row["sourceDigest"] == row["digest"] for row in rows[2:]))
        self.assertEqual(self.fixture.subset["orderedConsumerInputs"][-1]["path"], CHECKER.DESCRIPTOR)
        self.assertEqual(self.fixture.descriptor["overlayInputs"], [
            {"surface": "host", "path": self.fixture.host,
             "digest": sha(self.fixture.context.baseline[self.fixture.host][1])}])

    def test_output_and_mutable_manifest_cannot_self_certify_tampering(self):
        row = self.fixture.subset["files"][0]
        write(self.root, row["path"], b"edited output\n")
        row["digest"] = sha(b"edited output\n")
        write(self.root, CHECKER.SUBSET, json_bytes(self.fixture.subset))
        with self.assertRaisesRegex(CHECKER.CheckFailure, "selected output mismatch"):
            self.fixture.check()

    def test_missing_extra_stale_and_incomplete_bundle_are_rejected(self):
        target = next(name for name in self.fixture.payload if "skill-reviewer/SKILL.md" in name)
        original = (self.root / target).read_bytes()
        (self.root / target).unlink()
        with self.assertRaisesRegex(CHECKER.CheckFailure, "missing, extra or stale"):
            self.fixture.check()
        write(self.root, target, original)
        write(self.root, f"{CHECKER.PAYLOAD}/skills/withheld/SKILL.md", b"not selected\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "missing, extra or stale"):
            self.fixture.check()

    def test_canonical_evidence_and_frozen_authority_cannot_be_replaced(self):
        for name in (CHECKER.INVENTORY, f"{CHECKER.EVIDENCE}/agents/agent-engineer.yaml", CHECKER.OWNER_MANIFEST):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                write(self.root, name, original + b"\n")
                diagnostic = ("frozen authority changed" if name == CHECKER.INVENTORY
                              else "immutable owner evidence mismatch")
                with self.assertRaisesRegex(CHECKER.CheckFailure, diagnostic):
                    self.fixture.check()
                write(self.root, name, original)

    def test_provenance_order_unknown_build_and_readiness_are_strict(self):
        original = copy.deepcopy(self.fixture.subset)
        variants = []
        value = copy.deepcopy(original)
        value["orderedConsumerInputs"].reverse()
        variants.append(value)
        for key, replacement in (("actualProducingBuild", "invented-build"), ("operationalReady", True),
                                 ("overallReady", 0), ("activationAllowed", True),
                                 ("orderedOwnerProjectionsDigest", "0" * 64)):
            value = copy.deepcopy(original)
            value[key] = replacement
            variants.append(value)
        value = copy.deepcopy(original)
        value["operationalRows"][0]["status"] = "ready"
        variants.append(value)
        value = copy.deepcopy(original)
        value["operationalAggregate"] = {"status": "ready"}
        variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                write(self.root, CHECKER.SUBSET, json_bytes(value))
                with self.assertRaisesRegex(CHECKER.CheckFailure, "subset provenance"):
                    self.fixture.check()

    def test_descriptor_cannot_enable_registration_or_hide_an_applied_overlay(self):
        for key, replacement in (("registrationAllowed", True), ("overlaysAppliedToPayload", True),
                                 ("operationalReady", 0), ("actualProducingBuild", "guessed")):
            with self.subTest(key=key):
                value = copy.deepcopy(self.fixture.descriptor)
                value[key] = replacement
                write(self.root, CHECKER.DESCRIPTOR, json_bytes(value))
                with self.assertRaisesRegex(CHECKER.CheckFailure, "consumer descriptor mismatch"):
                    self.fixture.check()

    def test_strict_jsonc_duplicates_nonfinite_and_root_types(self):
        self.assertEqual(CHECKER.decode_document(b'{/* fixture */"url":"https://example/a//b",}'),
                         {"url": "https://example/a//b"})
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{"x":1', b'{/*'):
            with self.subTest(value=value), self.assertRaises((CHECKER.CheckFailure, ValueError)):
                CHECKER.decode_document(value)

    def test_windows_path_alias_and_escape_matrix(self):
        names = ("../outside", "/absolute", "a/../b", "a\\b", "C:/outside", "//server/share",
                 "a:stream", "a//b", "a/.", "a/CON", "a/nul.txt", "a/trailing.", "a/space ")
        for name in names:
            with self.subTest(name=name), self.assertRaises(CHECKER.CheckFailure):
                CHECKER.relative_name(name)
        self.assertEqual(CHECKER.relative_name("agents/valid-name.md"), "agents/valid-name.md")
        for name in (".ENV", "a/.SSH/key", "a/.git/config", "a/NODE_MODULES/x", "a/auth.json"):
            self.assertFalse(CHECKER.public_name(name), name)

    def test_protected_bytes_path_sets_modes_and_registry_history(self):
        original = (self.root / self.fixture.host).read_bytes()
        write(self.root, self.fixture.host, b"changed policy\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes"):
            self.fixture.check()
        write(self.root, self.fixture.host, original)
        write(self.root, "private_dot_config/opencode/agents/new.md", b"new registration\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "path set"):
            self.fixture.check()
        (self.root / "private_dot_config/opencode/agents/new.md").unlink()
        write(self.root, CHECKER.REGISTRIES[0], b'{"entries":[]}\n')
        with self.assertRaisesRegex(CHECKER.CheckFailure, "existing registration"):
            self.fixture.check()

    def test_protected_executable_mode_drift_is_rejected(self):
        target = self.root / self.fixture.host
        original_mode = stat.S_IMODE(target.stat().st_mode)
        try:
            target.chmod(original_mode | 0o111)
            with self.assertRaisesRegex(CHECKER.CheckFailure, "protected mode changed"):
                self.fixture.check()
        finally:
            target.chmod(original_mode)
        self.assertEqual(self.fixture.check()["status"], "PASS")

    def test_recorded_crlf_variance_requires_exact_bytes_and_real_attributes(self):
        name = "private_dot_config/opencode/fixture.ps1"
        committed = b"first\nsecond\n"
        working = b"first\r\nsecond\r\n"
        attributes = f"* text=auto\n{name} text eol=crlf\n".encode()
        self.fixture.context.baseline[name] = ("100644", committed)
        self.fixture.context.baseline[".gitattributes"] = ("100644", attributes)
        write(self.root, ".gitattributes", attributes)
        write(self.root, name, working)
        record = {"path": name, "committedDigest": sha(committed), "workingDigest": sha(working)}
        self.fixture.inventory["checkoutByteVariance"] = [record]
        self.fixture.refresh_context()
        self.assertEqual(self.fixture.check()["status"], "PASS")

        for field in ("committedDigest", "workingDigest"):
            with self.subTest(digest=field):
                original_digest = record[field]
                record[field] = "0" * 64
                self.fixture.refresh_context()
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
                    self.fixture.check()
                record[field] = original_digest

        changed = b"first\r\nchanged\r\n"
        write(self.root, name, changed)
        record["workingDigest"] = sha(changed)
        self.fixture.refresh_context()
        with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
            self.fixture.check()
        write(self.root, name, working)
        record["workingDigest"] = sha(working)

        for rules in (b"* text=auto\n", f"{name} text eol=lf\n".encode(),
                      f"{name} -text eol=crlf\n".encode()):
            with self.subTest(attributes=rules):
                self.fixture.context.baseline[".gitattributes"] = ("100644", rules)
                write(self.root, ".gitattributes", rules)
                self.fixture.refresh_context()
                with self.assertRaisesRegex(CHECKER.CheckFailure, "unverified checkout EOL"):
                    self.fixture.check()

    def test_legacy_link_payload_is_preserved_without_reading_its_targets(self):
        with tempfile.TemporaryDirectory(prefix="opencode-v2-legacy-target-") as temporary:
            outside = Path(temporary).resolve() / "sentinel"
            alternative = outside.with_name("alternative")
            sentinel = b"legacy target must not be opened or hashed\n"
            outside.write_bytes(sentinel)
            alternative.write_bytes(sentinel)
            name = "private_dot_config/opencode/legacy-link"
            link = self.root / name
            target_bytes = str(outside).encode()
            link.symlink_to(outside)
            self.fixture.context.baseline[name] = ("120000", target_bytes)
            self.fixture.refresh_context()
            forbidden = {outside.resolve(), alternative.resolve()}
            original_io, original_builtin, original_digest = io.open, builtins.open, CHECKER.digest

            def guard(original):
                def guarded(file, *arguments, **keywords):
                    if not isinstance(file, int) and Path(file).resolve() in forbidden:
                        raise AssertionError("legacy link target was opened")
                    return original(file, *arguments, **keywords)
                return guarded

            def guarded_digest(value):
                self.assertNotEqual(value, sentinel)
                return original_digest(value)

            with mock.patch("io.open", side_effect=guard(original_io)), \
                    mock.patch("builtins.open", side_effect=guard(original_builtin)), \
                    mock.patch.object(CHECKER, "digest", side_effect=guarded_digest):
                before = tree_snapshot(self.root)
                index_before = (self.root / ".git/index").read_bytes()
                self.assertEqual(self.fixture.check()["status"], "PASS")
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

                link.unlink()
                write(self.root, name, target_bytes)
                before = tree_snapshot(self.root)
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected link type changed"):
                    self.fixture.check()
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

                link.unlink()
                link.symlink_to(alternative)
                before = tree_snapshot(self.root)
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
                    self.fixture.check()
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
            self.assertEqual(outside.read_bytes(), sentinel)
            self.assertEqual(alternative.read_bytes(), sentinel)

    def test_registration_additions_and_ledger_append_preserve_history(self):
        for name in CHECKER.REGISTRIES:
            write(self.root, name, b'{"entries":[{"id":"existing"},{"id":"new"}]}\n'
                  if name.endswith(".json") else b"existing-helper.py\nnew-helper.py\n")
        write(self.root, CHECKER.LEDGER, b"# Synthetic historical ledger\n\nDV2-020 result\n")
        self.assertEqual(self.fixture.check()["status"], "PASS")
        write(self.root, CHECKER.LEDGER, b"rewritten history\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "historical ledger"):
            self.fixture.check()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_symlink_targets_are_not_read_or_hashed_and_checker_does_not_write(self):
        with tempfile.TemporaryDirectory(prefix="opencode-v2-outside-") as temporary:
            outside = Path(temporary).resolve() / "sentinel"
            outside.write_bytes(b"outside data must not be read by the checker\n")
            target = self.root / next(iter(self.fixture.payload))
            target.unlink()
            target.symlink_to(outside)
            before = tree_snapshot(self.root)
            index_before = (self.root / ".git/index").read_bytes()
            forbidden = outside.resolve()
            original_io, original_builtin, original_digest = io.open, builtins.open, CHECKER.digest

            def guard(original):
                def guarded(file, *arguments, **keywords):
                    if not isinstance(file, int) and Path(file).resolve() == forbidden:
                        raise AssertionError("forbidden target was opened")
                    return original(file, *arguments, **keywords)
                return guarded

            def guarded_digest(value):
                self.assertNotEqual(value, b"outside data must not be read by the checker\n")
                return original_digest(value)

            with mock.patch("io.open", side_effect=guard(original_io)), \
                    mock.patch("builtins.open", side_effect=guard(original_builtin)), \
                    mock.patch.object(CHECKER, "digest", side_effect=guarded_digest):
                with self.assertRaisesRegex(CHECKER.CheckFailure, "symlink"):
                    self.fixture.check()
            self.assertEqual(tree_snapshot(self.root), before)
            self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
            self.assertEqual(outside.read_bytes(), b"outside data must not be read by the checker\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_root_ancestor_dangling_cyclic_links_and_case_aliases_are_rejected(self):
        link = self.root / "root-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(CHECKER.CheckFailure, "symlink"):
            CHECKER.checked_root(link)
        link.unlink()
        ancestor = self.root / "ancestor"
        ancestor.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(CHECKER.CheckFailure, "symlink"):
            CHECKER.checked_root(ancestor / "nested")
        ancestor.unlink()
        for destination in (self.root / "missing", self.root / "cycle"):
            link = self.root / "cycle"
            link.symlink_to(destination)
            with self.assertRaisesRegex(CHECKER.CheckFailure, "symlink"):
                CHECKER.safe_path(self.root, "cycle/child")
            link.unlink()
        for directory in (".OpenCode", ".ClAuDe", ".AgEnTs", "DoT_ClAuDe"):
            target = self.root / directory / "nested"
            target.mkdir(parents=True, exist_ok=True)
            with self.subTest(directory=directory), \
                    self.assertRaisesRegex(CHECKER.CheckFailure, "discovery directory"):
                CHECKER.checked_root(target)
        for directory in (".CoNfIg", "PrIvAtE_DoT_CoNfIg"):
            target = self.root / directory / "OpEnCoDe" / "nested"
            target.mkdir(parents=True, exist_ok=True)
            with self.subTest(directory=directory), \
                    self.assertRaisesRegex(CHECKER.CheckFailure, "active configuration directory"):
                CHECKER.checked_root(target)
        with mock.patch.object(Path, "iterdir", return_value=[Path("/fixture/A"), Path("/fixture/a")]):
            with mock.patch.object(Path, "is_dir", lambda value: value == Path("/fixture")), \
                    mock.patch.object(Path, "is_file", return_value=True), \
                    mock.patch.object(Path, "is_symlink", return_value=False), \
                    mock.patch.object(CHECKER, "safe_path", return_value=Path("/fixture")):
                with self.assertRaisesRegex(CHECKER.CheckFailure, "case-alias"):
                    CHECKER.tree_files(self.root, "fixture")

    def test_missing_trusted_git_baseline_fails_without_fetch_or_fallback(self):
        with mock.patch.object(CHECKER, "git", side_effect=CHECKER.CheckFailure("missing baseline")) as query:
            with self.assertRaisesRegex(CHECKER.CheckFailure, "missing baseline"):
                CHECKER.load_context(self.root)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.args[1], "show")
        self.assertTrue(query.call_args.args[2].startswith(CHECKER.BASELINE + ":"))

    def test_profile_composition_order_workspace_scope_rebase_and_replacements(self):
        global_input = ({"agent": {"plan": {"model": "openai/base", "prompt": "{file:./plan.md}"}},
                         "list": [1, 2], "nested": {"a": 1}, "plugin": ["global"]}, "/global")
        defaults = ({"nested": {"b": 2}}, "/defaults")
        profiles = [("first", {"list": [3], "nested": {"a": 4}}, "/first"),
                    ("second", {"agent": {"plan": {"effort": "high"}}}, "/second"),
                    ("first", {"list": [999]}, "/ignored")]
        workspace = ({"agent": {"plan": {"prompt": "{file:../prompt.md}"}},
                      "plugin": ["must-not-import"], "permission": "allow"}, "/repo/.opencode")
        result = CHECKER.compose_contract(global_input, defaults, profiles, workspace, {})
        self.assertEqual(result["profiles"], ["first", "second"])
        self.assertEqual(result["config"], {
            "agent": {"plan": {"model": "openai/base", "prompt": "{file:/repo/prompt.md}", "effort": "high"}},
            "list": [3], "nested": {"a": 4, "b": 2}, "plugin": ["global"]})
        self.assertEqual(CHECKER.rebase_references("prefix {file:./x}", "/base"), "prefix {file:./x}")
        self.assertEqual(CHECKER.merge_objects({"a": {"x": 1}}, {"a": [2]}), {"a": [2]})

    def test_default_alias_provider_transforms_native_claude_and_windows_failure_contract(self):
        self.assertEqual(CHECKER.normalize_profiles(["chatgpt", "defaults", "api-fallback", "api-fallback"]),
                         ["defaults", "api-fallback"])
        defaults = ({}, "/defaults")
        normal = CHECKER.compose_contract(None, defaults, [("chatgpt", {}, "/unused")], ({}, "/repo"), {})
        self.assertEqual(normal, {"route": "normal", "configDir": None, "profiles": ["defaults"]})
        fallback = {"moonshot/kimi-k3": "openrouter/moonshotai/kimi-k3",
                    "moonshot/kimi-k2.7-code": "openrouter/moonshotai/kimi-k3",
                    "moonshot/kimi-k2.6": "openrouter/moonshotai/kimi-k2.6",
                    "google/gemini-3.6-flash": "openrouter/z-ai/glm-5.2:exacto"}
        models = ["anthropic/example", "claude-agent/native", "openai/keep", "opencode-go/keep", *fallback]
        base = ({"agent": {str(index): {"model": model} for index, model in enumerate(models)}}, "/base")
        composed = CHECKER.compose_contract(base, defaults,
                                           [("anthropic-api", {}, "/a"), ("api-fallback", {}, "/b")],
                                           ({}, "/repo"), fallback)
        actual = [row["model"] for row in composed["config"]["agent"].values()]
        self.assertEqual(actual, ["anthropic-api/example", "claude-agent/native", "openai/keep",
                                  "opencode-go/keep", *fallback.values()])
        self.assertEqual(CHECKER.runtime_selection(["chatgpt"], "windows", False), "normal")
        self.assertEqual(CHECKER.runtime_selection(["api-fallback"], "macos", False), "static-fallback")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "runtime-required"):
            CHECKER.runtime_selection(["api-fallback"], "windows", False)
        with self.assertRaises(CHECKER.CheckFailure):
            CHECKER.normalize_profiles(["../escape"])

    def test_workspace_preference_plan_trial_and_explicit_launcher_auto(self):
        names = {"opencode.json", "opencode.jsonc"}
        self.assertEqual(CHECKER.preferred_workspace(names, composer=True), "opencode.jsonc")
        self.assertEqual(CHECKER.preferred_workspace(names, composer=False), "opencode.json")
        self.assertTrue(CHECKER.plan_trial_supported(["chatgpt"]))
        self.assertFalse(CHECKER.plan_trial_supported(["api-fallback"]))
        self.assertEqual(CHECKER.launcher_arguments([], kind="opencode", approve_auto=False), [])
        self.assertEqual(CHECKER.launcher_arguments([], kind="claude", approve_auto=True), [])
        self.assertEqual(CHECKER.launcher_arguments([], kind="opencode", approve_auto=True), ["--auto"])
        self.assertEqual(CHECKER.launcher_arguments(["--auto"], kind="opencode", approve_auto=True), ["--auto"])

    def test_raw_host_and_fallback_mirror_lifecycle_ownership_and_isolation(self):
        selected = {"agents/a.md", "skills/a/SKILL.md", "opencode.jsonc", "profiles/x/package.json",
                    "node_modules/x.js", "configs/opencode-v2/prepared/owner/agents/a.md"}
        expected_write = {"agents/a.md", "skills/a/SKILL.md", "opencode.jsonc"}
        result = CHECKER.materialization_plan(selected, {"agents/mirror.md"},
                                             {"agents/removed.md", "generated.md"}, {"local.md"},
                                             {"generated.md"}, {"agents/a.md"})
        self.assertEqual(result, {"route": "raw-host", "write": expected_write,
                                  "remove": {"agents/removed.md"}, "preserve": {"local.md", "generated.md"},
                                  "backup": {"agents/a.md"}})
        fallback = CHECKER.materialization_plan(None, selected, set(), set(), set(), set())
        self.assertEqual(fallback["route"], "fallback-mirror")
        self.assertEqual(fallback["write"], expected_write)
        self.assertFalse(CHECKER.managed_path("opencode.jsonc/child"))


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class OpenCodeV2IsolationRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="opencode-v2-render-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root / "home"),
                            "XDG_CONFIG_HOME": str(self.root / "config"),
                            "XDG_CACHE_HOME": str(self.root / "cache"), "CHEZMOI_NO_TTY": "1"}
        for name in ("home", "config", "cache"):
            (self.root / name).mkdir()

    def query(self, patterns: str) -> tuple[set[str], set[str]]:
        with tempfile.TemporaryDirectory(dir=self.root, prefix="ignore-") as temporary:
            owned = Path(temporary)
            source, destination = owned / "source", owned / "destination"
            source.mkdir()
            destination.mkdir()
            configuration = owned / "chezmoi.toml"
            configuration.write_text("", encoding="utf-8")
            write(source, ".chezmoiignore", patterns.encode())
            for name in ("configs/opencode-v2/source/owner-evidence/agents/agent-engineer.yaml",
                         "configs/opencode-v2/prepared/owner/agents/agent-engineer.md",
                         "configs/opencode-v2/prepared/owner/skills/prompt-engineer/SKILL.md",
                         "configs/opencode-v2/prepared/metadata/subset-manifest.json",
                         "private_Documents/development/container-dotfiles/dotfiles/configs/opencode-v2/prepared/x.md",
                         "dot_config/opencode/agents/active.md"):
                write(source, name, b"synthetic content\n")
            common = [CHEZMOI, "--config", str(configuration), "--source", str(source),
                      "--destination", str(destination), "--cache", str(owned / "cache"),
                      "--persistent-state", str(owned / "state.db"), "--refresh-externals=never",
                      "--no-tty", "--no-pager"]
            outputs = []
            for arguments in (["ignored", "--nul-path-separator"],
                              ["managed", "--include", "files", "--path-style", "relative", "--nul-path-separator"]):
                result = subprocess.run([*common, *arguments], env=self.environment, cwd=source,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                outputs.append({name.decode().replace("\\", "/") for name in result.stdout.split(b"\0") if name})
            return outputs[0], outputs[1]

    def test_real_rendered_matrix_keeps_evidence_payload_and_container_mirror_unmanaged(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                patterns = render_template(".chezmoiignore", platform, environment=self.environment)
                ignored, managed = self.query(patterns)
                self.assertTrue(any(name == "configs" or name.startswith("configs/") for name in ignored))
                self.assertFalse(any(name.startswith("configs/") for name in managed), managed)
                self.assertFalse(any("container-dotfiles/dotfiles/configs/" in name for name in managed), managed)
                self.assertIn(".config/opencode/agents/active.md", managed)
                if platform == "windows":
                    self.assertGreater(patterns.rfind("\nconfigs/**"),
                                       patterns.rfind("!.config/opencode/prompts/**"))

    def test_actual_unignore_exposes_payload_in_either_order(self):
        exposure = "!configs/\n!configs/**\n"
        for platform in PLATFORMS:
            patterns = render_template(".chezmoiignore", platform, environment=self.environment)
            for variant in (exposure + patterns, patterns + "\n" + exposure):
                with self.subTest(platform=platform, prefix=variant.startswith(exposure)):
                    _, managed = self.query(variant)
                    self.assertIn("configs/opencode-v2/prepared/owner/agents/agent-engineer.md", managed)

    def test_omitting_config_exclusions_exposes_prepared_files_on_posix(self):
        for platform in ("linux", "macos", "wsl2"):
            patterns = render_template(".chezmoiignore", platform, environment=self.environment)
            variant = "\n".join(line for line in patterns.splitlines() if line not in {"configs/", "configs/**"})
            with self.subTest(platform=platform):
                _, managed = self.query(variant)
                self.assertIn("configs/opencode-v2/prepared/owner/agents/agent-engineer.md", managed)


if __name__ == "__main__":
    unittest.main()
