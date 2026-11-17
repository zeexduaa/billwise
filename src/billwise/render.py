"""The invoice as a document: a standalone HTML page that prints cleanly on A4 or Letter (and "Save as PDF" in any browser)."""

from __future__ import annotations

from datetime import date
from html import escape

from .model import Client, Invoice
from .money import format_money, format_rate

STATUS_TEXT = {"draft": "Draft", "unpaid": "Unpaid", "partial": "Partly paid", "overdue": "Overdue", "paid": "Paid", "void": "Void"}


def e(value: object) -> str:
    return escape(str(value), quote=True)


def lines(text: str) -> str:
    """Multi-line text (an address) with its line breaks kept, and nothing in it treated as HTML."""
    return "<br>".join(e(part) for part in text.strip().splitlines())


def nice_date(day: date) -> str:
    return f"{day.day} {day.strftime('%b %Y')}"


def number_text(value) -> str:
    text = format(value.normalize(), "f")
    return text


INVOICE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #eceef1; color: #1d2330; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.sheet { background: #fff; max-width: 820px; margin: 24px auto; padding: 48px 56px; box-shadow: 0 2px 14px rgb(0 0 0 / 10%); position: relative; }
.head { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }
.from h1 { font-size: 22px; margin: 0 0 4px; }
.title { text-align: right; }
.title h2 { font-size: 28px; letter-spacing: .08em; margin: 0; color: #3b4a6b; font-weight: 700; }
.muted { color: #667085; }
.meta { margin-top: 28px; display: flex; justify-content: space-between; gap: 24px; }
.meta table td { padding: 1px 0 1px 16px; text-align: right; }
.meta table td:first-child { color: #667085; padding-left: 0; text-align: left; }
.label { text-transform: uppercase; font-size: 11px; letter-spacing: .08em; color: #667085; margin-bottom: 4px; font-weight: 600; }
table.items { width: 100%; border-collapse: collapse; margin-top: 30px; }
table.items th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #667085; border-bottom: 2px solid #1d2330; padding: 8px 6px; }
table.items td { border-bottom: 1px solid #e4e7ec; padding: 9px 6px; vertical-align: top; }
table.items .n { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
table.items td.desc { white-space: pre-line; }
.totals { margin-left: auto; margin-top: 14px; min-width: 300px; border-collapse: collapse; }
.totals td { padding: 4px 6px; text-align: right; font-variant-numeric: tabular-nums; }
.totals td:first-child { color: #667085; text-align: left; }
.totals tr.total td { border-top: 2px solid #1d2330; font-weight: 700; font-size: 16px; color: #1d2330; padding-top: 8px; }
.totals tr.due td { font-weight: 700; color: #1d2330; }
.notes { margin-top: 32px; display: grid; gap: 18px; }
.notes p { margin: 0; white-space: pre-line; }
.foot { margin-top: 40px; text-align: center; color: #667085; font-size: 12px; white-space: pre-line; }
.stamp { display: inline-block; margin-top: 14px; transform: rotate(-8deg); border: 3px solid currentColor; border-radius: 8px;
         padding: 2px 12px; font-size: 18px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; opacity: .6; }
.stamp.paid { color: #1f8a4c; } .stamp.void, .stamp.overdue { color: #c0392b; } .stamp.draft { color: #8a8f99; }
.toolbar { max-width: 820px; margin: 16px auto 0; display: flex; gap: 8px; justify-content: flex-end; }
.toolbar a, .toolbar button { font: inherit; padding: 7px 14px; border-radius: 8px; border: 1px solid #c9ced6; background: #fff; color: #1d2330; text-decoration: none; cursor: pointer; }
@media (max-width: 700px) { .sheet { padding: 24px 18px; margin: 0; } .head, .meta { flex-direction: column; } .title { text-align: left; }
  .totals { min-width: 0; width: 100%; } }
@media print {
  body { background: #fff; } .sheet { box-shadow: none; margin: 0; max-width: none; padding: 0; } .toolbar { display: none; }
  @page { margin: 16mm; } tr { break-inside: avoid; }
}
"""


def invoice_document(inv: Invoice, client: Client, settings: dict[str, str], today: date, *, toolbar: str = "") -> str:
    cur = inv.currency
    t = inv.totals()
    status = inv.status(today)
    money = lambda amount: e(format_money(amount, cur))  # noqa: E731
    any_tax = any(item.tax_rate for item in inv.items)
    rows = "".join(
        f"<tr><td class='desc'>{e(item.description)}</td><td class='n'>{e(number_text(item.quantity))}</td>"
        f"<td class='n'>{money(item.unit_price)}</td>"
        + (f"<td class='n'>{e(format_rate(item.tax_rate)) if item.tax_rate else '–'}</td>" if any_tax else "")
        + f"<td class='n'>{money(item.amount(cur))}</td></tr>"
        for item in inv.items)
    totals = [f"<tr><td>Subtotal</td><td>{money(t.subtotal)}</td></tr>"]
    if t.discount:
        what = f"Discount ({format_rate(inv.discount_value)})" if inv.discount_type == "percent" else "Discount"
        totals.append(f"<tr><td>{e(what)}</td><td>−{money(t.discount)}</td></tr>")
    for line in t.taxes:
        totals.append(f"<tr><td>Tax {e(format_rate(line.rate))} on {money(line.base)}</td><td>{money(line.tax)}</td></tr>")
    totals.append(f"<tr class='total'><td>Total</td><td>{money(t.total)}</td></tr>")
    if t.paid:
        totals.append(f"<tr><td>Paid</td><td>−{money(t.paid)}</td></tr>")
        totals.append(f"<tr class='due'><td>Balance due</td><td>{money(t.balance)}</td></tr>")
    stamp = f"<div class='stamp {e(status)}'>{e(STATUS_TEXT[status])}</div>" if status in ("paid", "void", "overdue", "draft") else ""
    business = settings.get("business_name") or "Your business name"
    from_block = "".join([
        f"<h1>{e(business)}</h1>",
        f"<div class='muted'>{lines(settings.get('business_address', ''))}</div>" if settings.get("business_address") else "",
        f"<div class='muted'>{e(settings['business_email'])}</div>" if settings.get("business_email") else "",
        f"<div class='muted'>Tax ID: {e(settings['business_tax_id'])}</div>" if settings.get("business_tax_id") else "",
    ])
    to_block = "".join([
        f"<strong>{e(client.name)}</strong>",
        f"<div>{lines(client.address)}</div>" if client.address else "",
        f"<div class='muted'>{e(client.email)}</div>" if client.email else "",
        f"<div class='muted'>Tax ID: {e(client.tax_id)}</div>" if client.tax_id else "",
    ])
    meta = [("Invoice", inv.label), ("Date", nice_date(inv.issue_date)), ("Due", nice_date(inv.due_date))]
    if inv.reference:
        meta.append(("Reference", inv.reference))
    notes = []
    if inv.notes:
        notes.append(f"<div><div class='label'>Notes</div><p>{e(inv.notes)}</p></div>")
    if settings.get("payment_details") and status not in ("paid", "void"):
        notes.append(f"<div><div class='label'>How to pay</div><p>{e(settings['payment_details'])}</p></div>")
    head = (f"<tr><th>Description</th><th class='n'>Qty</th><th class='n'>Price</th>"
            + ("<th class='n'>Tax</th>" if any_tax else "") + "<th class='n'>Amount</th></tr>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(inv.label)} · {e(client.name)}</title><style>{INVOICE_CSS}</style></head>
<body>{toolbar}<main class="sheet">
<div class="head"><div class="from">{from_block}</div><div class="title"><h2>INVOICE</h2><div class="muted">{e(inv.label)}</div>{stamp}</div></div>
<div class="meta"><div><div class="label">Bill to</div>{to_block}</div>
<table>{''.join(f'<tr><td>{e(k)}</td><td>{e(v)}</td></tr>' for k, v in meta)}
<tr><td><strong>Amount due</strong></td><td><strong>{money(t.balance if inv.state == 'issued' else t.total)}</strong></td></tr></table></div>
<table class="items"><thead>{head}</thead><tbody>{rows or "<tr><td colspan='5' class='muted'>No lines yet.</td></tr>"}</tbody></table>
<table class="totals">{''.join(totals)}</table>
<div class="notes">{''.join(notes)}</div>
<div class="foot">{e(settings.get('footer', ''))}</div>
</main></body></html>
"""
