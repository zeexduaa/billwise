from datetime import date
from decimal import Decimal as D

import pytest

from billwise.model import Client, Invoice, InvoiceError, Item, Payment

TODAY = date(2026, 6, 15)


def invoice(*items, currency="USD", discount=("none", "0"), state="issued", due=date(2026, 6, 30), payments=()):
    inv = Invoice(client_id=1, currency=currency, issue_date=date(2026, 6, 1), due_date=due, state=state,
                  items=list(items), payments=list(payments), discount_type=discount[0], discount_value=D(discount[1]))
    inv.id = 1
    return inv


def test_simple_total():
    t = invoice(Item("Design", D(2), D("150"))).totals()
    assert (t.subtotal, t.tax, t.total, t.balance) == (D("300"), 0, D("300"), D("300"))


def test_each_line_is_rounded_before_adding():
    # 3 x 0.333 = 0.999 -> 1.00 on the line; the total must match what's printed on the lines.
    t = invoice(Item("a", D(3), D("0.333")), Item("b", D(3), D("0.333"))).totals()
    assert t.subtotal == D("2.00")


def test_tax_per_rate():
    t = invoice(Item("Service", D(1), D("100"), D(17)), Item("Book", D(1), D("50"), D(0)), Item("Other", D(1), D("10"), D(17))).totals()
    assert [(x.rate, x.base, x.tax) for x in t.taxes] == [(D(17), D("110"), D("18.70"))]
    assert t.total == D("178.70")


def test_percent_discount_is_shared_between_tax_rates():
    t = invoice(Item("Web", D(1), D("1200")), Item("Hosting", D(12), D("15.50"), D(17)), discount=("percent", "10")).totals()
    assert t.subtotal == D("1386.00")
    assert t.discount == D("138.60")
    assert t.taxes[0].base == D("167.40")
    assert t.taxes[0].tax == D("28.46")
    assert t.total == t.subtotal - t.discount + t.tax == D("1275.86")


def test_discount_shares_always_add_up():
    # Three equal groups and a discount that doesn't divide evenly: no cent may be lost or invented.
    t = invoice(Item("a", D(1), D("10"), D(5)), Item("b", D(1), D("10"), D(10)), Item("c", D(1), D("10"), D(20)),
                discount=("amount", "10")).totals()
    assert sum(x.base for x in t.taxes) == D("20")
    assert t.total == D("20") + t.tax


def test_amount_discount_never_exceeds_subtotal():
    t = invoice(Item("a", D(1), D("30")), discount=("amount", "50")).totals()
    assert t.discount == D("30") and t.total == 0


def test_zero_decimal_currency():
    t = invoice(Item("a", D(1), D("999.6"), D(10)), currency="JPY").totals()
    assert t.subtotal == D("1000") and t.tax == D("100") and t.total == D("1100")


def test_credit_line():
    t = invoice(Item("Work", D(1), D("500")), Item("Credit for last month", D(1), D("-50"))).totals()
    assert t.total == D("450")


def test_status():
    lines = [Item("x", D(1), D("100"))]
    assert invoice(*lines, state="draft").status(TODAY) == "draft"
    assert invoice(*lines, state="void").status(TODAY) == "void"
    assert invoice(*lines).status(TODAY) == "unpaid"
    assert invoice(*lines, payments=[Payment(TODAY, D("40"))]).status(TODAY) == "partial"
    assert invoice(*lines, payments=[Payment(TODAY, D("100"))]).status(TODAY) == "paid"
    late = invoice(*lines, due=date(2026, 6, 10))
    assert late.status(TODAY) == "overdue" and late.days_overdue(TODAY) == 5
    assert invoice(*lines, due=TODAY).status(TODAY) == "unpaid"  # due today isn't late yet


def test_item_checks():
    with pytest.raises(InvoiceError):
        Item("  ", D(1), D(1)).validate()
    with pytest.raises(InvoiceError):
        Item("x", D(0), D(1)).validate()
    with pytest.raises(InvoiceError):
        Item("x", D(1), D(1), D(101)).validate()


def test_client_checks():
    assert Client("  Acme  ").validate().name == "Acme"
    with pytest.raises(InvoiceError):
        Client("").validate()
    with pytest.raises(InvoiceError):
        Client("Acme", email="not an email").validate()
