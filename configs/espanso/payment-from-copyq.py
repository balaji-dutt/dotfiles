#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
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


@dataclass
class CardUpPayment:
    amount: str
    reference: str
    payment_fee: str
    transaction_date: str
    payment_date: str
    offer_code: str | None


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
        raise ValueError("Could not parse amount value.") from exc
    return f"{amount:.2f}"


def normalize_date(raw_value: str) -> str:
    raw_value = raw_value.strip()
    for date_format in ("%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            parsed = dt.datetime.strptime(raw_value, date_format)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise ValueError("Could not parse date value.")


def run_command(command: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def resolve_copyq_path() -> str:
    candidates: list[str] = []

    for executable in ("copyq", "copyq.exe"):
        resolved = shutil.which(executable)
        if resolved:
            candidates.append(resolved)

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                r"C:\Program Files\CopyQ\copyq.exe",
                r"C:\Program Files (x86)\CopyQ\copyq.exe",
                os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "copyq.exe"),
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/CopyQ.app/Contents/MacOS/CopyQ",
                os.path.expanduser("~/Applications/CopyQ.app/Contents/MacOS/CopyQ"),
            ]
        )

    seen: set[str] = set()
    normalized_candidates: list[str] = []
    for candidate in candidates:
        normalized = os.path.normpath(candidate) if candidate else ""
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_candidates.append(normalized)

    for candidate in normalized_candidates:
        if Path(candidate).is_file():
            return candidate

    checked = "; ".join(normalized_candidates) if normalized_candidates else "(none)"
    raise RuntimeError(f"copyq is not available in PATH or standard locations. Checked: {checked}")


def read_copyq_item(tab_name: str) -> str:
    copyq = resolve_copyq_path()

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
        raise RuntimeError("CopyQ failed while reading the selected item.")
    raise RuntimeError("CopyQ returned no text for the selected item.")


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


def parse_cardup(text: str) -> CardUpPayment:
    reference = require_match(
        text,
        r"\b(?P<value>txn_[A-Za-z0-9]+)\b",
        "CardUp transaction reference",
    )

    amount_values = re.findall(r"\bSGD\s*([0-9,]+\.[0-9]{2})\b", text, flags=re.IGNORECASE)
    if len(amount_values) < 3:
        raise ValueError("Could not parse CardUp paid amount, fee, and total from the receipt text.")

    amount = normalize_amount(amount_values[0])
    payment_fee = normalize_amount(amount_values[1])
    total = normalize_amount(amount_values[2])
    if Decimal(amount) + Decimal(payment_fee) != Decimal(total):
        raise ValueError("CardUp paid amount and fee did not match the total.")

    date_values = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
    if len(date_values) < 2:
        raise ValueError("Could not parse CardUp transaction and payment dates from the receipt text.")

    offer_code = None
    for line in text.splitlines():
        line = line.strip()
        if "SGD" not in line.upper() or not re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", line):
            continue
        offer_match = re.match(
            r"(?P<value>(?!SGD\b)[A-Z][A-Z0-9-]*)\s+SGD\b",
            line,
            flags=re.IGNORECASE,
        )
        if offer_match:
            candidate = offer_match.group("value")
            if candidate.casefold() in {"amount", "fee", "paid", "payment", "total"}:
                continue
            offer_code = candidate
            break

    return CardUpPayment(
        amount=amount,
        reference=reference,
        payment_fee=payment_fee,
        transaction_date=normalize_date(date_values[0]),
        payment_date=normalize_date(date_values[1]),
        offer_code=offer_code,
    )


def format_payment(payment: Payment, variant: str) -> str:
    tx_date = dt.date.today().strftime("%d/%m/%Y")
    base = (
        f"Paid ${payment.amount} vide {payment.bank_portal} "
        f"(Tx Ref: {payment.reference}) on {payment.transfer_date} "
        f"(Tx Date: {tx_date})"
    )
    return base + VARIANT_SUFFIX[variant]


def format_cardup_payment(payment: CardUpPayment, variant: str) -> str:
    offer_note = (
        f"used Offer Code {payment.offer_code}"
        if payment.offer_code
        else "no offer code used/available"
    )
    base = (
        f"Paid ${payment.amount} vide CardUp Portal\n"
        f"Tx Ref: {payment.reference} on {payment.payment_date} "
        f"(Tx Date: {payment.transaction_date})\n"
        f"CardUp Payment Fees: ${payment.payment_fee} ({offer_note})"
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
        payment = parse_cardup(source_text)
        return format_cardup_payment(payment, variant)

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
