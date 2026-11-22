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


AMENDMENT_PATHS = (
    "assets/check-opencode-v2-preparation.py", "tests/test_opencode_v2_preparation.py",
    "configs/automation-test-inventory.json", CHECKER.LEDGER,
)
PROJECTION_SUPPORT = {
    ".chezmoiignore", CHECKER.MIRRORS, "docs/devcontainers.md", "dot_claude/settings-base.json",
    "tests/__init__.py", "tests/support/__init__.py", "tests/support/chezmoi_render.py",
}


def projection_git(root: Path, *arguments: str, input_bytes: bytes | None = None,
                   check: bool = True) -> subprocess.CompletedProcess:
    environment = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Projection fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Projection fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    result = subprocess.run(
        ["git", "--no-optional-locks", "--no-replace-objects", "-C", str(root),
         "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false", *arguments],
        input=input_bytes, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=120, check=False,
    )
    if check and result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result


def source_git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    if arguments[0] not in {"ls-tree", "cat-file", "merge-base", "diff", "rev-parse", "for-each-ref"}:
        raise AssertionError("projection source operation is not read-only")
    return projection_git(root, *arguments, input_bytes=input_bytes).stdout


def projection_public_path(name: str, mode: str) -> bool:
    if not CHECKER.public_name(name):
        return False
    if name.startswith(CHECKER.AREA + "/") or name in {*AMENDMENT_PATHS, *PROJECTION_SUPPORT}:
        return True
    if not CHECKER.active_loading_path(name):
        return False
    basename = Path(name).name
    return (mode == "120000" or basename.startswith("symlink_")
            or basename in {"opencode.json", "opencode.jsonc", "AGENTS.md", "CLAUDE.md"})


def projection_public_view(source: Path, revision: str) -> dict:
    selected = []
    for row in source_git(source, "ls-tree", "-rz", revision).split(b"\0"):
        if row:
            header, raw_name = row.split(b"\t", 1)
            mode, kind, identifier = header.decode().split()
            name = raw_name.decode()
            basename = Path(name).name
            if (kind == "blob" and basename.casefold() in {"opencode.json.tmpl", "opencode.jsonc.tmpl"}
                    and projection_public_path(name[:-5], mode)):
                raise AssertionError("unverified loader template in public projection")
            if (kind == "blob" and basename.casefold() in {"opencode.json", "opencode.jsonc"}
                    and basename != basename.casefold() and CHECKER.active_loading_path(name)):
                raise AssertionError("unverified loader case alias in public projection")
            if kind == "blob" and projection_public_path(name, mode):
                CHECKER.relative_name(name)
                selected.append((name, mode, identifier))
    if not selected:
        return {}
    output = source_git(source, "cat-file", "--batch",
                        input_bytes="".join(identifier + "\n" for _, _, identifier in selected).encode())
    offset, result = 0, {}
    for name, mode, identifier in selected:
        boundary = output.index(b"\n", offset)
        actual, kind, length = output[offset:boundary].decode().split()
        size = int(length)
        content = output[boundary + 1:boundary + 1 + size]
        expected = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if actual != identifier or kind != "blob" or expected != identifier:
            raise AssertionError("public projection blob identity mismatch")
        result[name] = (mode, content)
        offset = boundary + size + 2
    if offset != len(output):
        raise AssertionError("public projection blob stream has trailing bytes")
    return result


def projection_commit(root: Path, rows: dict, parent: str | None = None) -> str:
    projection_git(root, "read-tree", "--empty")
    entries = []
    for name, (mode, content) in sorted(rows.items()):
        CHECKER.relative_name(name)
        identifier = projection_git(root, "hash-object", "--no-filters", "-w", "--stdin",
                                    input_bytes=content).stdout.decode().strip()
        entries.append(f"{mode} {identifier}\t{name}\0")
    if entries:
        projection_git(root, "update-index", "-z", "--index-info", input_bytes="".join(entries).encode())
    tree = projection_git(root, "write-tree").stdout.decode().strip()
    arguments = ["commit-tree", tree, "-m", "Public projection"]
    if parent:
        arguments.extend(["-p", parent])
    return projection_git(root, *arguments).stdout.decode().strip()


def projection_source_snapshot(source: Path) -> dict:
    index_name = source_git(source, "rev-parse", "--git-path", "index").decode().strip()
    index = source / index_name
    return {"index": sha(index.read_bytes()) if index.exists() else None,
            "feature": source_git(source, "rev-parse", "HEAD").decode().strip(),
            "sharedRefs": source_git(source, "for-each-ref", "--format=%(refname) %(objectname)"),
            "files": {name: sha(CHECKER.read_bytes(source, name)) for name in AMENDMENT_PATHS
                      if os.path.lexists(source / name)}}


def require_projection_source_stable(before: dict, after: dict) -> None:
    for key in ("index", "files", "feature"):
        if before[key] != after[key]:
            raise AssertionError("projection source changed: " + key)


def capture_projection_revisions(source: Path, feature_sha: str) -> tuple[str, str, str]:
    if source_git(source, "rev-parse", "HEAD").decode().strip() != feature_sha:
        raise AssertionError("feature moved before public projection")
    main_sha = source_git(source, "rev-parse", "refs/heads/main").decode().strip()
    common = source_git(source, "merge-base", main_sha, feature_sha).decode().strip()
    return common, main_sha, feature_sha


