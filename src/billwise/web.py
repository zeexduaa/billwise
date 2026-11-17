"""billwise in the browser: a small web app served from your own computer (127.0.0.1 only), using just the standard library.

`App` turns a request into a response and knows nothing about sockets, so it's easy to test; `serve` wraps it in an HTTP server.
"""

from __future__ import annotations

import re
import secrets
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import __version__, reports
from .model import STATUSES, Client, Invoice, InvoiceError, Item, Payment
from .money import MoneyError, format_money, format_rate, parse_decimal
from .render import STATUS_TEXT, e, invoice_document, nice_date, number_text
from .store import DEFAULTS, Store

MAX_BODY = 1_000_000


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def html(cls, text: str, status: int = 200) -> "Response":
        return cls(status, text.encode(), {"Content-Type": "text/html; charset=utf-8"})

    @classmethod
    def redirect(cls, to: str) -> "Response":
        return cls(303, b"", {"Location": to})


class Problem(Exception):
    """Something the person typed or clicked can't be done; shown to them, never a crash."""


def parse_date(text: str, what: str) -> date:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        raise Problem(f"{what} should be a date like 2026-03-31") from None


def money(amount: Decimal, currency: str) -> str:
    return e(format_money(amount, currency))


def month_name(key: str) -> str:
    """"2026-09" -> "Sep 2026"."""
    return date.fromisoformat(key + "-01").strftime("%b %Y")


def pill(status: str) -> str:
    return f"<span class='pill {e(status)}'>{e(STATUS_TEXT[status])}</span>"


APP_CSS = """
:root { --bg:#f5f6f8; --surface:#fff; --text:#1d2330; --muted:#667085; --line:#e2e5ea; --accent:#3557c8; --accent-ink:#fff;
        --soft:#eaf0ff; --ok:#1f8a4c; --warn:#b4610e; --bad:#c0392b; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --surface:#1c2027; --text:#e8ebf0; --muted:#98a2b3; --line:#2d333d;
        --accent:#7c9bff; --accent-ink:#0d1220; --soft:#1f2a47; --ok:#4cc483; --warn:#f0a35a; --bad:#f07468; color-scheme: dark; } }
* { box-sizing: border-box; }
[hidden] { display: none !important; } /* the phone layout gives rows display:block, which beat the hidden attribute */
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); }
header { background: var(--surface); border-bottom: 1px solid var(--line); }
header .in { max-width: 1100px; margin: 0 auto; padding: .7rem 1rem; display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.brand { font-weight: 800; font-size: 1.15rem; color: var(--text); text-decoration: none; margin-right: auto; }
nav { display: flex; gap: .2rem; flex-wrap: wrap; }
nav a { color: var(--muted); text-decoration: none; padding: .4rem .8rem; border-radius: 8px; font-weight: 600; }
nav a.on { background: var(--soft); color: var(--accent); }
main { max-width: 1100px; margin: 0 auto; padding: 1.4rem 1rem 4rem; }
h1 { font-size: 1.6rem; margin: 0 0 1rem; } h2 { font-size: 1.15rem; margin: 1.6rem 0 .6rem; }
.row { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.spread { justify-content: space-between; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.1rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: .8rem; }
.big { font-size: 1.5rem; font-weight: 750; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); } .small { font-size: .875rem; }
.btn { display: inline-flex; align-items: center; gap: .3rem; padding: .5rem .95rem; border-radius: 8px; border: 1px solid var(--line);
       background: var(--surface); color: var(--text); font: inherit; font-weight: 600; cursor: pointer; text-decoration: none; white-space: nowrap; }
.btn:hover { border-color: var(--accent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.btn.danger { color: var(--bad); } .btn.small { padding: .3rem .65rem; font-size: .875rem; }
form.inline { display: inline; }
table.list { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
table.list th, table.list td { padding: .6rem .75rem; text-align: left; border-bottom: 1px solid var(--line); }
table.list th { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); background: color-mix(in srgb, var(--surface) 92%, var(--text)); }
table.list tr:last-child td { border-bottom: 0; }
table.list .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.scroll { overflow-x: auto; }
@media (max-width: 640px) { table.list td, table.list th { white-space: nowrap; padding: .55rem .6rem; } .hide-sm { display: none; } }
.pill { display: inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .8rem; font-weight: 700; background: var(--soft); color: var(--accent); }
.pill.paid { background: color-mix(in srgb, var(--ok) 16%, transparent); color: var(--ok); }
.pill.overdue, .pill.void { background: color-mix(in srgb, var(--bad) 14%, transparent); color: var(--bad); }
.pill.partial { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.pill.draft { background: color-mix(in srgb, var(--muted) 18%, transparent); color: var(--muted); }
label { display: block; font-weight: 600; margin-bottom: .25rem; font-size: .92rem; }
input, select, textarea { font: inherit; color: inherit; width: 100%; padding: .5rem .65rem; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
textarea { min-height: 4.5rem; resize: vertical; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .9rem; }
.field { margin-bottom: .9rem; } .hint { color: var(--muted); font-weight: 400; font-size: .84rem; }
.flash { background: color-mix(in srgb, var(--ok) 14%, var(--surface)); border: 1px solid color-mix(in srgb, var(--ok) 40%, transparent); padding: .6rem .9rem; border-radius: 10px; margin-bottom: 1rem; }
.error { background: color-mix(in srgb, var(--bad) 12%, var(--surface)); border: 1px solid color-mix(in srgb, var(--bad) 40%, transparent); padding: .6rem .9rem; border-radius: 10px; margin-bottom: 1rem; }
table.items { width: 100%; border-collapse: collapse; }
table.items th { text-align: left; font-size: .78rem; text-transform: uppercase; color: var(--muted); padding: .3rem .3rem; }
table.items td { padding: .25rem .3rem; vertical-align: top; }
table.items td.qty { width: 6rem; } table.items td.price { width: 9rem; } table.items td.tax { width: 6.5rem; } table.items td.x { width: 2.5rem; }
.chips { display: flex; gap: .35rem; flex-wrap: wrap; margin-bottom: 1rem; }
.chips a { padding: .3rem .75rem; border-radius: 999px; border: 1px solid var(--line); text-decoration: none; color: var(--text); font-size: .9rem; background: var(--surface); }
.chips a.on { background: var(--soft); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.empty { text-align: center; padding: 2.5rem 1rem; color: var(--muted); }
iframe.preview { width: 100%; height: 70rem; border: 1px solid var(--line); border-radius: 12px; background: #eceef1; }
.side { display: grid; grid-template-columns: minmax(0, 1fr) 20rem; gap: 1.2rem; align-items: start; }
@media (max-width: 860px) { .side { grid-template-columns: 1fr; } iframe.preview { height: 48rem; }
  table.items, table.items tbody, table.items tr, table.items td { display: block; width: auto !important; }
  table.items thead { display: none; } table.items tr { border: 1px solid var(--line); border-radius: 10px; padding: .4rem; margin-bottom: .6rem; }
  table.items td[data-label]::before { content: attr(data-label); display: block; font-size: .78rem; color: var(--muted); } }
footer { text-align: center; color: var(--muted); font-size: .8rem; padding: 2rem 1rem; }
"""

