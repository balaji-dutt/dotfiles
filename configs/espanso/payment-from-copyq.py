#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


BANK_PORTALS = (
    "StanChart Internet Banking",
    "OCBC Internet Banking",
    "CardUp Portal",
)

VARIANT_SUFFIX = {
    "paidcc": "",
    "dpaidcc": "\n\nSalary Pool\t: $ 0.00 (0.00 Diff. Eye)\nSpare Pool\t: $ 0.00 (0.00 Diff. Eye)",
    "spaidcc": "\n\nSalary Pool\t: $ 0.00\nSpare Pool\t: $ 0.00",
    "lpaidcc": "\n\nSalary Pool\t: $ 0.00\nReimb. Pool\t: $ 0.00",
}


@dataclass
class Payment:
    amount: str
    bank_portal: str
    reference: str
    transfer_date: str


def require_match(text: str, pattern: str, field_name: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(f"Could not parse {field_name} from the selected receipt text.")
    return match.group("value").strip()


def normalize_amount(raw_value: str) -> str:
    cleaned = re.sub(r"[^0-9.,]", "", raw_value).replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Could not parse amount value: {raw_value!r}") from exc
    return f"{amount:.2f}"


def normalize_date(raw_value: str) -> str:
    raw_value = raw_value.strip()
    for date_format in ("%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            parsed = dt.datetime.strptime(raw_value, date_format)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise ValueError(f"Could not parse date value: {raw_value!r}")


def run_command(command: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def read_copyq_item(tab_name: str) -> str:
    copyq = shutil.which("copyq") or shutil.which("copyq.exe")
    if not copyq:
        raise RuntimeError("copyq is not available in PATH.")

    code, stdout, stderr = run_command([copyq, "tab", tab_name, "read", "0"])
    if code == 0 and stdout:
        return stdout

    if os.name == "nt":
        pwsh = shutil.which("pwsh") or shutil.which("pwsh.exe")
        if pwsh:
            tab_name_escaped = tab_name.replace("'", "''")
            copyq_escaped = copyq.replace("'", "''")
            command = f"& '{copyq_escaped}' tab '{tab_name_escaped}' read 0 | Write-Output"
            fallback_code, fallback_stdout, fallback_stderr = run_command(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
            )
            if fallback_code == 0 and fallback_stdout:
                return fallback_stdout
            stderr = fallback_stderr or stderr

    if stderr:
        raise RuntimeError(f"Could not read CopyQ tab '{tab_name}' item 0: {stderr}")
    raise RuntimeError(f"Could not read CopyQ tab '{tab_name}' item 0.")


def parse_stanchart(text: str, bank_portal: str) -> Payment:
    reference = require_match(
        text,
        r"Receipt number:\s*(?P<value>[^\r\n]+)",
        "receipt number",
    )
    amount = normalize_amount(
        require_match(
            text,
            r"Amount:\s*SGD\s*(?P<value>[0-9,]+\.[0-9]{2})",
            "amount",
        )
    )
    transfer_date = normalize_date(
        require_match(
            text,
            r"When to be transferred:\s*(?P<value>\d{1,2}/\d{1,2}/\d{4})",
            "transfer date",
        )
    )

    return Payment(
        amount=amount,
        bank_portal=bank_portal,
        reference=reference,
        transfer_date=transfer_date,
    )


def parse_ocbc(text: str, bank_portal: str) -> Payment:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("The OCBC receipt text did not contain the expected fields.")

    amount_candidates = [line for line in lines if re.fullmatch(r"[0-9,]+\.[0-9]{2}", line)]
    if not amount_candidates:
        raise ValueError("Could not parse amount from the OCBC receipt text.")

    transfer_match = re.search(
        r"\bon\s+(?P<value>\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not transfer_match:
        raise ValueError("Could not parse transfer date from the OCBC receipt text.")

    return Payment(
        amount=normalize_amount(amount_candidates[-1]),
        bank_portal=bank_portal,
        reference=lines[0],
        transfer_date=normalize_date(transfer_match.group("value")),
    )


def format_payment(payment: Payment, variant: str) -> str:
    tx_date = dt.date.today().strftime("%d/%m/%Y")
    base = (
        f"Paid ${payment.amount} vide {payment.bank_portal} "
        f"(Tx Ref: {payment.reference}) on {payment.transfer_date} "
        f"(Tx Date: {tx_date})"
    )
    return base + VARIANT_SUFFIX[variant]


def build_output(variant: str, bank_portal: str, source_text: str) -> str:
    if bank_portal == "StanChart Internet Banking":
        payment = parse_stanchart(source_text, bank_portal)
        return format_payment(payment, variant)

    if bank_portal == "OCBC Internet Banking":
        payment = parse_ocbc(source_text, bank_portal)
        return format_payment(payment, variant)

    if bank_portal == "CardUp Portal":
        return "[CardUp Portal parser not implemented: add a sample receipt text.]"

    raise ValueError(f"Unsupported bank portal: {bank_portal!r}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=tuple(VARIANT_SUFFIX))
    parser.add_argument("--bank-portal", required=True, choices=BANK_PORTALS)
    parser.add_argument("--copyq-tab", default="AppAutomation")
    parser.add_argument("--clipboard-text")
    args = parser.parse_args()

    try:
        source_text = args.clipboard_text if args.clipboard_text is not None else read_copyq_item(args.copyq_tab)
        print(build_output(args.variant, args.bank_portal, source_text))
        return 0
    except Exception as exc:
        print(f"[Payment parser error: {exc}]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