def projection_source_observation(source: Path, before: dict, captured_main: str) -> dict:
    observed_main = source_git(source, "rev-parse", "refs/heads/main").decode().strip()
    after = projection_source_snapshot(source)
    require_projection_source_stable(before, after)
    return {"capturedMain": captured_main, "observedMainAfter": observed_main,
            "mainMovementObserved": observed_main != captured_main,
            "sharedRefMovementObserved": before["sharedRefs"] != after["sharedRefs"]}


def timing_projection_fixture(source: Path) -> tuple[tuple[str, str, str], str]:
    source.mkdir()
    projection_git(source, "init", "-q")
    rows = {name: ("100644", (name + "\n").encode()) for name in AMENDMENT_PATHS}
    rows["docs/devcontainers.md"] = ("100644", b"original documentation\n")
    base = projection_commit(source, rows)
    main_rows = copy.deepcopy(rows)
    main_rows["docs/devcontainers.md"] = ("100644", b"captured main documentation\n")
    main = projection_commit(source, main_rows, base)
    feature_rows = copy.deepcopy(rows)
    feature_rows[AMENDMENT_PATHS[3]] = ("100644", b"feature ledger\n")
    feature = projection_commit(source, feature_rows, base)
    later_rows = copy.deepcopy(main_rows)
    later_rows["docs/devcontainers.md"] = ("100644", b"later main documentation\n")
    later = projection_commit(source, later_rows, main)
    projection_git(source, "update-ref", "refs/heads/main", main)
    projection_git(source, "update-ref", "refs/heads/feature", feature)
    projection_git(source, "update-ref", "refs/heads/other", base)
    projection_git(source, "symbolic-ref", "HEAD", "refs/heads/feature")
    projection_git(source, "read-tree", feature)
    projection_git(source, "checkout-index", "--all", "--force")
    return (base, main, feature), later


def build_public_projection(source: Path, revisions: tuple[str, str, str], delta: bytes,
                            target: Path) -> dict:
    before = projection_source_snapshot(source)
    target.mkdir(parents=True, exist_ok=True)
    if target.resolve() == source.resolve():
        raise AssertionError("projection target must be independently owned")
    projection_git(target, "init", "-q")
    rows = [projection_public_view(source, revision) for revision in revisions]
    base = projection_commit(target, rows[0])
    main = projection_commit(target, rows[1], base)
    feature = projection_commit(target, rows[2], base)
    merged = projection_git(target, "merge-tree", "--write-tree", main, feature, check=False)
    if merged.returncode:
        raise AssertionError("public projection content conflict")
    merged_tree = merged.stdout.splitlines()[0].decode()
    projection_git(target, "read-tree", merged_tree)
    projection_git(target, "checkout-index", "--all", "--force", "--index")
    headers = [line for line in delta.decode().splitlines() if line.startswith("diff --git ")]
    if delta and not headers:
        raise AssertionError("amendment delta has no approved file headers")
    for line in headers:
        parts = line.split()
        name = parts[2].removeprefix("a/") if len(parts) == 4 else ""
        if name not in AMENDMENT_PATHS or parts[2:] != ["a/" + name, "b/" + name]:
            raise AssertionError("amendment delta contains an unapproved path")
    changed_before = projection_git(target, "ls-files", "--stage").stdout
    if delta:
        for name in AMENDMENT_PATHS:
            if os.path.lexists(target / name):
                CHECKER.safe_path(target, name)
        applied = projection_git(target, "apply", "--3way", "--index", input_bytes=delta, check=False)
        if applied.returncode:
            raise AssertionError(
                f"public projection amendment application failed (exit {applied.returncode})\n"
                f"stdout:\n{applied.stdout.decode(errors='replace')}\n"
                f"stderr:\n{applied.stderr.decode(errors='replace')}"
            )
    changes = projection_git(target, "diff", "--cached", "--name-only", merged_tree, "-z").stdout
    if any(name.decode() not in AMENDMENT_PATHS for name in changes.split(b"\0") if name):
        raise AssertionError("projection delta changed an unapproved path")
    after = projection_source_snapshot(source)
    require_projection_source_stable(before, after)
    return {"publicMergeTree": merged_tree, "initialProjectedIndex": sha(changed_before),
            "projectedTree": projection_git(target, "write-tree").stdout.decode().strip(),
            "sharedRefMovementObserved": before["sharedRefs"] != after["sharedRefs"],
            "amendedFiles": {name: sha(CHECKER.read_bytes(target, name)) for name in AMENDMENT_PATHS
                             if os.path.lexists(target / name)}}