APP_JS = """
// Add another line to the invoice editor, and ask before anything that can't be undone.
document.addEventListener('click', (ev) => {
  const add = ev.target.closest('[data-add-line]');
  if (add) {
    const body = document.querySelector('#lines');
    const row = document.querySelector('#line-template').content.firstElementChild.cloneNode(true);
    body.appendChild(row);
    row.querySelector('input,textarea').focus();
  }
  const remove = ev.target.closest('[data-remove-line]');
  if (remove) {
    const row = remove.closest('tr');
    for (const input of row.querySelectorAll('input,textarea')) input.value = '';
    row.hidden = true;
  }
  const print = ev.target.closest('[data-print]');
  if (print) window.print();
});
document.addEventListener('submit', (ev) => {
  const message = ev.target.dataset.confirm;
  if (message && !confirm(message)) ev.preventDefault();
});
"""


class App:
    def __init__(self, store: Store, *, today=date.today, allowed_hosts: set[str] | None = None):
        self.store = store
        self.today = today
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = allowed_hosts  # None: don't check (tests); the server sets the real ones
        self.flash = ""
        self.lock = threading.Lock()

    # -- plumbing --------------------------------------------------------------------------------

    def request(self, method: str, target: str, form: dict[str, list[str]] | None = None, headers: dict[str, str] | None = None) -> Response:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if self.allowed_hosts is not None and headers.get("host", "") not in self.allowed_hosts:
            # Stops other websites reaching this server through DNS tricks ("DNS rebinding").
            return Response(421, b"Unknown host", {"Content-Type": "text/plain"})
        url = urlsplit(target)
        query = parse_qs(url.query, keep_blank_values=True)
        form = form or {}
        if method == "POST":
            origin = headers.get("origin")
            # "null" is what browsers send from pages with a strict referrer policy; the form token still protects those.
            if self.allowed_hosts is not None and origin and origin != "null" and urlsplit(origin).netloc not in self.allowed_hosts:
                return Response(403, b"Forbidden", {"Content-Type": "text/plain"})
            if not secrets.compare_digest(self.one(form, "csrf"), self.token):
                return Response(403, b"This form has expired. Go back, reload the page and try again.", {"Content-Type": "text/plain; charset=utf-8"})
        with self.lock:
            try:
                response = self.route(method, url.path, query, form)
            except (InvoiceError, MoneyError, Problem) as err:
                response = self.page("Can't do that", f"<div class='error'>{e(err)}</div><p class='muted'>Use your browser's Back button to return, or <a href='/'>go to the dashboard</a>.</p>", status=400)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "SAMEORIGIN",
        })
        return response

    @staticmethod
    def one(values: dict[str, list[str]], key: str, default: str = "") -> str:
        got = values.get(key)
        return got[0] if got else default

    def csrf(self) -> str:
        return f"<input type='hidden' name='csrf' value='{e(self.token)}'>"

    def button(self, action: str, label: str, cls: str = "btn", confirm: str = "", extra: str = "") -> str:
        ask = f" data-confirm='{e(confirm)}'" if confirm else ""
        return f"<form class='inline' method='post' action='{e(action)}'{ask}>{self.csrf()}{extra}<button class='{e(cls)}'>{e(label)}</button></form>"

    def done(self, message: str, to: str) -> Response:
        self.flash = message
        return Response.redirect(to)

    def page(self, title: str, body: str, *, tab: str = "", status: int = 200) -> Response:
        name = self.store.setting("business_name") or "billwise"
        tabs = [("/", "Dashboard", "home"), ("/invoices", "Invoices", "invoices"), ("/clients", "Clients", "clients"),
                ("/reports", "Reports", "reports"), ("/settings", "Settings", "settings")]
        nav = "".join(f"<a href='{href}' class='{'on' if key == tab else ''}'>{label}</a>" for href, label, key in tabs)
        flash, self.flash = self.flash, ""
        return Response.html(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} · {e(name)}</title>
