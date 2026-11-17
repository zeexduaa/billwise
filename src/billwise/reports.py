"""What the numbers say: who owes what, how late, what came in each month, and how much tax was charged."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .model import ZERO, Invoice
from .money import format_rate, round_money

AGING_BUCKETS = ("Not due yet", "1–30 days late", "31–60 days late", "61–90 days late", "Over 90 days late")


def bucket(days_late: int) -> int:
    if days_late <= 0:
        return 0
    return 1 if days_late <= 30 else 2 if days_late <= 60 else 3 if days_late <= 90 else 4


@dataclass
class Aging:
    currency: str
    buckets: list[Decimal] = field(default_factory=lambda: [ZERO] * len(AGING_BUCKETS))
    invoices: list[tuple[Invoice, int, Decimal]] = field(default_factory=list)  # invoice, days late, balance

    @property
    def total(self) -> Decimal:
        return sum(self.buckets, ZERO)


def open_invoices(invoices: list[Invoice], today: date) -> list[Invoice]:
    """Issued, not void, with money still owed: oldest due first."""
    return sorted((i for i in invoices if i.status(today) in ("unpaid", "partial", "overdue")), key=lambda i: (i.due_date, i.id or 0))


def aging(invoices: list[Invoice], today: date) -> dict[str, Aging]:
    """Money owed grouped by how late it is, one table per currency (amounts in different currencies never add up)."""
    out: dict[str, Aging] = {}
    for inv in open_invoices(invoices, today):
        late = (today - inv.due_date).days
        balance = inv.totals().balance
        row = out.setdefault(inv.currency, Aging(inv.currency))
        row.buckets[bucket(late)] += balance
        row.invoices.append((inv, max(late, 0), balance))
    return dict(sorted(out.items()))


def in_range(day: date, start: date | None, end: date | None) -> bool:
    return (start is None or day >= start) and (end is None or day <= end)


def monthly(invoices: list[Invoice], start: date | None = None, end: date | None = None) -> dict[str, dict[str, dict[str, Decimal]]]:
    """Per currency and month ("2026-03"): what was invoiced (by invoice date) and what was received (by payment date)."""
    out: dict[str, dict[str, dict[str, Decimal]]] = {}
    for inv in invoices:
        if inv.state != "issued":
            continue
        months = out.setdefault(inv.currency, {})
        if in_range(inv.issue_date, start, end):
            m = months.setdefault(inv.issue_date.strftime("%Y-%m"), {"invoiced": ZERO, "received": ZERO})
            m["invoiced"] += inv.totals().total
        for p in inv.payments:
            if in_range(p.date, start, end):
                m = months.setdefault(p.date.strftime("%Y-%m"), {"invoiced": ZERO, "received": ZERO})
                m["received"] += p.amount
    return {cur: dict(sorted(months.items())) for cur, months in sorted(out.items()) if months}


def tax_summary(invoices: list[Invoice], start: date | None = None, end: date | None = None) -> dict[str, dict[Decimal, dict[str, Decimal]]]:
    """Tax charged per rate on issued invoices dated in the period, for filling in a sales-tax or VAT return."""
    out: dict[str, dict[Decimal, dict[str, Decimal]]] = {}
    for inv in invoices:
        if inv.state != "issued" or not in_range(inv.issue_date, start, end):
            continue
        rates = out.setdefault(inv.currency, {})
        for line in inv.totals().taxes:
            r = rates.setdefault(line.rate, {"base": ZERO, "tax": ZERO})
            r["base"] += line.base
            r["tax"] += line.tax
    return {cur: dict(sorted(rates.items())) for cur, rates in sorted(out.items()) if rates}


def client_balances(invoices: list[Invoice], today: date) -> dict[tuple[int, str], Decimal]:
    out: dict[tuple[int, str], Decimal] = {}
    for inv in open_invoices(invoices, today):
        key = (inv.client_id, inv.currency)
        out[key] = out.get(key, ZERO) + inv.totals().balance
    return out


# -- CSV -----------------------------------------------------------------------------------------

def _cell(value: object) -> str:
    """Spreadsheet apps run cells starting with = + - @ as formulas; a client name like "=HYPERLINK(...)" must stay text."""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(text):
        return "'" + text
    return text


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except Exception:
        return False


def _csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows([[_cell(v) for v in row] for row in rows])
    return buf.getvalue()


def invoices_csv(invoices: list[Invoice], client_names: dict[int, str], today: date) -> str:
    rows = []
    for inv in sorted(invoices, key=lambda i: (i.issue_date, i.id or 0)):
        t = inv.totals()
        m = lambda x: round_money(x, inv.currency)  # noqa: E731 - same decimal places in every amount column
        rows.append([inv.label, inv.status(today), client_names.get(inv.client_id, ""), inv.issue_date, inv.due_date, inv.currency,
                     m(t.subtotal), m(t.discount), m(t.tax), m(t.total), m(t.paid), m(t.balance), inv.reference])
    return _csv(["number", "status", "client", "date", "due", "currency", "subtotal", "discount", "tax", "total", "paid", "balance",
                 "reference"], rows)


def payments_csv(invoices: list[Invoice], client_names: dict[int, str]) -> str:
    rows = [[p.date, inv.label, client_names.get(inv.client_id, ""), inv.currency, round_money(p.amount, inv.currency), p.method, p.note]
            for inv in invoices for p in inv.payments]
    rows.sort(key=lambda r: (str(r[0]), str(r[1])))
    return _csv(["date", "invoice", "client", "currency", "amount", "method", "note"], rows)


def tax_label(rate: Decimal) -> str:
    return format_rate(rate)