def rehearse_current_main(feature_sha: str) -> dict:
    source = REPO_ROOT
    before = projection_source_snapshot(source)
    common, main_sha, feature_sha = capture_projection_revisions(source, feature_sha)
    delta = source_git(source, "diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index", CHECKER.COMPLETED_PREPARATION,
                       "--", *AMENDMENT_PATHS)
    with tempfile.TemporaryDirectory(prefix="opencode-v2-public-projection-") as temporary:
        target = Path(temporary).resolve() / "projection"
        result = build_public_projection(source, (common, main_sha, feature_sha), delta, target)
        specification = importlib.util.spec_from_file_location(
            "opencode_v2_projected_checker", target / AMENDMENT_PATHS[0])
        candidate = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = candidate
        with mock.patch.object(sys, "dont_write_bytecode", True):
            specification.loader.exec_module(candidate)
        result["checkerAPI"] = candidate.check_repository(target, candidate.load_context(source))
        rendered = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "-v",
             "tests.test_opencode_v2_preparation.OpenCodeV2IsolationRenderTests"],
            cwd=target, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False,
        )
        if rendered.returncode or "Ran 3 tests" not in rendered.stderr.decode() or "skipped=" in rendered.stderr.decode():
            raise AssertionError("projected companion render gate failed: " + rendered.stderr.decode())
        result.update({"feature": feature_sha, "commonAncestor": common,
                        "companionRender": "3 executed PASS", "proof": "public projection, not full merged CI"})
    result.update(projection_source_observation(source, before, main_sha))
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
        baseline_bytes.update({name: b"{}\n" for name in CHECKER.MANDATORY_CONFIGS if name != self.host})
        baseline_bytes[CHECKER.MIRRORS] = b'{"shared":{"mirrors":[]}}\n'
        self.inventory["additionalCurrentSha256ByPath"][CHECKER.MIRRORS] = sha(baseline_bytes[CHECKER.MIRRORS])
        baseline_bytes.update({name: b'{"entries":[{"id":"existing"}]}\n' if name.endswith(".json")
                               else b"existing-helper.py\n" for name in CHECKER.REGISTRIES})
        frozen = {CHECKER.INVENTORY: json_bytes(self.inventory), CHECKER.CONTRACT: json_bytes(self.contract)}
        self.context = CHECKER.TrustedContext(
            self.inventory, self.contract, frozen,
            {name: ("100644", value) for name, value in baseline_bytes.items()},
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

    def audit(self) -> None:
        with mock.patch.dict(os.environ, self.environment, clear=True):
            CHECKER.verify_preservation(self.root, self.context)

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

    def test_completed_history_is_independent_of_benign_current_changes(self):
        write(self.root, self.fixture.host, json_bytes({"agent": {"plan": {"model": "openai/new"}},
                                                      "provider": {"new": {}}, "permission": {"read": "ask"}}))
        write(self.root, "dot_claude/settings-base.json", b'{"new":"setting"}\n')
        write(self.root, "docs/devcontainers.md", b"legitimate current documentation\n")
        result = self.fixture.check()
        self.assertEqual(result["historicalPreservation"]["completed"], CHECKER.COMPLETED_PREPARATION)
        self.assertIn("does not prove chezmoi exclusion", result["proof"])
        original = copy.deepcopy(self.fixture.context.completed)
        variants = [({**original, self.fixture.host: ("100644", b"changed historical bytes\n")}, "completed protected source bytes"),
                    ({**original, self.fixture.host: ("100755", original[self.fixture.host][1])}, "completed protected mode"),
                    ({name: value for name, value in original.items() if name != self.fixture.host}, "completed protected path"),
                    ({**original, CHECKER.REGISTRIES[0]: ("100644", b'{"entries":[]}\n')}, "existing registration")]
        for completed, diagnostic in variants:
            with self.subTest(diagnostic=diagnostic):
                context = CHECKER.TrustedContext(self.fixture.inventory, self.fixture.contract,
                                                 self.fixture.context.frozen, self.fixture.context.baseline, completed)
                with self.assertRaisesRegex(CHECKER.CheckFailure, diagnostic):
                    CHECKER.verify_completed_preservation(context)
        context = CHECKER.TrustedContext(self.fixture.inventory, self.fixture.contract,
                                         self.fixture.context.frozen, self.fixture.context.baseline)
        with self.assertRaisesRegex(CHECKER.CheckFailure, "missing completed"):
            CHECKER.verify_completed_preservation(context)
        legacy = {"public-link": ("120000", b"target")}
        context = CHECKER.TrustedContext({}, {}, {}, legacy, {"public-link": ("120000", b"retargeted")})
        with self.assertRaisesRegex(CHECKER.CheckFailure, "completed protected source bytes"):
            CHECKER.verify_completed_preservation(context)

    def test_loader_fields_resolve_independently_and_reject_preparation_exposure(self):
        project = ".opencode/opencode.jsonc"
        cases = [({"instructions": ["configs/opencode-v2/prepared/owner/agents/agent-engineer.md"]}, "inactive preparation"),
                 ({"instructions": ["CONFIGS/OPENCODE-V2/**"]}, "inactive preparation"),
                 ({"skills": {"paths": ["../configs"]}}, "inactive ancestor"),
                 ({"skills": {"paths": ["../configs/**"]}}, "inactive preparation"),
                  ({"plugin": [["../configs/opencode-v2/payload.js", {}]]}, "inactive preparation"),
                  ({"plugin": [r"\\server\configs\opencode-v2\payload.js"]}, "unverified"),
                  ({"plugin": [r"configs\opencode-v2\payload.js"]}, "unverified"),
                  ({"plugin": ["C:payload.js"]}, "unverified"),
                 ({"agent": {"x": {"prompt": "first {file:../configs/opencode-v2/source/inventory.json} last"}}}, "inactive preparation"),
                 ({"agents": {"x": {"system": "{file:../configs/opencode-v2/source/inventory.json}"}}}, "inactive preparation"),
                 ({"skills": {"paths": ["{env:LOCAL_SKILLS}"]}}, "unverified"),
                 ({"agent": {"x": {"prompt": "{file:{env:LOCAL_PROMPT}}"}}}, "unverified prompt interpolation")]
        for value, diagnostic in cases:
            with self.subTest(value=value):
                write(self.root, project, json_bytes(value))
                with self.assertRaisesRegex(CHECKER.CheckFailure, diagnostic):
                    self.fixture.check()
        write(self.root, project, json_bytes({
            "instructions": ["docs/public.md"], "skills": {"paths": ["./skills"]},
            "plugin": [["@plannotator/opencode@{env:PLANNOTATOR_PIN_VERSION}", {}], "./plugins/local.js"],
            "agent": {"x": {"prompt": "{file:~/.config/opencode/prompts/plan-agent.md}"}},
        }))
        self.assertEqual(self.fixture.check()["status"], "PASS")
        self.assertEqual(CHECKER.local_reference(self.root, "~/.config/opencode/prompts/x.md", project),
                         "private_dot_config/opencode/prompts/x.md")
        container = CHECKER.OPENCODE_ROOTS[1] + "/opencode.jsonc"
        self.assertEqual(CHECKER.local_reference(self.root, "~/.config/opencode/prompts/x.md", container),
                         CHECKER.OPENCODE_ROOTS[1] + "/prompts/x.md")
        for value in ("../configs/opencode-v2", "instructions.md"):
            write(self.root, self.fixture.host, json_bytes({"instructions": [value]}))
            with self.assertRaisesRegex(CHECKER.CheckFailure, "relative global instruction"):
                self.fixture.check()
        write(self.root, self.fixture.host, json_bytes({"instructions": [(self.root / "docs/public.md").as_uri()]}))
        self.assertEqual(self.fixture.check()["status"], "PASS")

    def test_mapped_loader_namespaces_reject_escapes_before_target_inspection(self):
        project = ".opencode/opencode.jsonc"
        tail = "Documents/development/dotfiles/configs/opencode-v2/x.js"
        host, container = CHECKER.OPENCODE_ROOTS
        cases = [
            (host + "/opencode.jsonc", {"plugin": ["../../" + tail]}),
            (container + "/opencode.jsonc", {"plugin": [["../../" + tail, {}]]}),
            (project, {"plugin": ["~/.config/opencode/../../" + tail]}),
            (project, {"agent": {"x": {"prompt": "{file:~/.claude/../" + tail + "}"}}}),
            (project, {"instructions": ["~/.config/opencode/../../" + tail]}),
            (host + "/opencode.jsonc", {"instructions": ["~/.claude/../" + tail]}),
            (host + "/opencode.jsonc", {"skills": {"paths": ["../"]}}),
            (project, {"skills": {"paths": ["~/.config/opencode/../../Documents/development/dotfiles/configs"]}}),
            (host + "/profiles/alpha/opencode.jsonc", {"plugin": ["../../../../" + tail]}),
            (container + "/profiles/alpha/opencode.jsonc", {"agents": {"x": {"system": "{file:../../../../" + tail + "}"}}}),
            (project, {"plugin": ["file:~/.config/opencode/%2e%2e/%2e%2e/" + tail]}),
            (host + "/opencode.jsonc", {"plugin": ["file:%2e%2e/%2e%2e/" + tail]}),
            (project, {"skills": {"paths": ["~/.CoNfIg/OpEnCoDe/../OPENCODE-extra/**"]}}),
            (project, {"plugin": ["~/.ClAuDe/../dot_claude-extra/plugin.js"]}),
        ]
        original_read = CHECKER.read_bytes
        for config, value in cases:
            with self.subTest(config=config, value=value):
                write(self.root, config, json_bytes(value))
                def guarded_read(root, name):
                    self.assertEqual(name, config)
                    return original_read(root, name)
                with mock.patch.object(CHECKER, "read_bytes", side_effect=guarded_read), \
                     mock.patch.object(CHECKER, "reject_unknown_aliases", side_effect=AssertionError("target inspection before namespace verification")) as inspect:
                    with self.assertRaisesRegex(CHECKER.CheckFailure, "escaping mapped loader namespace"):
                        CHECKER.verify_loading_config(self.root, config, self.fixture.context, {config})
                    inspect.assert_not_called()
        for namespace in ("dot_claude", f"{CHECKER.CONTAINER_SOURCE}/dot_claude"):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(CHECKER.CheckFailure, "mapped loader namespace"):
                CHECKER.local_reference(self.root, "../outside.md", namespace + "/symlink_CLAUDE.md")

    def test_mapped_loader_namespaces_preserve_declaring_bases_and_public_mappings(self):
        project = ".opencode/opencode.jsonc"
        host, container = CHECKER.OPENCODE_ROOTS
        for namespace in (host, container, "dot_claude", f"{CHECKER.CONTAINER_SOURCE}/dot_claude"):
            config = namespace + "/profiles/alpha/opencode.jsonc"
            for value, expected in (("../shared/plugin.js", namespace + "/profiles/shared/plugin.js"),
                                    ("../../prompts/x.md", namespace + "/prompts/x.md"),
                                    ("./child/../plugin.js", namespace + "/profiles/alpha/plugin.js")):
                with self.subTest(config=config, value=value):
                    self.assertEqual(CHECKER.local_reference(self.root, value, config), expected)
        for config, namespace in ((project, host), (container + "/opencode.jsonc", container)):
            self.assertEqual(CHECKER.local_reference(self.root, "~/.config/opencode", config), namespace)
            self.assertEqual(CHECKER.local_reference(self.root, "file:~/.config/opencode/prompts/%2e%2e/agents/x.md", config),
                             namespace + "/agents/x.md")
        self.assertEqual(CHECKER.local_reference(self.root, "../public/plugin.js", project), "public/plugin.js")
        self.assertEqual(CHECKER.local_reference(self.root, str(self.root / "docs/../public.md"), self.fixture.host), "public.md")
        self.assertEqual(CHECKER.local_reference(self.root, (self.root / "docs/../public.md").as_uri(), self.fixture.host), "public.md")
        for namespace in (host, container):
            config = namespace + "/profiles/alpha/opencode.jsonc"
            write(self.root, config, json_bytes({"skills": {"paths": ["../shared"]},
                                                "plugin": ["../shared/plugin.js", "@plannotator/opencode@{env:PLANNOTATOR_PIN_VERSION}"],
                                                "agent": {"x": {"prompt": "{file:../../prompts/x.md}"}}}))
            CHECKER.verify_loading_config(self.root, config, self.fixture.context, {config})
            write(self.root, config, json_bytes({"instructions": ["../../prompts/x.md"]}))
            with self.assertRaisesRegex(CHECKER.CheckFailure, "relative global instruction"):
                CHECKER.verify_loading_config(self.root, config, self.fixture.context, {config})

    def test_current_inputs_presence_url_escapes_and_external_boundaries(self):
        for value in ("C:/configs/opencode-v2", "//server/configs/opencode-v2", "file:///tmp/%252e%252e/x",
                      "!configs/**", "{env:PATH}", "../../escape"):
            with self.subTest(value=value), self.assertRaises(CHECKER.CheckFailure):
                CHECKER.local_reference(self.root, value, ".opencode/opencode.jsonc")
        self.assertIsNone(CHECKER.local_reference(self.root, "/unqualified/external/file.md", self.fixture.host))
        target = self.root / self.fixture.host
        original = target.read_bytes()
        target.unlink()
        with self.assertRaisesRegex(CHECKER.CheckFailure, "missing mandatory"):
            self.fixture.check()
        write(self.root, self.fixture.host, original)
        optional = "opencode.jsonc"
        write(self.root, optional, b"{}\n")
        write(self.root, ".gitignore", b"/opencode.jsonc\n")
        original_read = CHECKER.read_bytes
        def guarded_read(root, name):
            self.assertNotEqual(name, optional)
            return original_read(root, name)
        with mock.patch.object(CHECKER, "read_bytes", side_effect=guarded_read):
            with self.assertRaisesRegex(CHECKER.CheckFailure, "non-public loading configuration"):
                self.fixture.check()
        (self.root / optional).unlink()
        write(self.root, CHECKER.OPENCODE_ROOTS[0] + "/profiles/synthetic/opencode.jsonc", b"{}\n")
        self.assertEqual(self.fixture.check()["currentLoading"]["configurations"], 4)

    def test_alias_payloads_have_bounded_chains_and_never_read_targets(self):
        directory = self.root / ".opencode/skills"
        directory.mkdir(parents=True)
        write(self.root, ".opencode/skills/target.md", b"public terminal target\n")
        for number in range(16):
            write(self.root, f".opencode/skills/symlink_link{number}",
                  (f"link{number + 1}" if number < 15 else "target.md").encode())
        self.assertEqual(self.fixture.check()["currentLoading"]["aliases"], 16)
        write(self.root, ".opencode/skills/symlink_link15", b"link16")
        write(self.root, ".opencode/skills/symlink_link16", b"target.md")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "hop limit"):
            self.fixture.check()
        for candidate in directory.glob("symlink_*"):
            candidate.unlink()
        for target, diagnostic in (("../../configs/opencode-v2", "inactive preparation"),
                                   ("cycle", "alias cycle"), ("missing", "dangling"),
                                   ("/unqualified/external", "external alias")):
            with self.subTest(target=target):
                write(self.root, ".opencode/skills/symlink_cycle", target.encode())
                with self.assertRaisesRegex(CHECKER.CheckFailure, diagnostic):
                    self.fixture.check()
        (directory / "symlink_cycle").unlink()
        (directory / "native").symlink_to("../../configs/opencode-v2")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "inactive preparation"):
            self.fixture.check()

    def test_mirror_destinations_are_guarded_before_source_overlap(self):
        def check_mirror(source, target, include, exclude):
            write(self.root, CHECKER.MIRRORS, json_bytes({"shared": {"mirrors": [
                {"source_root": source, "target_root": target, "include": include, "exclude": exclude}
            ]}}))
            return self.fixture.check()
        for target in (CHECKER.AREA, CHECKER.AREA + "/source", "configs"):
            with self.subTest(target=target), self.assertRaisesRegex(CHECKER.CheckFailure, "inactive"):
                check_mirror("unrelated-public-source", target, ["**"], [])
        with self.assertRaisesRegex(CHECKER.CheckFailure, "projects inactive"):
            check_mirror(CHECKER.AREA + "/prepared/owner", ".opencode", ["agents/**"], [])
        with self.assertRaisesRegex(CHECKER.CheckFailure, "projects inactive"):
            check_mirror("CONFIGS/OpenCode-V2/prepared/owner", ".opencode", ["agents/**"], [])
        self.assertEqual(check_mirror(CHECKER.AREA, ".opencode/skills", ["**"], ["**"])["status"], "PASS")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "projects inactive"):
            check_mirror(CHECKER.AREA, ".opencode/skills", ["**"], ["SOURCE/**", "PREPARED/**"])
        self.assertEqual(check_mirror("unrelated-public-source", "public-mirror", ["**"], [])["status"], "PASS")

    def test_public_projection_filters_before_blob_reads_and_preserves_main_delta(self):
        source = self.root / "projection-source"
        target = self.root / "projection-target"
        source.mkdir()
        projection_git(source, "init", "-q")
        original_checker = b"heading\nkeep-1\nkeep-2\nkeep-3\nkeep-4\nkeep-5\nmarker\n"
        rows = {name: ("100644", (name + "\n").encode()) for name in AMENDMENT_PATHS}
        rows[AMENDMENT_PATHS[0]] = ("100644", original_checker)
        rows["docs/devcontainers.md"] = ("100644", b"original documentation\n")
        excluded_name = "private/credentials.json"
        excluded_bytes = b"owned-secret-sentinel-never-project\n"
        rows[excluded_name] = ("100644", excluded_bytes)
        base = projection_commit(source, rows)
        main_rows = copy.deepcopy(rows)
        main_rows[AMENDMENT_PATHS[0]] = ("100644", original_checker.replace(b"heading", b"main-only"))
        main_rows["docs/devcontainers.md"] = ("100644", b"main-only documentation\n")
        main = projection_commit(source, main_rows, base)
        feature_rows = copy.deepcopy(rows)
        feature_rows[AMENDMENT_PATHS[3]] = ("100644", b"feature-only ledger\n")
        feature = projection_commit(source, feature_rows, base)
        projection_git(source, "update-ref", "refs/heads/main", main)
        projection_git(source, "update-ref", "refs/heads/feature", feature)
        projection_git(source, "symbolic-ref", "HEAD", "refs/heads/feature")
        projection_git(source, "read-tree", feature)
        projection_git(source, "checkout-index", "--all", "--force")
        write(source, AMENDMENT_PATHS[0], original_checker.replace(b"marker", b"amended-marker"))
        delta = source_git(source, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                           "--full-index", feature, "--", *AMENDMENT_PATHS)
        excluded_oid = hashlib.sha1(b"blob " + str(len(excluded_bytes)).encode() + b"\0" + excluded_bytes).hexdigest()
        before = tree_snapshot(source)
        original_source_git = source_git
        original_projection_git = projection_git
        requests = []
        apply_observations = []

        def guarded_source_git(source_root, *arguments, **kwargs):
            if arguments[0] == "cat-file":
                requested = kwargs.get("input_bytes", b"").splitlines()
                requests.extend(requested)
                self.assertNotIn(excluded_oid.encode(), requested)
            return original_source_git(source_root, *arguments, **kwargs)

        def observe_real_apply(projection_root, *arguments, **kwargs):
            if arguments[:3] == ("apply", "--3way", "--index"):
                self.assertEqual(projection_root, target)
                agreement = original_projection_git(
                    projection_root, "diff-files", "--quiet", "--", *AMENDMENT_PATHS, check=False)
                self.assertEqual(agreement.returncode, 0, agreement.stderr.decode(errors="replace"))
                apply_observations.append(projection_root)
            return original_projection_git(projection_root, *arguments, **kwargs)

        with mock.patch.object(sys.modules[__name__], "source_git", side_effect=guarded_source_git), mock.patch.object(
                sys.modules[__name__], "projection_git", side_effect=observe_real_apply):
            result = build_public_projection(source, (base, main, feature), delta, target)
        self.assertEqual(apply_observations, [target])
        self.assertTrue(requests)
        self.assertEqual(tree_snapshot(source), before)
        self.assertFalse((target / excluded_name).exists())
        self.assertFalse((target / ".git/objects/info/alternates").exists())
        self.assertNotEqual(projection_git(target, "cat-file", "-e", excluded_oid, check=False).returncode, 0)
        self.assertEqual((target / AMENDMENT_PATHS[0]).read_bytes(),
                         original_checker.replace(b"heading", b"main-only").replace(b"marker", b"amended-marker"))
        self.assertEqual((target / "docs/devcontainers.md").read_bytes(), b"main-only documentation\n")
        self.assertEqual((target / AMENDMENT_PATHS[3]).read_bytes(), b"feature-only ledger\n")
        self.assertEqual(set(result["amendedFiles"]), set(AMENDMENT_PATHS))
        with self.assertRaisesRegex(AssertionError, "source operation is not read-only"):
            source_git(source, "update-ref", "refs/heads/main", feature)
        self.assertEqual(tree_snapshot(source), before)
        template_rows = copy.deepcopy(rows)
        template_rows[".opencode/opencode.jsonc.tmpl"] = ("100644", b"not requested\n")
        template_commit = projection_commit(source, template_rows, base)
        calls = []

        def template_boundary(source_root, *arguments, **kwargs):
            calls.append(arguments[0])
            return original_source_git(source_root, *arguments, **kwargs)

        with mock.patch.object(sys.modules[__name__], "source_git", side_effect=template_boundary):
            with self.assertRaisesRegex(AssertionError, "unverified loader template"):
                projection_public_view(source, template_commit)
        self.assertNotIn("cat-file", calls)

    def test_public_projection_conflicts_and_unapproved_delta_fail_closed(self):
        for scenario in ("content", "amendment", "scope"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary).resolve() / "source"
                target = Path(temporary).resolve() / "target"
                source.mkdir()
                projection_git(source, "init", "-q")
                original = b"heading\nkeep-1\nkeep-2\nkeep-3\nkeep-4\nkeep-5\nmarker\n"
                rows = {name: ("100644", (name + "\n").encode()) for name in AMENDMENT_PATHS}
                rows[AMENDMENT_PATHS[0]] = ("100644", original)
                base = projection_commit(source, rows)
                main_rows = copy.deepcopy(rows)
                feature_rows = copy.deepcopy(rows)
                if scenario == "content":
                    main_rows[AMENDMENT_PATHS[0]] = ("100644", original.replace(b"heading", b"main"))
                    feature_rows[AMENDMENT_PATHS[0]] = ("100644", original.replace(b"heading", b"feature"))
                elif scenario == "amendment":
                    main_rows[AMENDMENT_PATHS[0]] = ("100644", original.replace(b"marker", b"main-marker"))
                main = projection_commit(source, main_rows, base)
                feature = projection_commit(source, feature_rows, base)
                projection_git(source, "update-ref", "refs/heads/main", main)
                projection_git(source, "update-ref", "refs/heads/feature", feature)
                projection_git(source, "symbolic-ref", "HEAD", "refs/heads/feature")
                projection_git(source, "read-tree", feature)
                projection_git(source, "checkout-index", "--all", "--force")
                delta = b""
                diagnostic = "public projection content conflict"
                if scenario == "amendment":
                    write(source, AMENDMENT_PATHS[0], original.replace(b"marker", b"amended-marker"))
                    delta = source_git(source, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                                       "--full-index", feature, "--", *AMENDMENT_PATHS)
                    diagnostic = "public projection amendment application failed"
                elif scenario == "scope":
                    delta = b"diff --git a/unapproved.md b/unapproved.md\n"
                    diagnostic = "unapproved path"
                before = tree_snapshot(source)
                with self.assertRaisesRegex(AssertionError, diagnostic) as failure:
                    build_public_projection(source, (base, main, feature), delta, target)
                if scenario == "amendment":
                    self.assertIn(f"Applied patch to '{AMENDMENT_PATHS[0]}' with conflicts.", str(failure.exception))
                    unmerged = projection_git(target, "ls-files", "--unmerged", "-z").stdout
                    entries = [record.split(b"\t", 1) for record in unmerged.split(b"\0") if record]
                    self.assertEqual(len(entries), 3)
                    self.assertEqual({(details.split()[2], name) for details, name in entries},
                                     {(str(stage).encode(), AMENDMENT_PATHS[0].encode()) for stage in (1, 2, 3)})
                self.assertEqual(tree_snapshot(source), before)

    def test_public_projection_keeps_one_capture_when_shared_refs_advance(self):
        original_view = projection_public_view
        original_source_git = source_git
        for scenario in ("main", "other", "both"):
            with self.subTest(scenario=scenario):
                source = self.root / ("moving-source-" + scenario)
                target = self.root / ("moving-target-" + scenario)
                revisions, later = timing_projection_fixture(source)
                before = projection_source_snapshot(source)
                selected = []
                main_queries = []
                actor_snapshot = []

                def record_source_query(source_root, *arguments, **kwargs):
                    if arguments == ("rev-parse", "refs/heads/main"):
                        main_queries.append(arguments)
                    return original_source_git(source_root, *arguments, **kwargs)

                def advance_shared_ref(source_root, revision):
                    selected.append(revision)
                    if len(selected) == 1:
                        if scenario in ("main", "both"):
                            projection_git(source, "update-ref", "refs/heads/main", later)
                        if scenario in ("other", "both"):
                            projection_git(source, "update-ref", "refs/heads/other", later)
                        actor_snapshot.append(tree_snapshot(source))
                    return original_view(source_root, revision)

                with mock.patch.object(sys.modules[__name__], "source_git", side_effect=record_source_query), \
                        mock.patch.object(sys.modules[__name__], "projection_public_view", side_effect=advance_shared_ref):
                    captured = capture_projection_revisions(source, revisions[2])
                    result = build_public_projection(source, captured, b"", target)
                    observation = projection_source_observation(source, before, captured[1])
                self.assertEqual(captured, revisions)
                self.assertEqual(selected, list(revisions))
                self.assertEqual(len(main_queries), 2)
                self.assertTrue(result["sharedRefMovementObserved"])
                self.assertEqual(observation, {
                    "capturedMain": revisions[1],
                    "observedMainAfter": later if scenario in ("main", "both") else revisions[1],
                    "mainMovementObserved": scenario in ("main", "both"),
                    "sharedRefMovementObserved": True,
                })
                self.assertEqual((target / "docs/devcontainers.md").read_bytes(), b"captured main documentation\n")
                self.assertEqual(tree_snapshot(source), actor_snapshot[0])
                require_projection_source_stable(before, projection_source_snapshot(source))

    def test_public_projection_rejects_own_index_files_and_feature_drift(self):
        original_view = projection_public_view
        for scenario in ("index", "files", "feature"):
            with self.subTest(scenario=scenario):
                source = self.root / ("own-source-" + scenario)
                target = self.root / ("own-target-" + scenario)
                revisions, later = timing_projection_fixture(source)
                actor_snapshot = []

                def change_owned_state(source_root, revision):
                    if not actor_snapshot:
                        if scenario == "index":
                            projection_git(source, "update-index", "--force-remove", "--", AMENDMENT_PATHS[0])
                        elif scenario == "files":
                            write(source, AMENDMENT_PATHS[0], b"competing owned-fixture edit\n")
                        else:
                            projection_git(source, "update-ref", "refs/heads/feature", later)
                        actor_snapshot.append(tree_snapshot(source))
                    return original_view(source_root, revision)

                captured = capture_projection_revisions(source, revisions[2])
                with mock.patch.object(sys.modules[__name__], "projection_public_view", side_effect=change_owned_state):
                    with self.assertRaisesRegex(AssertionError, "projection source changed: " + scenario):
                        build_public_projection(source, captured, b"", target)
                self.assertEqual(tree_snapshot(source), actor_snapshot[0])

        source = self.root / "feature-preflight-source"
        revisions, _ = timing_projection_fixture(source)
        before = tree_snapshot(source)
        with self.assertRaisesRegex(AssertionError, "feature moved before public projection"):
            capture_projection_revisions(source, revisions[0])
        self.assertEqual(tree_snapshot(source), before)

    def test_protected_bytes_path_sets_modes_and_registry_history(self):
        original = (self.root / self.fixture.host).read_bytes()
        write(self.root, self.fixture.host, b"changed policy\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes"):
            self.fixture.audit()
        write(self.root, self.fixture.host, original)
        write(self.root, "private_dot_config/opencode/agents/new.md", b"new registration\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "path set"):
            self.fixture.audit()
        (self.root / "private_dot_config/opencode/agents/new.md").unlink()
        write(self.root, CHECKER.REGISTRIES[0], b'{"entries":[]}\n')
        with self.assertRaisesRegex(CHECKER.CheckFailure, "existing registration"):
            self.fixture.audit()

    def test_protected_executable_mode_drift_is_rejected(self):
        target = self.root / self.fixture.host
        original_mode = stat.S_IMODE(target.stat().st_mode)
        try:
            target.chmod(original_mode | 0o111)
            with self.assertRaisesRegex(CHECKER.CheckFailure, "protected mode changed"):
                self.fixture.audit()
        finally:
            target.chmod(original_mode)
        self.fixture.audit()

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
        self.fixture.audit()

        for field in ("committedDigest", "workingDigest"):
            with self.subTest(digest=field):
                original_digest = record[field]
                record[field] = "0" * 64
                self.fixture.refresh_context()
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
                    self.fixture.audit()
                record[field] = original_digest

        changed = b"first\r\nchanged\r\n"
        write(self.root, name, changed)
        record["workingDigest"] = sha(changed)
        self.fixture.refresh_context()
        with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
            self.fixture.audit()
        write(self.root, name, working)
        record["workingDigest"] = sha(working)

        for rules in (b"* text=auto\n", f"{name} text eol=lf\n".encode(),
                      f"{name} -text eol=crlf\n".encode()):
            with self.subTest(attributes=rules):
                self.fixture.context.baseline[".gitattributes"] = ("100644", rules)
                write(self.root, ".gitattributes", rules)
                self.fixture.refresh_context()
                with self.assertRaisesRegex(CHECKER.CheckFailure, "unverified checkout EOL"):
                    self.fixture.audit()

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
                self.fixture.audit()
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

                link.unlink()
                write(self.root, name, target_bytes)
                before = tree_snapshot(self.root)
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected link type changed"):
                    self.fixture.audit()
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

                link.unlink()
                link.symlink_to(alternative)
                before = tree_snapshot(self.root)
                with self.assertRaisesRegex(CHECKER.CheckFailure, "protected source bytes changed"):
                    self.fixture.audit()
                self.assertEqual(tree_snapshot(self.root), before)
                self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
            self.assertEqual(outside.read_bytes(), sentinel)
            self.assertEqual(alternative.read_bytes(), sentinel)

    def test_registration_additions_and_ledger_append_preserve_history(self):
        for name in CHECKER.REGISTRIES:
            write(self.root, name, b'{"entries":[{"id":"existing"},{"id":"new"}]}\n'
                  if name.endswith(".json") else b"existing-helper.py\nnew-helper.py\n")
        write(self.root, CHECKER.LEDGER, b"# Synthetic historical ledger\n\nDV2-020 result\n")
        self.fixture.audit()
        write(self.root, CHECKER.LEDGER, b"rewritten history\n")
        with self.assertRaisesRegex(CHECKER.CheckFailure, "historical ledger"):
            self.fixture.audit()

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