<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%233557c8'/%3E%3Cpath d='M9 8h14M9 14h14M9 20h8' stroke='white' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E">
</head><body><header><div class="in"><a class="brand" href="/">{e(name)}</a><nav>{nav}</nav></div></header>
<main>{f"<div class='flash' role='status'>{e(flash)}</div>" if flash else ""}{body}</main>
<footer>billwise {e(__version__)} · your data stays on this computer</footer></body></html>""", status)

    def route(self, method: str, path: str, query: dict[str, list[str]], form: dict[str, list[str]]) -> Response:
        if path == "/static/app.css":
            return Response(200, APP_CSS.encode(), {"Content-Type": "text/css; charset=utf-8", "Cache-Control": "max-age=300"})
        if path == "/static/app.js":
            return Response(200, APP_JS.encode(), {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "max-age=300"})
        routes = [
            ("GET", r"/", self.dashboard),
            ("GET", r"/invoices", self.invoice_list),
            ("GET", r"/invoices/new", self.invoice_new_form),
            ("POST", r"/invoices/new", self.invoice_create),
            ("GET", r"/invoices/(\d+)", self.invoice_view),
            ("GET", r"/invoices/(\d+)/edit", self.invoice_edit_form),
            ("POST", r"/invoices/(\d+)/edit", self.invoice_save),
            ("GET", r"/invoices/(\d+)/print", self.invoice_print),
            ("POST", r"/invoices/(\d+)/issue", self.invoice_issue),
            ("POST", r"/invoices/(\d+)/void", self.invoice_void),
            ("POST", r"/invoices/(\d+)/delete", self.invoice_delete),
            ("POST", r"/invoices/(\d+)/duplicate", self.invoice_duplicate),
            ("POST", r"/invoices/(\d+)/payments", self.payment_add),
            ("POST", r"/invoices/(\d+)/payments/(\d+)/delete", self.payment_delete),
            ("GET", r"/clients", self.client_list),
            ("GET", r"/clients/new", self.client_new_form),
            ("POST", r"/clients/new", self.client_create),
            ("GET", r"/clients/(\d+)", self.client_view),
            ("POST", r"/clients/(\d+)", self.client_save),
            ("POST", r"/clients/(\d+)/archive", self.client_archive),
            ("POST", r"/clients/(\d+)/delete", self.client_delete),
            ("GET", r"/reports", self.reports_page),
            ("GET", r"/export/invoices.csv", self.export_invoices),
            ("GET", r"/export/payments.csv", self.export_payments),
            ("GET", r"/settings", self.settings_form),
            ("POST", r"/settings", self.settings_save),
            ("GET", r"/backup", self.backup),
        ]
        known_path = False
        for verb, pattern, handler in routes:
            m = re.fullmatch(pattern, path)
            if m:
                known_path = True
                if verb == method:
                    return handler(query, form, *(int(g) for g in m.groups()))
        if known_path:
            return Response(405, b"Method not allowed", {"Content-Type": "text/plain"})
        return self.page("Not found", "<div class='empty'><h1>There's no such page</h1><a class='btn' href='/'>Go to the dashboard</a></div>", status=404)

    def client_names(self) -> dict[int, str]:
        return {c.id: c.name for c in self.store.clients(archived=True)}  # type: ignore[misc]

    def invoice_table(self, invoices: list[Invoice], names: dict[int, str], *, client_column: bool = True) -> str:
        today = self.today()
        if not invoices:
            return "<p class='muted'>Nothing here.</p>"
        rows = []
        for inv in invoices:
            t = inv.totals()
            rows.append(f"<tr><td><a href='/invoices/{inv.id}'>{e(inv.label)}</a></td>"
                        + (f"<td>{e(names.get(inv.client_id, '?'))}</td>" if client_column else "")
                        + f"<td class='hide-sm'>{e(nice_date(inv.issue_date))}</td><td>{e(nice_date(inv.due_date))}</td>"
                        f"<td class='n'>{money(t.total, inv.currency)}</td><td class='n'>{money(t.balance, inv.currency) if inv.state == 'issued' else '–'}</td>"
                        f"<td>{pill(inv.status(today))}</td></tr>")
        head = "<tr><th>Invoice</th>" + ("<th>Client</th>" if client_column else "") + \
               "<th class='hide-sm'>Date</th><th>Due</th><th class='n'>Total</th><th class='n'>Owed</th><th>Status</th></tr>"
        return f"<div class='scroll'><table class='list'><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"

    # -- dashboard -------------------------------------------------------------------------------

    def dashboard(self, query, form) -> Response:
        today = self.today()
        settings = self.store.settings()
        clients = self.store.clients()
        invoices = self.store.invoices()
        names = self.client_names()
        steps = []
        if not settings["business_name"]:
            steps.append("<a class='btn primary' href='/settings'>1. Add your business details</a>")
        if not clients:
            steps.append("<a class='btn" + (" primary" if not steps else "") + "' href='/clients/new'>2. Add your first client</a>")
        if not invoices:
            steps.append("<a class='btn' href='/invoices/new'>3. Create an invoice</a>")
        intro = (f"<div class='card' style='margin-bottom:1.2rem'><h2 style='margin-top:0'>Welcome to billwise</h2>"
                 f"<p class='muted'>Three steps to your first invoice:</p><div class='row'>{''.join(steps)}</div></div>") if steps else ""
        aging = reports.aging(invoices, today)
        month = today.strftime("%Y-%m")
        received = {cur: months.get(month, {}).get("received", Decimal(0)) for cur, months in reports.monthly(invoices).items()}
        cards = []
        for cur, row in aging.items():
            overdue = sum(row.buckets[1:], Decimal(0))
            cards.append(f"<div class='card'><div class='muted small'>Owed to you ({e(cur)})</div><div class='big'>{money(row.total, cur)}</div>"
                         f"<div class='small {'muted' if not overdue else ''}' style='color:{'var(--bad)' if overdue else ''}'>"
                         f"{money(overdue, cur) + ' overdue' if overdue else 'nothing overdue'}</div></div>")
        for cur, amount in received.items():
            if amount:
                cards.append(f"<div class='card'><div class='muted small'>Received this month ({e(cur)})</div><div class='big'>{money(amount, cur)}</div></div>")
        drafts = [i for i in invoices if i.state == "draft"]
        cards.append(f"<div class='card'><div class='muted small'>Drafts</div><div class='big'>{len(drafts)}</div>"
                     f"<div class='small'><a href='/invoices?status=draft'>{'review drafts' if drafts else 'none waiting'}</a></div></div>")
        overdue_list = [i for i in reports.open_invoices(invoices, today) if i.status(today) == "overdue"]
        body = (f"<div class='row spread'><h1>Dashboard</h1><a class='btn primary' href='/invoices/new'>+ New invoice</a></div>{intro}"
                f"<div class='cards'>{''.join(cards)}</div>")
        if overdue_list:
            body += "<h2>Overdue — follow these up</h2>" + self.invoice_table(overdue_list, names)
        body += "<h2>Recent invoices</h2>" + self.invoice_table(invoices[:8], names)
        return self.page("Dashboard", body, tab="home")

    # -- invoices --------------------------------------------------------------------------------

    def invoice_list(self, query, form) -> Response:
        today = self.today()
        status = self.one(query, "status")
        text = self.one(query, "q").strip()
        names = self.client_names()
        invoices = self.store.invoices()
        if status in STATUSES:
            invoices = [i for i in invoices if i.status(today) == status]
        elif status == "open":
            invoices = reports.open_invoices(invoices, today)
        if text:
            needle = text.casefold()
            invoices = [i for i in invoices if needle in i.label.casefold() or needle in names.get(i.client_id, "").casefold()
                        or needle in i.reference.casefold()]
        chips = [("", "All"), ("open", "Owed")] + [(s, STATUS_TEXT[s]) for s in STATUSES]
        chip_html = "".join(f"<a class='{'on' if s == status else ''}' href='/invoices?status={s}{'&q=' + quote(text) if text else ''}'>{e(label)}</a>"
                            for s, label in chips)
        search = (f"<form method='get' action='/invoices' class='row' style='margin-bottom:.8rem'><input type='hidden' name='status' value='{e(status)}'>"
                  f"<input type='search' name='q' value='{e(text)}' placeholder='Search number, client or reference' style='max-width:22rem'>"
                  f"<button class='btn'>Search</button></form>")
        body = (f"<div class='row spread'><h1>Invoices</h1><a class='btn primary' href='/invoices/new'>+ New invoice</a></div>"
                f"<div class='chips'>{chip_html}</div>{search}{self.invoice_table(invoices, names)}")
        return self.page("Invoices", body, tab="invoices")

    def invoice_new_form(self, query, form) -> Response:
        clients = self.store.clients()
        if not clients:
            return self.done("Add a client first: invoices are addressed to someone.", "/clients/new")
        chosen = self.one(query, "client")
        options = "".join(f"<option value='{c.id}' {'selected' if str(c.id) == chosen else ''}>{e(c.name)}</option>" for c in clients)
        body = (f"<h1>New invoice</h1><form method='post' action='/invoices/new' class='card' style='max-width:32rem'>{self.csrf()}"
                f"<div class='field'><label for='client'>Who is it for?</label><select id='client' name='client'>{options}</select></div>"
                f"<p class='small muted'>It starts as a draft: you can change anything until you issue it.</p>"
                f"<div class='row'><button class='btn primary'>Create draft</button><a class='btn' href='/clients/new'>New client</a></div></form>")
        return self.page("New invoice", body, tab="invoices")

    def invoice_create(self, query, form) -> Response:
        client_id = self.one(form, "client")
        if not client_id.isdigit():
            raise Problem("choose a client")
        inv = self.store.new_invoice(int(client_id), issue_date=self.today())
        rate = Decimal(self.store.setting("default_tax_rate"))
        return Response.redirect(f"/invoices/{inv.id}/edit" + (f"?tax={rate}" if rate else ""))

    def invoice_view(self, query, form, invoice_id: int) -> Response:
        today = self.today()
        inv = self.store.invoice(invoice_id)
        client = self.store.client(inv.client_id)
        t = inv.totals()
        status = inv.status(today)
        actions = []
        side = []
        if inv.state == "draft":
            actions += [f"<a class='btn primary' href='/invoices/{inv.id}/edit'>Edit</a>",
                        self.button(f"/invoices/{inv.id}/issue", "Issue invoice", "btn primary" if inv.items else "btn",
                                    "Issue this invoice? It gets its number and can't be edited afterwards."),
                        self.button(f"/invoices/{inv.id}/delete", "Delete draft", "btn danger", "Delete this draft?")]
        else:
            actions.append(f"<a class='btn primary' href='/invoices/{inv.id}/print' target='_blank' rel='noopener'>Print / save PDF</a>")
        actions.append(self.button(f"/invoices/{inv.id}/duplicate", "Duplicate"))
        if inv.state == "issued" and not inv.payments:
            actions.append(self.button(f"/invoices/{inv.id}/void", "Void", "btn danger",
                                       "Void this invoice? It keeps its number but will no longer count as owed."))
        if inv.state == "issued" and t.balance > 0:
            side.append(f"""<form method='post' action='/invoices/{inv.id}/payments' class='card'>{self.csrf()}
