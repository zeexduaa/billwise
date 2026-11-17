"""Money: exact decimal amounts, rounding to a currency's minor unit, parsing what people type, formatting.

Amounts are never floats. 0.1 + 0.2 must be 0.3 on an invoice, and a total must equal the sum of what's printed.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Currencies whose minor unit isn't cents (ISO 4217). Everything else uses 2 decimal places.
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}

SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "PKR": "Rs ", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$",
    "CNY": "CN¥", "KRW": "₩", "BRL": "R$", "MXN": "MX$", "ZAR": "R ", "NGN": "₦", "SGD": "S$", "HKD": "HK$", "TRY": "₺",
    "BDT": "৳", "PHP": "₱", "ILS": "₪", "THB": "฿", "UAH": "₴", "VND": "₫", "KES": "KSh ", "EGP": "E£",
}

LIMIT = Decimal("1e12")  # no invoice line needs a trillion; a bigger number is a typo


class MoneyError(ValueError):
    """Something typed isn't a usable amount, quantity, rate or currency."""


def check_currency(code: str) -> str:
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise MoneyError(f"currency should be a 3-letter code like USD, EUR or PKR, not {code!r}")
    return code


def places(currency: str) -> int:
    return 0 if currency in ZERO_DECIMAL else 3 if currency in THREE_DECIMAL else 2


def round_money(amount: Decimal, currency: str) -> Decimal:
    """Round half away from zero to the currency's minor unit, as accountants (and tax offices) expect."""
    return amount.quantize(Decimal(1).scaleb(-places(currency)), rounding=ROUND_HALF_UP)


_GROUPED = re.compile(r"\d{1,3}(,\d{3})+(\.\d*)?")


def parse_decimal(text: str | int | float | Decimal, what: str = "amount", *, negative: bool = False) -> Decimal:
    """Read a number someone typed: "1,234.50", "$12", "12.5%", " 3 ". Commas are only accepted as thousands
    separators ("1,234"), because "1,5" could mean one and a half or fifteen."""
    if isinstance(text, Decimal):
        value = text
    elif isinstance(text, (int, float)) and not isinstance(text, bool):
        value = Decimal(str(text))
    else:
        raw = str(text).strip().replace(" ", "").replace(" ", "")
        sign = ""
        if raw[:1] in "-−":
            sign, raw = "-", raw[1:]
        raw = re.sub(r"^[^\d.,\-−]+", "", raw)       # a currency symbol or code in front
        raw = raw.removesuffix("%")
        if raw[:1] in "-−" and not sign:             # "$-5"
            sign, raw = "-", raw[1:]
        if "," in raw:
            if not _GROUPED.fullmatch(raw):
                raise MoneyError(f"{what}: use a dot for decimals and commas only between thousands (e.g. 1,234.50), not {text!r}")
            raw = raw.replace(",", "")
        if not re.fullmatch(r"\d+(\.\d*)?|\.\d+", raw):
            raise MoneyError(f"{what} should be a number, not {text!r}")
        try:
            value = Decimal(sign + raw)
        except InvalidOperation as err:  # pragma: no cover - the pattern above already rules this out
            raise MoneyError(f"{what} should be a number, not {text!r}") from err
    if not value.is_finite():
        raise MoneyError(f"{what} should be a number")
    if abs(value) >= LIMIT:
        raise MoneyError(f"{what} is too large")
    if value < 0 and not negative:
        raise MoneyError(f"{what} can't be negative")
    return value + 0  # turns -0 into 0


def format_money(amount: Decimal, currency: str, *, symbol: bool = True) -> str:
    """1234.5, "USD" -> "$1,234.50"; currencies without a known symbol show their code: "CHF 12.00"."""
    value = round_money(amount, currency)
    body = f"{abs(value):,.{places(currency)}f}"
    prefix = SYMBOLS.get(currency, f"{currency} ") if symbol else ""
    return f"{'-' if value < 0 else ''}{prefix}{body}"


def format_rate(rate: Decimal) -> str:
    """10.000 -> "10%", 7.25 -> "7.25%"."""
    text = format(rate.normalize(), "f")
    return f"{text}%"
