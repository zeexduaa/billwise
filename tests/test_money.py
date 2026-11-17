from decimal import Decimal

import pytest

from billwise.money import MoneyError, check_currency, format_money, format_rate, parse_decimal, round_money


@pytest.mark.parametrize("text, expected", [
    ("12", "12"), ("12.50", "12.50"), ("1,234.56", "1234.56"), ("$1,200", "1200"), ("Rs 5,000", "5000"), (" 3 ", "3"),
    (".5", "0.5"), ("17%", "17"), ("1,234,567.8", "1234567.8"), ("€0.10", "0.10"),
])
def test_parse_what_people_type(text, expected):
    assert parse_decimal(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "1,5", "12,34.5", "1.2.3", "1e5", "nan", "inf", "--5", "1,23"])
def test_reject_unclear_numbers(text):
    with pytest.raises(MoneyError):
        parse_decimal(text)


def test_negative_only_when_allowed():
    with pytest.raises(MoneyError):
        parse_decimal("-5")
    assert parse_decimal("-5", negative=True) == -5
    assert parse_decimal("$-5", negative=True) == -5
    assert parse_decimal("−5", negative=True) == -5  # the typographic minus sign


def test_huge_numbers_are_typos():
    with pytest.raises(MoneyError):
        parse_decimal("1000000000000")


def test_exact_arithmetic():
    assert parse_decimal("0.1") + parse_decimal("0.2") == Decimal("0.3")


def test_rounding_is_half_up_per_currency():
    assert round_money(Decimal("2.345"), "USD") == Decimal("2.35")
    assert round_money(Decimal("-2.345"), "USD") == Decimal("-2.35")
    assert round_money(Decimal("1234.5"), "JPY") == Decimal("1235")
    assert round_money(Decimal("1.2345"), "KWD") == Decimal("1.235")


def test_formatting():
    assert format_money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert format_money(Decimal("-5"), "EUR") == "-€5.00"
    assert format_money(Decimal("1500"), "JPY") == "¥1,500"
    assert format_money(Decimal("12"), "CHF") == "CHF 12.00"
    assert format_money(Decimal("2500"), "PKR") == "Rs 2,500.00"
    assert format_rate(Decimal("17.000")) == "17%"
    assert format_rate(Decimal("7.25")) == "7.25%"
    assert format_rate(Decimal("10")) == "10%"


def test_currency_codes():
    assert check_currency(" usd ") == "USD"
    for bad in ("US", "dollars", "U5D", ""):
        with pytest.raises(MoneyError):
            check_currency(bad)