<h2 style='margin-top:0'>Record a payment</h2>
<div class='field'><label for='amount'>Amount ({e(inv.currency)})</label><input id='amount' name='amount' inputmode='decimal' value='{e(t.balance)}' required></div>
<div class='field'><label for='pdate'>Date received</label><input id='pdate' type='date' name='date' value='{today.isoformat()}' required></div>
<div class='field'><label for='method'>Method <span class='hint'>optional</span></label><input id='method' name='method' list='methods' placeholder='Bank transfer'>
<datalist id='methods'><option>Bank transfer</option><option>Cash</option><option>Card</option><option>Cheque</option><option>PayPal</option></datalist></div>
<div class='field'><label for='pnote'>Note <span class='hint'>optional</span></label><input id='pnote' name='note'></div>
<button class='btn primary'>Save payment</button></form>""")
        if inv.payments:
            rows = "".join(f"<tr><td>{e(nice_date(p.date))}</td><td class='n'>{money(p.amount, inv.currency)}</td><td>{e(p.method)}"
                           f"{(' · ' + e(p.note)) if p.note else ''}</td><td>"
                           + self.button(f"/invoices/{inv.id}/payments/{p.id}/delete", "Remove", "btn small danger", "Remove this payment?")
                           + "</td></tr>" for p in inv.payments)
            side.append(f"<div class='card'><h2 style='margin-top:0'>Payments</h2><table class='list'><tbody>{rows}</tbody></table>"
                        f"<p class='small muted'>Still owed: <strong>{money(t.balance, inv.currency)}</strong></p></div>")
        late = f" · {inv.days_overdue(today)} days late" if status == "overdue" else ""
        side.insert(0, f"<div class='card'><div class='muted small'>Client</div><a href='/clients/{client.id}'><strong>{e(client.name)}</strong></a>"
                       f"<div class='muted small' style='margin-top:.6rem'>Total</div><div class='big'>{money(t.total, inv.currency)}</div>"
                       f"<div class='small'>{pill(status)}<span class='muted'>{e(late)}</span></div></div>")
        body = (f"<div class='row spread'><h1>{e(inv.label)}</h1><div class='row'>{''.join(actions)}</div></div>"
                f"<div class='side'><iframe class='preview' title='Invoice preview' src='/invoices/{inv.id}/print?embed=1'></iframe>"
                f"<div style='display:grid;gap:1rem'>{''.join(side)}</div></div>")
        return self.page(inv.label, body, tab="invoices")

    def invoice_print(self, query, form, invoice_id: int) -> Response:
        inv = self.store.invoice(invoice_id)
        toolbar = "<style>body{background:#fff}.sheet{margin:0;box-shadow:none;max-width:none}</style>" if self.one(query, "embed") else (
            f"<div class='toolbar'><a href='/invoices/{inv.id}'>Back</a><button type='button' data-print>Print / Save as PDF</button></div>"
            "<script src='/static/app.js' defer></script>")
        return Response.html(invoice_document(inv, self.store.client(inv.client_id), self.store.settings(), self.today(), toolbar=toolbar))

    def editor(self, inv: Invoice, values: dict[str, list[str]] | None = None, error: str = "", tax: str = "") -> Response:
        """The draft editor. After a mistake it's shown again with exactly what was typed, so nothing has to be re-entered."""
        clients = self.store.clients()
        if inv.client_id not in {c.id for c in clients}:
            clients.append(self.store.client(inv.client_id))
        v = values or {}
        get = lambda key, default: self.one(v, key, default) if values else default  # noqa: E731
        if values:
            rows = list(zip(v.get("desc", []), v.get("qty", []), v.get("price", []), v.get("tax", [])))
        else:
            rows = [(i.description, number_text(i.quantity), str(i.unit_price), number_text(i.tax_rate)) for i in inv.items]
        default_tax = tax or ""
        blank = ("", "1", "", default_tax)
        rows += [blank] * (3 if not rows else 1)

        def row(desc: str, qty: str, price: str, rate: str) -> str:
            return (f"<tr><td data-label='Description'><textarea name='desc' rows='1' aria-label='Description' style='min-height:2.6rem'>{e(desc)}</textarea></td>"
                    f"<td class='qty' data-label='Qty'><input name='qty' value='{e(qty)}' inputmode='decimal' aria-label='Quantity'></td>"
                    f"<td class='price' data-label='Price'><input name='price' value='{e(price)}' inputmode='decimal' aria-label='Unit price'></td>"
                    f"<td class='tax' data-label='Tax %'><input name='tax' value='{e(rate)}' inputmode='decimal' aria-label='Tax percent' placeholder='0'></td>"
                    f"<td class='x'><button type='button' class='btn small' data-remove-line aria-label='Remove line'>✕</button></td></tr>")

        client_opts = "".join(f"<option value='{c.id}' {'selected' if str(c.id) == get('client', str(inv.client_id)) else ''}>{e(c.name)}</option>"
                              for c in clients)
        dtype = get("discount_type", inv.discount_type)
        dopts = "".join(f"<option value='{k}' {'selected' if k == dtype else ''}>{label}</option>"
                        for k, label in (("none", "No discount"), ("percent", "Percent off"), ("amount", "Amount off")))
        body = f"""<div class='row spread'><h1>Edit {e(inv.label)}</h1><a class='btn' href='/invoices/{inv.id}'>Cancel</a></div>
{f"<div class='error' role='alert'>{e(error)}</div>" if error else ""}
<form method='post' action='/invoices/{inv.id}/edit'>{self.csrf()}
<div class='card'><div class='grid'>
<div class='field'><label for='client'>Client</label><select id='client' name='client'>{client_opts}</select></div>
<div class='field'><label for='issue'>Invoice date</label><input id='issue' type='date' name='issue_date' value='{e(get("issue_date", inv.issue_date.isoformat()))}' required></div>
<div class='field'><label for='due'>Due date</label><input id='due' type='date' name='due_date' value='{e(get("due_date", inv.due_date.isoformat()))}' required></div>
<div class='field'><label for='currency'>Currency</label><input id='currency' name='currency' value='{e(get("currency", inv.currency))}' maxlength='3' style='text-transform:uppercase' required></div>
<div class='field'><label for='ref'>Reference <span class='hint'>e.g. their PO number</span></label><input id='ref' name='reference' value='{e(get("reference", inv.reference))}' maxlength='80'></div>
</div></div>
<h2>Lines</h2>
<div class='card'><table class='items'><thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Tax %</th><th></th></tr></thead>
<tbody id='lines'>{''.join(row(*r) for r in rows)}</tbody></table>
<template id='line-template'>{row(*blank)}</template>
<button type='button' class='btn small' data-add-line>+ Add line</button>
<p class='small muted'>Empty lines are ignored. Use a negative price for a credit or adjustment line.</p></div>
<h2>Discount and notes</h2>
<div class='card'><div class='grid'>
<div class='field'><label for='dtype'>Discount</label><select id='dtype' name='discount_type'>{dopts}</select></div>
<div class='field'><label for='dval'>Discount value <span class='hint'>% or amount</span></label><input id='dval' name='discount_value' value='{e(get("discount_value", number_text(inv.discount_value)))}' inputmode='decimal'></div>
</div>
<div class='field'><label for='notes'>Notes for the client <span class='hint'>shown on the invoice</span></label><textarea id='notes' name='notes'>{e(get("notes", inv.notes))}</textarea></div></div>
<div class='row' style='margin-top:1rem'><button class='btn primary' name='then' value='view'>Save draft</button>
<button class='btn' name='then' value='issue'>Save and issue</button></div>
</form>"""
        return self.page(f"Edit {inv.label}", body, tab="invoices", status=400 if error else 200)

    def invoice_edit_form(self, query, form, invoice_id: int) -> Response:
        inv = self.store.invoice(invoice_id)
        if inv.state != "draft":
            return self.done(f"{inv.label} has been issued, so it's locked. Duplicate it to make a corrected copy.", f"/invoices/{inv.id}")
        return self.editor(inv, tax=self.one(query, "tax"))

    def read_invoice_form(self, inv: Invoice, form: dict[str, list[str]]) -> Invoice:
        client = self.one(form, "client")
        if not client.isdigit():
            raise Problem("choose a client")
        inv.client_id = int(client)
        inv.issue_date = parse_date(self.one(form, "issue_date"), "the invoice date")
        inv.due_date = parse_date(self.one(form, "due_date"), "the due date")
        inv.currency = self.one(form, "currency").strip().upper()
        inv.reference = self.one(form, "reference")[:80]
        inv.notes = self.one(form, "notes")[:4000]
        inv.discount_type = self.one(form, "discount_type", "none")
        raw_discount = self.one(form, "discount_value").strip()
        inv.discount_value = parse_decimal(raw_discount or "0", "the discount") if inv.discount_type != "none" else Decimal(0)
        if inv.discount_type == "none":
            inv.discount_value = Decimal(0)
        items = []
        columns = [form.get(k, []) for k in ("desc", "qty", "price", "tax")]
        if len({len(c) for c in columns}) > 1:
            raise Problem("the lines didn't arrive complete; please try again")
        for n, (desc, qty, price, rate) in enumerate(zip(*columns), start=1):
            if not desc.strip() and not price.strip():
                continue
            if not price.strip():
                raise Problem(f"line {n} ({desc.strip()[:40]}) needs a price")
            items.append(Item(desc[:2000], parse_decimal(qty.strip() or "1", f"the quantity on line {n}"),
                              parse_decimal(price, f"the price on line {n}", negative=True),
                              parse_decimal(rate.strip() or "0", f"the tax rate on line {n}")).validate())
        inv.items = items
        return inv

    def invoice_save(self, query, form, invoice_id: int) -> Response:
        inv = self.store.invoice(invoice_id)
        if inv.state != "draft":
            return self.done(f"{inv.label} has already been issued, so the changes weren't saved.", f"/invoices/{inv.id}")
        try:
            self.store.save_draft(self.read_invoice_form(inv, form))
        except (InvoiceError, MoneyError, Problem) as err:
            return self.editor(self.store.invoice(invoice_id), form, str(err))
        if self.one(form, "then") == "issue":
            return self.invoice_issue(query, form, invoice_id)
        return self.done("Draft saved.", f"/invoices/{inv.id}")

    def invoice_issue(self, query, form, invoice_id: int) -> Response:
        inv = self.store.issue(invoice_id)
        return self.done(f"Issued as {inv.label}. Print it or save it as a PDF to send.", f"/invoices/{inv.id}")

    def invoice_void(self, query, form, invoice_id: int) -> Response:
        inv = self.store.void(invoice_id)
        return self.done(f"{inv.label} is void.", f"/invoices/{inv.id}")

    def invoice_delete(self, query, form, invoice_id: int) -> Response:
        self.store.delete_draft(invoice_id)
        return self.done("Draft deleted.", "/invoices")

    def invoice_duplicate(self, query, form, invoice_id: int) -> Response:
        new = self.store.duplicate(invoice_id, issue_date=self.today())
        return self.done("Copied into a new draft. Check the dates and lines, then issue it.", f"/invoices/{new.id}/edit")

    def payment_add(self, query, form, invoice_id: int) -> Response:
        amount = parse_decimal(self.one(form, "amount"), "the payment")
        payment = Payment(parse_date(self.one(form, "date"), "the payment date"), amount, self.one(form, "method")[:60], self.one(form, "note")[:200])
        inv = self.store.add_payment(invoice_id, payment)
        left = inv.totals().balance
        message = f"Payment of {format_money(amount, inv.currency)} saved. " + (
            f"{inv.label} is fully paid." if left <= 0 else f"{format_money(left, inv.currency)} still owed.")
        return self.done(message, f"/invoices/{invoice_id}")

    def payment_delete(self, query, form, invoice_id: int, payment_id: int) -> Response:
        self.store.delete_payment(invoice_id, payment_id)
        return self.done("Payment removed.", f"/invoices/{invoice_id}")

    # -- clients ---------------------------------------------------------------------------------

    def client_list(self, query, form) -> Response:
        today = self.today()
        show_archived = self.one(query, "archived") == "1"
        clients = self.store.clients(archived=show_archived)
        invoices = self.store.invoices()
        balances = reports.client_balances(invoices, today)
        counts: dict[int, int] = {}
        for inv in invoices:
            counts[inv.client_id] = counts.get(inv.client_id, 0) + 1
        if not clients:
            table = "<div class='empty card'><p>No clients yet. Add the people and companies you bill.</p><a class='btn primary' href='/clients/new'>Add a client</a></div>"
        else:
            rows = []
            for c in clients:
                owed = ", ".join(money(amount, cur) for (cid, cur), amount in sorted(balances.items()) if cid == c.id) or "–"
                rows.append(f"<tr><td><a href='/clients/{c.id}'>{e(c.name)}</a>{' <span class=pill>archived</span>' if c.archived else ''}</td>"
                            f"<td>{e(c.email)}</td><td class='n'>{counts.get(c.id, 0)}</td><td class='n'>{owed}</td></tr>")
            table = (f"<div class='scroll'><table class='list'><thead><tr><th>Name</th><th>Email</th><th class='n'>Invoices</th><th class='n'>Owes you</th></tr></thead>"
                     f"<tbody>{''.join(rows)}</tbody></table></div>")
        toggle = "<a href='/clients'>Hide archived</a>" if show_archived else "<a href='/clients?archived=1'>Show archived</a>"
        body = (f"<div class='row spread'><h1>Clients</h1><a class='btn primary' href='/clients/new'>+ New client</a></div>{table}"
                f"<p class='small'>{toggle}</p>")
        return self.page("Clients", body, tab="clients")

    def client_form(self, client: Client, action: str, submit: str, error: str = "") -> str:
        return f"""{f"<div class='error' role='alert'>{e(error)}</div>" if error else ""}
<form method='post' action='{e(action)}' class='card'>{self.csrf()}<div class='grid'>
<div class='field'><label for='name'>Name</label><input id='name' name='name' value='{e(client.name)}' maxlength='200' required></div>
<div class='field'><label for='email'>Email</label><input id='email' type='email' name='email' value='{e(client.email)}' maxlength='254'></div>
<div class='field'><label for='tax_id'>Tax ID <span class='hint'>VAT / GST / NTN, if they need it on invoices</span></label><input id='tax_id' name='tax_id' value='{e(client.tax_id)}' maxlength='60'></div></div>
<div class='field'><label for='address'>Address</label><textarea id='address' name='address'>{e(client.address)}</textarea></div>
<div class='field'><label for='notes'>Private notes <span class='hint'>not shown on invoices</span></label><textarea id='notes' name='notes'>{e(client.notes)}</textarea></div>
<button class='btn primary'>{e(submit)}</button></form>"""

    def read_client(self, client: Client, form) -> Client:
        client.name = self.one(form, "name")[:200]
        client.email = self.one(form, "email")[:254]
        client.address = self.one(form, "address")[:1000]
        client.tax_id = self.one(form, "tax_id").strip()[:60]
        client.notes = self.one(form, "notes")[:4000]
        return client

    def client_new_form(self, query, form) -> Response:
        return self.page("New client", "<h1>New client</h1>" + self.client_form(Client(""), "/clients/new", "Add client"), tab="clients")

    def client_create(self, query, form) -> Response:
        client = self.read_client(Client(""), form)
        try:
            self.store.add_client(client)
        except InvoiceError as err:
            return self.page("New client", "<h1>New client</h1>" + self.client_form(client, "/clients/new", "Add client", str(err)), tab="clients", status=400)
        return self.done(f"{client.name} added.", f"/clients/{client.id}")

    def client_view(self, query, form, client_id: int, error: str = "", draft: Client | None = None) -> Response:
        today = self.today()
        client = self.store.client(client_id)
        invoices = self.store.invoices(client_id=client_id)
        balances = {cur: amount for (cid, cur), amount in reports.client_balances(invoices, today).items()}
        owed = " + ".join(money(a, c) for c, a in sorted(balances.items())) or "nothing"
        actions = [f"<a class='btn primary' href='/invoices/new?client={client.id}'>+ New invoice</a>"] if not client.archived else []
        actions.append(self.button(f"/clients/{client.id}/archive", "Restore" if client.archived else "Archive"))
        if not invoices:
            actions.append(self.button(f"/clients/{client.id}/delete", "Delete", "btn danger", f"Delete {client.name}?"))
        body = (f"<div class='row spread'><h1>{e(client.name)}{' <span class=pill>archived</span>' if client.archived else ''}</h1>"
                f"<div class='row'>{''.join(actions)}</div></div>"
                f"<div class='cards'><div class='card'><div class='muted small'>Owes you</div><div class='big'>{owed}</div></div>"
                f"<div class='card'><div class='muted small'>Invoices</div><div class='big'>{len(invoices)}</div></div></div>"
                f"<h2>Invoices</h2>{self.invoice_table(invoices, {}, client_column=False)}"
                f"<h2>Details</h2>{self.client_form(draft or client, f'/clients/{client.id}', 'Save changes', error)}")
        return self.page(client.name, body, tab="clients", status=400 if error else 200)

    def client_save(self, query, form, client_id: int) -> Response:
        client = self.read_client(self.store.client(client_id), form)
        try:
            self.store.update_client(client)
        except InvoiceError as err:
            return self.client_view(query, form, client_id, str(err), client)
        return self.done("Saved.", f"/clients/{client_id}")

    def client_archive(self, query, form, client_id: int) -> Response:
        client = self.store.client(client_id)
        client.archived = not client.archived
        self.store.update_client(client)
        return self.done(f"{client.name} {'archived' if client.archived else 'restored'}.", f"/clients/{client_id}")

    def client_delete(self, query, form, client_id: int) -> Response:
        client = self.store.client(client_id)
        self.store.delete_client(client_id)
        return self.done(f"{client.name} deleted.", "/clients")

    # -- reports ---------------------------------------------------------------------------------

    def reports_page(self, query, form) -> Response:
        today = self.today()
        start = parse_date(self.one(query, "from"), "the start date") if self.one(query, "from") else date(today.year, 1, 1)
        end = parse_date(self.one(query, "to"), "the end date") if self.one(query, "to") else today
        if end < start:
            raise Problem("the end date is before the start date")
        invoices = self.store.invoices()
        names = self.client_names()
        parts = [f"""<div class='row spread'><h1>Reports</h1><div class='row'><a class='btn' href='/export/invoices.csv'>Invoices CSV</a>
<a class='btn' href='/export/payments.csv'>Payments CSV</a></div></div>
<form method='get' class='row card' style='margin-bottom:1rem'><label for='from' style='margin:0'>From</label><input id='from' type='date' name='from' value='{start}' style='width:auto'>
<label for='to' style='margin:0'>to</label><input id='to' type='date' name='to' value='{end}' style='width:auto'><button class='btn'>Show</button>
<span class='small muted'>The period applies to income and tax. Money owed is always as of today.</span></form>"""]
        aging = reports.aging(invoices, today)
        parts.append("<h2>Money owed to you, by how late it is</h2>")
        if not aging:
            parts.append("<p class='muted'>Nobody owes you anything right now.</p>")
        for cur, row in aging.items():
            head = "".join(f"<th class='n'>{e(b)}</th>" for b in reports.AGING_BUCKETS)
            cells = "".join(f"<td class='n'>{money(v, cur)}</td>" for v in row.buckets)
            detail = "".join(f"<tr><td><a href='/invoices/{inv.id}'>{e(inv.label)}</a></td><td>{e(names.get(inv.client_id, '?'))}</td>"
                             f"<td>{e(nice_date(inv.due_date))}</td><td class='n'>{late or '–'}</td><td class='n'>{money(bal, cur)}</td></tr>"
                             for inv, late, bal in row.invoices)
            parts.append(f"<div class='scroll'><table class='list'><thead><tr><th>{e(cur)}</th>{head}<th class='n'>Total</th></tr></thead>"
                         f"<tbody><tr><td></td>{cells}<td class='n'><strong>{money(row.total, cur)}</strong></td></tr></tbody></table></div>"
                         f"<details style='margin:.5rem 0 1rem'><summary>{len(row.invoices)} invoices</summary><div class='scroll'><table class='list'>"
                         f"<thead><tr><th>Invoice</th><th>Client</th><th>Due</th><th class='n'>Days late</th><th class='n'>Owed</th></tr></thead>"
                         f"<tbody>{detail}</tbody></table></div></details>")
        parts.append("<h2>Invoiced and received, by month</h2>")
        monthly = reports.monthly(invoices, start, end)
        if not monthly:
            parts.append("<p class='muted'>No issued invoices or payments in this period.</p>")
        for cur, months in monthly.items():
            rows = "".join(f"<tr><td>{e(month_name(m))}</td><td class='n'>{money(v['invoiced'], cur)}</td><td class='n'>{money(v['received'], cur)}</td></tr>"
                           for m, v in months.items())
            tot_i = sum((v["invoiced"] for v in months.values()), Decimal(0))
            tot_r = sum((v["received"] for v in months.values()), Decimal(0))
            parts.append(f"<div class='scroll'><table class='list'><thead><tr><th>Month ({e(cur)})</th><th class='n'>Invoiced</th><th class='n'>Received</th></tr></thead>"
                         f"<tbody>{rows}<tr><td><strong>Total</strong></td><td class='n'><strong>{money(tot_i, cur)}</strong></td>"
                         f"<td class='n'><strong>{money(tot_r, cur)}</strong></td></tr></tbody></table></div>")
        parts.append("<h2>Tax charged</h2>")
        taxes = reports.tax_summary(invoices, start, end)
        if not taxes:
            parts.append("<p class='muted'>No tax was charged on invoices dated in this period.</p>")
        for cur, rates in taxes.items():
            rows = "".join(f"<tr><td>{e(format_rate(rate))}</td><td class='n'>{money(v['base'], cur)}</td><td class='n'>{money(v['tax'], cur)}</td></tr>"
                           for rate, v in rates.items())
            parts.append(f"<div class='scroll'><table class='list'><thead><tr><th>Rate ({e(cur)})</th><th class='n'>Charged on</th><th class='n'>Tax</th></tr></thead>"
                         f"<tbody>{rows}</tbody></table></div>")
        return self.page("Reports", "".join(parts), tab="reports")

    def csv_response(self, text: str, name: str) -> Response:
        # The BOM makes Excel read the file as UTF-8, so names like "Café Müller" survive.
        return Response(200, ("﻿" + text).encode(), {"Content-Type": "text/csv; charset=utf-8",
                                                          "Content-Disposition": f"attachment; filename={name}"})

    def export_invoices(self, query, form) -> Response:
        return self.csv_response(reports.invoices_csv(self.store.invoices(), self.client_names(), self.today()), "billwise-invoices.csv")

    def export_payments(self, query, form) -> Response:
        return self.csv_response(reports.payments_csv(self.store.invoices(), self.client_names()), "billwise-payments.csv")

    # -- settings --------------------------------------------------------------------------------

    def settings_form(self, query, form, error: str = "", values: dict[str, str] | None = None) -> Response:
        s = values or self.store.settings()
        field = lambda key, label, hint="", kind="input": (  # noqa: E731
            f"<div class='field'><label for='{key}'>{label}{f' <span class=hint>{hint}</span>' if hint else ''}</label>"
            + (f"<textarea id='{key}' name='{key}'>{e(s[key])}</textarea>" if kind == "textarea"
               else f"<input id='{key}' name='{key}' value='{e(s[key])}'>") + "</div>")
        body = f"""<h1>Settings</h1>{f"<div class='error' role='alert'>{e(error)}</div>" if error else ""}
<form method='post' action='/settings'>{self.csrf()}
<h2>Your business <span class='hint'>shown at the top of every invoice</span></h2><div class='card'><div class='grid'>
{field("business_name", "Business name")}{field("business_email", "Email")}{field("business_tax_id", "Tax ID", "VAT / GST / NTN")}</div>
{field("business_address", "Address", "", "textarea")}</div>
<h2>Invoices</h2><div class='card'><div class='grid'>
{field("currency", "Usual currency", "3 letters, e.g. USD, EUR, PKR")}{field("terms_days", "Payment terms", "days until due")}
{field("default_tax_rate", "Usual tax rate", "% for new lines, 0 for none")}{field("number_format", "Invoice numbers", "{YYYY} year, {NNNN} counter")}</div>
{field("payment_details", "How to pay you", "bank details or a payment link, printed on unpaid invoices", "textarea")}
{field("footer", "Footer", "a short line at the bottom")}</div>
<div class='row' style='margin-top:1rem'><button class='btn primary'>Save settings</button></div></form>
<h2>Backup</h2><div class='card'><p>Everything is in one file on this computer:<br><code>{e(self.store.path)}</code></p>
<a class='btn' href='/backup'>Download a backup</a></div>"""
        return self.page("Settings", body, tab="settings", status=400 if error else 200)

    def settings_save(self, query, form) -> Response:
        values = {key: self.one(form, key, self.store.setting(key))[:2000] for key in DEFAULTS}
        try:
            self.store.save_settings(values)
        except (InvoiceError, MoneyError) as err:
            return self.settings_form(query, form, str(err), {**self.store.settings(), **values})
        return self.done("Settings saved.", "/settings")

    def backup(self, query, form) -> Response:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.store.backup(Path(tmp) / "billwise.db")
            data = path.read_bytes()
        name = f"billwise-backup-{self.today().isoformat()}.db"
        return Response(200, data, {"Content-Type": "application/octet-stream", "Content-Disposition": f"attachment; filename={name}"})


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "billwise"

    def _serve(self, method: str) -> None:
        form: dict[str, list[str]] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413, "Too much data")
                return
            body = self.rfile.read(length).decode("utf-8", "replace")
            form = parse_qs(body, keep_blank_values=True)
        response = self.app.request(method, self.path, form, dict(self.headers.items()))
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - keep the terminal quiet
        pass


def make_server(store: Store, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    app = App(store)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    real_port = server.server_address[1]
    app.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}", f"[::1]:{real_port}"}
    return server
