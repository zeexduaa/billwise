"""Clients, invoices, line items and payments, and the arithmetic that turns them into totals and a status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import MoneyError, round_money

ZERO = Decimal(0)
HUNDRED = Decimal(100)


class InvoiceError(ValueError):
    """An action that doesn't make sense for this invoice (editing a sent one, paying a draft, ...)."""


@dataclass
class Client:
    name: str
    email: str = ""
    address: str = ""
    tax_id: str = ""
    notes: str = ""
    archived: bool = False
    id: int | None = None

    def validate(self) -> "Client":
        self.name = self.name.strip()
        self.email = self.email.strip()
        if not self.name:
            raise InvoiceError("a client needs a name")
        if len(self.name) > 200:
            raise InvoiceError("the client's name is too long")
        if self.email and ("@" not in self.email or " " in self.email or len(self.email) > 254):
            raise InvoiceError(f"{self.email!r} doesn't look like an email address")
        return self


@dataclass
class Item:
    description: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal = ZERO
    tax_rate: Decimal = ZERO  # percent, e.g. 17 for 17%

    def amount(self, currency: str) -> Decimal:
        return round_money(self.quantity * self.unit_price, currency)

    def validate(self) -> "Item":
        self.description = self.description.strip()
        if not self.description:
            raise InvoiceError("every line needs a description")
        if self.quantity <= 0:
            raise InvoiceError(f"the quantity for {self.description!r} must be more than zero")
        if not ZERO <= self.tax_rate <= HUNDRED:
            raise InvoiceError(f"the tax rate for {self.description!r} must be between 0 and 100%")
        return self


@dataclass
class Payment:
    date: date
    amount: Decimal
    method: str = ""
    note: str = ""
    id: int | None = None


@dataclass
class TaxLine:
    rate: Decimal
    base: Decimal   # what the rate is charged on, after the discount
    tax: Decimal


@dataclass
class Totals:
    subtotal: Decimal
    discount: Decimal
    taxes: list[TaxLine]
    tax: Decimal
    total: Decimal
    paid: Decimal
    balance: Decimal


@dataclass
class Invoice:
    client_id: int
    currency: str
    issue_date: date
    due_date: date
    items: list[Item] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    state: str = "draft"            # draft -> issued -> (void); "paid" and "overdue" are worked out, not stored
    number: str | None = None       # given when the invoice is issued, so numbers have no gaps
    discount_type: str = "none"     # none | percent | amount
    discount_value: Decimal = ZERO
    reference: str = ""             # the client's PO number or similar
    notes: str = ""
    id: int | None = None
    created: str = ""
    issued_at: str = ""
    kind: str = "invoice"           # invoice | quote (for a quote, due_date is the date the offer is valid until)
    outcome: str = ""               # quotes only: "", accepted or declined
    converted_to: int | None = None # quotes only: the invoice made from it

    @property
    def is_quote(self) -> bool:
        return self.kind == "quote"

    @property
    def label(self) -> str:
        return self.number or (f"Draft quote {self.id}" if self.is_quote else f"Draft {self.id}")

    def totals(self) -> Totals:
        return compute_totals(self)

    def status(self, today: date) -> str:
        return status_of(self, today)

    def days_overdue(self, today: date) -> int:
        return (today - self.due_date).days if self.status(today) == "overdue" else 0


def discount_amount(inv: Invoice, subtotal: Decimal) -> Decimal:
    if inv.discount_type == "percent":
        return round_money(subtotal * inv.discount_value / HUNDRED, inv.currency)
    if inv.discount_type == "amount":
        return min(round_money(inv.discount_value, inv.currency), max(subtotal, ZERO))
    return ZERO


def compute_totals(inv: Invoice) -> Totals:
    """Lines are rounded first, then the discount comes off the subtotal, shared between the tax rates in proportion
    to their lines, and each rate's tax is rounded once. So the printed tax lines always add up to the printed total."""
    cur = inv.currency
    groups: dict[Decimal, Decimal] = {}
    for item in inv.items:
        rate = item.tax_rate.normalize() + 0
        groups[rate] = groups.get(rate, ZERO) + item.amount(cur)
    subtotal = sum(groups.values(), ZERO)
    discount = discount_amount(inv, subtotal)
    taxes: list[TaxLine] = []
    remaining = discount
    rates = sorted(groups)
    for i, rate in enumerate(rates):
        gross = groups[rate]
        share = remaining if i == len(rates) - 1 else round_money(discount * gross / subtotal, cur) if subtotal else ZERO
        remaining -= share
        base = gross - share
        if rate:
            taxes.append(TaxLine(rate, base, round_money(base * rate / HUNDRED, cur)))
    tax = sum((t.tax for t in taxes), ZERO)
    total = subtotal - discount + tax
    paid = sum((p.amount for p in inv.payments), ZERO)
    return Totals(subtotal, discount, taxes, tax, total, paid, total - paid)


def status_of(inv: Invoice, today: date) -> str:
    if inv.state in ("draft", "void"):
        return inv.state
    t = inv.totals()
    if t.balance <= 0:
        return "paid"
    if inv.due_date < today:
        return "overdue"
    return "partial" if t.paid > 0 else "unpaid"


STATUSES = ("draft", "unpaid", "partial", "overdue", "paid", "void")


def check_discount(kind: str, value: Decimal) -> None:
    if kind not in ("none", "percent", "amount"):
        raise InvoiceError("the discount should be none, a percent or an amount")
    if kind == "percent" and not ZERO <= value <= HUNDRED:
        raise InvoiceError("a percent discount must be between 0 and 100")
    if value < 0:
        raise MoneyError("a discount can't be negative")
