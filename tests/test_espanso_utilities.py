from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_SCRIPT = REPO_ROOT / "configs/espanso/aoe-session-name.py"
PAYMENT_SCRIPT = REPO_ROOT / "configs/espanso/payment-from-copyq.py"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


session_name = load_script("test_aoe_session_name_script", SESSION_SCRIPT)
payment = load_script("test_payment_from_copyq_script", PAYMENT_SCRIPT)


STANCHART_RECEIPT = """\
Receipt number: SAFE-REF-123
Amount: SGD 1,234.50
When to be transferred: 28/08/2026
"""

OCBC_RECEIPT = """\
SAFE-OCBC-456
Payment successful
1,234.50
Transferred on 28 Aug 2026
"""

CARDUP_RECEIPT = """\
txn_SAFE789
Paid SGD 100.00 on 27/08/2026
Fee SGD 5.00
Total SGD 105.00 on 28/08/2026
safe-offer SGD 5.00 on 28/08/2026
"""


class SessionNameTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SESSION_SCRIPT), *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_slugify_normalizes_unicode_punctuation_and_separators(self) -> None:
        cases = {
            "  Café déjà vu!  ": "cafe-deja-vu",
            "Fix___three...things///now": "fix-three-things-now",
            "Ångström & Español": "angstrom-espanol",
            "---": "",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(session_name.slugify(source), expected)

    def test_validate_ref_name_rejects_every_unsafe_family(self) -> None:
        invalid = (
            "",
            "@",
            "/feat/name",
            "feat/name/",
            "feat//name",
            "feat/name..next",
            "feat/name@{next",
            "feat/name.",
            "feat/ name",
            "feat/name~next",
            "feat/name^next",
            "feat/name:next",
            "feat/name?next",
            "feat/name*next",
            "feat/name[next",
            "feat/name\\next",
            "feat/name\x1fnext",
            "feat/.hidden",
            "feat/name.lock",
        )
        for ref_name in invalid:
            with self.subTest(ref_name=repr(ref_name)):
                with self.assertRaises(ValueError):
                    session_name.validate_ref_name(ref_name)

    def test_build_session_name_supports_all_documented_types(self) -> None:
        for branch_type in session_name.BRANCH_TYPES:
            with self.subTest(branch_type=branch_type):
                self.assertEqual(
                    session_name.build_session_name(branch_type, "Safe change"),
                    f"{branch_type}/safe-change",
                )

    def test_cli_writes_exact_name_without_newline(self) -> None:
        result = self.run_cli("--type", "feat", "--description", "Café paths & locks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "feat/cafe-paths-locks")
        self.assertEqual(result.stderr, "")

    def test_cli_rejects_invalid_type_and_empty_slug(self) -> None:
        invalid_type = self.run_cli("--type", "release", "--description", "safe")
        self.assertEqual(invalid_type.returncode, 2)
        self.assertIn("invalid choice", invalid_type.stderr)

        empty = self.run_cli("--type", "fix", "--description=---")
        self.assertEqual(empty.returncode, 1)
        self.assertEqual(empty.stdout, "")
        self.assertIn("at least one letter or number", empty.stderr)


class PaymentParsingTests(unittest.TestCase):
    def test_parses_stanchart_and_ocbc_receipts(self) -> None:
        stanchart = payment.parse_stanchart(
            STANCHART_RECEIPT, "StanChart Internet Banking"
        )
        self.assertEqual(stanchart.amount, "1234.50")
        self.assertEqual(stanchart.reference, "SAFE-REF-123")
        self.assertEqual(stanchart.transfer_date, "28/08/2026")

        ocbc = payment.parse_ocbc(OCBC_RECEIPT, "OCBC Internet Banking")
        self.assertEqual(ocbc.amount, "1234.50")
        self.assertEqual(ocbc.reference, "SAFE-OCBC-456")
        self.assertEqual(ocbc.transfer_date, "28/08/2026")

    def test_cardup_validates_totals_dates_and_finds_later_offer_code(self) -> None:
        parsed = payment.parse_cardup(CARDUP_RECEIPT)
        self.assertEqual(parsed.amount, "100.00")
        self.assertEqual(parsed.payment_fee, "5.00")
        self.assertEqual(parsed.reference, "txn_SAFE789")
        self.assertEqual(parsed.transaction_date, "27/08/2026")
        self.assertEqual(parsed.payment_date, "28/08/2026")
        self.assertEqual(parsed.offer_code, "safe-offer")

        bad_total = CARDUP_RECEIPT.replace("Total SGD 105.00", "Total SGD 106.00")
        with self.assertRaisesRegex(ValueError, "did not match the total"):
            payment.parse_cardup(bad_total)

        bad_dates = CARDUP_RECEIPT.replace("27/08/2026", "not-a-date").replace(
            "28/08/2026", "also-not-a-date"
        )
        with self.assertRaisesRegex(ValueError, "transaction and payment dates"):
            payment.parse_cardup(bad_dates)

    def test_cardup_without_offer_code_is_supported(self) -> None:
        parsed = payment.parse_cardup(
            CARDUP_RECEIPT.replace("safe-offer SGD 5.00 on 28/08/2026\n", "")
        )
        self.assertIsNone(parsed.offer_code)

    def test_all_variants_have_exact_portal_specific_format(self) -> None:
        class FrozenDate(payment.dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 28)

        expected_suffixes = payment.VARIANT_SUFFIX
        with mock.patch.object(payment.dt, "date", FrozenDate):
            for variant, suffix in expected_suffixes.items():
                with self.subTest(portal="StanChart", variant=variant):
                    output = payment.build_output(
                        variant, "StanChart Internet Banking", STANCHART_RECEIPT
                    )
                    self.assertEqual(
                        output,
                        "Paid $1234.50 vide StanChart Internet Banking "
                        "(Tx Ref: SAFE-REF-123) on 28/08/2026 "
                        f"(Tx Date: 28/08/2026){suffix}",
                    )

                with self.subTest(portal="OCBC", variant=variant):
                    output = payment.build_output(
                        variant, "OCBC Internet Banking", OCBC_RECEIPT
                    )
                    self.assertEqual(
                        output,
                        "Paid $1234.50 vide OCBC Internet Banking "
                        "(Tx Ref: SAFE-OCBC-456) on 28/08/2026 "
                        f"(Tx Date: 28/08/2026){suffix}",
                    )

                with self.subTest(portal="CardUp", variant=variant):
                    output = payment.build_output(
                        variant, "CardUp Portal", CARDUP_RECEIPT
                    )
                    self.assertEqual(
                        output,
                        "Paid $100.00 vide CardUp Portal\n"
                        "Tx Ref: txn_SAFE789 on 28/08/2026 "
                        "(Tx Date: 27/08/2026)\n"
                        "CardUp Payment Fees: $5.00 (used Offer Code safe-offer)"
                        f"{suffix}",
                    )

    def test_parse_failures_name_fields_without_echoing_values(self) -> None:
        amount_sentinel = "SECRET-AMOUNT"
        date_sentinel = "SECRET-DATE"
        with self.assertRaisesRegex(ValueError, "Could not parse amount value") as amount_error:
            payment.normalize_amount(amount_sentinel)
        self.assertNotIn(amount_sentinel, str(amount_error.exception))

        with self.assertRaisesRegex(ValueError, "Could not parse date value") as date_error:
            payment.normalize_date(date_sentinel)
        self.assertNotIn(date_sentinel, str(date_error.exception))


class CopyQContractTests(unittest.TestCase):
    def test_resolve_copyq_prefers_path_and_deduplicates_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "copyq"
            executable.write_text("fixture", encoding="utf-8")
            with mock.patch.object(payment.shutil, "which") as which:
                which.side_effect = lambda name: str(executable) if name == "copyq" else None
                self.assertEqual(payment.resolve_copyq_path(), str(executable))

    def test_read_copyq_uses_windows_powershell_fallback_with_literal_tab(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str]) -> tuple[int, str, str]:
            calls.append(command)
            if len(calls) == 1:
                return 1, "", "primary failed"
            return 0, "fallback receipt", ""

        with (
            mock.patch.object(payment, "resolve_copyq_path", return_value=r"C:\CopyQ\copyq.exe"),
            mock.patch.object(payment.os, "name", "nt"),
            mock.patch.object(payment.shutil, "which", return_value=r"C:\PowerShell\pwsh.exe"),
            mock.patch.object(payment, "run_command", side_effect=fake_run),
        ):
            self.assertEqual(payment.read_copyq_item("Team's tab"), "fallback receipt")

        self.assertEqual(calls[0], [r"C:\CopyQ\copyq.exe", "tab", "Team's tab", "read", "0"])
        self.assertEqual(calls[1][:4], [r"C:\PowerShell\pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive"])
        self.assertIn("Team''s tab", calls[1][-1])

    def test_copyq_empty_and_failure_errors_do_not_echo_tool_output(self) -> None:
        secret = "SECRET-COPYQ-STDERR"
        with (
            mock.patch.object(payment, "resolve_copyq_path", return_value="/fake/copyq"),
            mock.patch.object(payment, "run_command", return_value=(1, "", secret)),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed while reading") as error:
                payment.read_copyq_item("Synthetic")
        self.assertNotIn(secret, str(error.exception))

        with (
            mock.patch.object(payment, "resolve_copyq_path", return_value="/fake/copyq"),
            mock.patch.object(payment, "run_command", return_value=(0, "", "")),
        ):
            with self.assertRaisesRegex(RuntimeError, "returned no text"):
                payment.read_copyq_item("Synthetic")

    def test_cli_uses_fake_copyq_and_never_reads_real_clipboard(self) -> None:
        with isolated_environment(prefix="espanso-payment-") as fixture:
            write_executable(
                fixture.fake_bin / "copyq",
                f"#!{sys.executable}\n"
                "import sys\n"
                "assert sys.argv[1:] == ['tab', 'Synthetic Tab', 'read', '0']\n"
                f"sys.stdout.write({STANCHART_RECEIPT!r})\n",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(PAYMENT_SCRIPT),
                    "--variant",
                    "paidcc",
                    "--bank-portal",
                    "StanChart Internet Banking",
                    "--copyq-tab",
                    "Synthetic Tab",
                ],
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SAFE-REF-123", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_cli_empty_clipboard_and_subprocess_failure_are_secret_safe(self) -> None:
        empty = subprocess.run(
            [
                sys.executable,
                str(PAYMENT_SCRIPT),
                "--variant",
                "paidcc",
                "--bank-portal",
                "StanChart Internet Banking",
                "--clipboard-text",
                "",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(empty.returncode, 0)
        self.assertIn("Payment parser error", empty.stdout)

        secret = "SECRET-RECEIPT-REFERENCE"
        malformed = subprocess.run(
            [
                sys.executable,
                str(PAYMENT_SCRIPT),
                "--variant",
                "paidcc",
                "--bank-portal",
                "StanChart Internet Banking",
                "--clipboard-text",
                "\n".join(
                    (
                        f"Receipt number: {secret}",
                        "Amount: SGD 1.00",
                        "When to be transferred: 31/02/2026",
                    )
                ),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(malformed.returncode, 0)
        self.assertIn("Could not parse date value", malformed.stdout)
        self.assertNotIn(secret, malformed.stdout + malformed.stderr)

        secret = "SECRET-TOOL-DIAGNOSTIC"
        with isolated_environment(prefix="espanso-payment-error-") as fixture:
            write_executable(
                fixture.fake_bin / "copyq",
                f"#!{sys.executable}\nimport sys\nsys.stderr.write({secret!r})\nraise SystemExit(7)\n",
            )
            failed = subprocess.run(
                [
                    sys.executable,
                    str(PAYMENT_SCRIPT),
                    "--variant",
                    "paidcc",
                    "--bank-portal",
                    "StanChart Internet Banking",
                ],
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(failed.returncode, 0)
        self.assertIn("CopyQ failed while reading", failed.stdout)
        self.assertNotIn(secret, failed.stdout + failed.stderr)


if __name__ == "__main__":
    unittest.main()
