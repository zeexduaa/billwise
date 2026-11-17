"""The billwise command: everything the web app does, from a terminal (and scriptable)."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import __version__, reports
from .model import STATUSES, Client, Invoice, InvoiceError, Item, Payment
from .money import MoneyError, format_money, format_rate, parse_decimal
from .render import STATUS_TEXT, invoice_document, nice_date, number_text
from .store import DEFAULTS, Store, StoreError, default_path


def out(text: str = "") -> None:
    print(text)


def table(rows: list[list[str]], right: set[int] = frozenset()) -> str:  # type: ignore[assignment]
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, row in enumerate(rows):
        cells = [c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(row, widths))]
        lines.append("  ".join(cells).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a date like 2026-03-31") from None


def parse_discount(text: str) -> tuple[str, Decimal]:
    """"10%" is ten percent off; "50" is fifty off; "0" or "none" removes the discount."""
    text = text.strip()
    if text.lower() in ("", "0", "none"):
        return "none", Decimal(0)
    if text.endswith("%"):
        return "percent", parse_decimal(text, "the discount")
    return "amount", parse_decimal(text, "the discount")


def parse_line(text: str, default_tax: Decimal) -> Item:
    """"Logo design | 2 | 150 | 17" -> description | quantity | unit price | tax %. Quantity and tax can be left out:
    "Logo design | 150"."""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) == 2:
        desc, qty, price, tax = parts[0], "1", parts[1], ""
    elif len(parts) in (3, 4):
        desc, qty, price, tax = (parts + [""])[:4]
    else:
        raise InvoiceError(f"write a line as \"description | qty | price | tax%\", not {text!r}")
    return Item(desc, parse_decimal(qty or "1", "the quantity"), parse_decimal(price, "the price", negative=True),
                parse_decimal(tax, "the tax rate") if tax else default_tax).validate()


def show_invoice(store: Store, inv: Invoice, today: date) -> None:
    client = store.client(inv.client_id)
    t = inv.totals()
    cur = inv.currency
    m = lambda a: format_money(a, cur)  # noqa: E731
    out(f"{inv.label}  ·  {STATUS_TEXT[inv.status(today)]}  ·  {client.name}")
    out(f"Date {nice_date(inv.issue_date)}, due {nice_date(inv.due_date)}" + (f", reference {inv.reference}" if inv.reference else ""))
    out()
    rows = [["#", "Description", "Qty", "Price", "Tax", "Amount"]]
    for n, item in enumerate(inv.items, 1):
        rows.append([str(n), item.description.replace("\n", " ")[:50], number_text(item.quantity), m(item.unit_price),
                     format_rate(item.tax_rate) if item.tax_rate else "–", m(item.amount(cur))])
    out(table(rows, {0, 2, 3, 4, 5}) if inv.items else "(no lines yet)")
    out()
    summary = [["Subtotal", m(t.subtotal)]]
    if t.discount:
        summary.append(["Discount", "-" + m(t.discount)])
    summary += [[f"Tax {format_rate(x.rate)} on {m(x.base)}", m(x.tax)] for x in t.taxes]
    summary.append(["Total", m(t.total)])
    if t.paid:
        summary += [["Paid", "-" + m(t.paid)], ["Balance due", m(t.balance)]]
    width = max(len(r[0]) for r in summary)
    for label, value in summary:
        out(f"{label.rjust(width)}  {value}")
    if inv.payments:
        out()
        out("Payments:")
        for p in inv.payments:
            out(f"  #{p.id}  {p.date}  {m(p.amount)}" + (f"  {p.method}" if p.method else "") + (f"  ({p.note})" if p.note else ""))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="billwise", description="Invoices, clients and payments, kept on your own computer.")
    p.add_argument("--version", action="version", version=f"billwise {__version__}")
    p.add_argument("--db", type=Path, help="data file to use (default: BILLWISE_DB or your app-data folder)")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("serve", help="open the web app in your browser")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser window")

    s = sub.add_parser("setup", help="show or change your business details and invoice settings")
    for key in DEFAULTS:
        s.add_argument("--" + key.replace("_", "-"), dest=key, metavar="TEXT")

    c = sub.add_parser("client", help="add, list and change clients").add_subparsers(dest="action", metavar="action", required=True)
    s = c.add_parser("add", help="add a client")
    s.add_argument("name")
    for opt in ("email", "address", "tax-id", "notes"):
        s.add_argument("--" + opt, default="")
    s = c.add_parser("list", help="list clients and what they owe")
    s.add_argument("--all", action="store_true", help="include archived clients")
    for action, text in (("show", "a client's details and invoices"), ("archive", "hide a client you no longer work with"),
                         ("restore", "bring back an archived client"), ("delete", "delete a client who has no invoices")):
        c.add_parser(action, help=text).add_argument("client", help="id or (part of the) name")
    s = c.add_parser("edit", help="change a client's details")
    s.add_argument("client")
    for opt in ("name", "email", "address", "tax-id", "notes"):
        s.add_argument("--" + opt)

    i = sub.add_parser("invoice", help="create, issue and manage invoices").add_subparsers(dest="action", metavar="action", required=True)
    s = i.add_parser("new", help="start an invoice (a draft) for a client")
    s.add_argument("client")
    s.add_argument("-l", "--line", action="append", default=[], metavar="'DESC | QTY | PRICE | TAX%'", help="add a line (repeatable)")
    s.add_argument("--date", type=day, help="invoice date (default today)")
    s.add_argument("--due", type=day, help="due date (default: today + your payment terms)")
    s.add_argument("--currency")
    s.add_argument("--discount", help="10%% for percent off, or an amount like 50")
    s.add_argument("--reference", default="", help="the client's PO number or similar")
    s.add_argument("--notes", default="")
    s.add_argument("--issue", action="store_true", help="issue it straight away")
    s = i.add_parser("add-line", help="add a line to a draft")
    s.add_argument("invoice")
    s.add_argument("line", metavar="'DESC | QTY | PRICE | TAX%'")
    s = i.add_parser("remove-line", help="remove a line from a draft")
    s.add_argument("invoice")
    s.add_argument("number", type=int, help="line number as shown by 'invoice show'")
    s = i.add_parser("set", help="change a draft's dates, discount, reference or notes")
    s.add_argument("invoice")
    s.add_argument("--date", type=day)
    s.add_argument("--due", type=day)
    s.add_argument("--currency")
    s.add_argument("--discount")
    s.add_argument("--reference")
    s.add_argument("--notes")
    s = i.add_parser("list", help="list invoices")
    s.add_argument("--status", choices=STATUSES + ("open",), help="'open' means anything still owed")
    s.add_argument("--client")
    for action, text in (("show", "an invoice's lines, totals and payments"), ("issue", "give a draft its number and lock it"),
                         ("void", "cancel an issued invoice"), ("delete", "delete a draft"), ("duplicate", "copy into a new draft")):
        i.add_parser(action, help=text).add_argument("invoice", help="number (INV-2026-0001) or id")
    s = i.add_parser("html", help="save the invoice as a printable HTML page")
    s.add_argument("invoice")
    s.add_argument("-o", "--output", type=Path, help="file to write (default: <number>.html)")

    s = sub.add_parser("pay", help="record a payment against an invoice")
    s.add_argument("invoice")
    s.add_argument("amount", nargs="?", help="default: everything still owed")
    s.add_argument("--date", type=day, help="date received (default today)")
    s.add_argument("--method", default="")
    s.add_argument("--note", default="")
    s = sub.add_parser("unpay", help="remove a payment recorded by mistake")
    s.add_argument("invoice")
    s.add_argument("payment_id", type=int, help="the #id shown by 'invoice show'")

    s = sub.add_parser("report", help="money owed, monthly income and tax")
    s.add_argument("kind", choices=("owed", "monthly", "tax"))
    s.add_argument("--from", dest="start", type=day)
    s.add_argument("--to", dest="end", type=day)

    s = sub.add_parser("export", help="export invoices or payments as CSV for a spreadsheet or your accountant")
    s.add_argument("kind", choices=("invoices", "payments"))
    s.add_argument("-o", "--output", type=Path, help="file to write (default: print)")

    s = sub.add_parser("backup", help="copy all your data to a file")
    s.add_argument("file", type=Path)
    return p


def run(args: argparse.Namespace, store: Store, today: date) -> int:
    cmd = args.command
    if cmd == "serve":
        from .web import make_server
        try:
            server = make_server(store, args.port)
        except OSError as err:
            raise InvoiceError(f"can't use port {args.port} ({err.strerror}). Is billwise already running? "
                               f"Open http://127.0.0.1:{args.port}/ or pick another port with --port") from None
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        out(f"billwise is running at {url}  (press Ctrl+C to stop)")
        out(f"Data file: {store.path}")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            out("\nStopped.")
        finally:
            server.server_close()
        return 0

    if cmd == "setup":
        changes = {k: v for k, v in vars(args).items() if k in DEFAULTS and v is not None}
        if changes:
            store.save_settings(changes)
            out("Saved.")
        for key, value in store.settings().items():
            out(f"{key.replace('_', '-'):18} {value.replace(chr(10), ' / ') or '–'}")
        return 0

    if cmd == "client":
        if args.action == "add":
            client = store.add_client(Client(args.name, args.email, args.address, args.tax_id, args.notes))
            out(f"Added {client.name} (#{client.id}).")
        elif args.action == "list":
            balances = reports.client_balances(store.invoices(), today)
            rows = [["#", "Name", "Email", "Owes you"]]
            for c in store.clients(archived=args.all):
                owed = ", ".join(format_money(a, cur) for (cid, cur), a in sorted(balances.items()) if cid == c.id) or "–"
                rows.append([str(c.id), c.name + (" (archived)" if c.archived else ""), c.email, owed])
            out(table(rows, {0, 3}) if len(rows) > 1 else "No clients yet. Add one with: billwise client add \"Name\"")
        elif args.action == "show":
            client = store.find_client(args.client)
            out(f"{client.name} (#{client.id})" + (" — archived" if client.archived else ""))
            for label, value in (("Email", client.email), ("Tax ID", client.tax_id), ("Address", client.address.replace("\n", ", ")),
                                 ("Notes", client.notes)):
                if value:
                    out(f"  {label}: {value}")
            invoices = store.invoices(client_id=client.id)
            out()
            print_invoices(invoices, {client.id: client.name}, today)  # type: ignore[dict-item]
        elif args.action in ("archive", "restore"):
            client = store.find_client(args.client)
            client.archived = args.action == "archive"
            store.update_client(client)
            out(f"{client.name} {'archived' if client.archived else 'restored'}.")
        elif args.action == "delete":
            client = store.find_client(args.client)
            store.delete_client(client.id)  # type: ignore[arg-type]
            out(f"Deleted {client.name}.")
        elif args.action == "edit":
            client = store.find_client(args.client)
            for key in ("name", "email", "address", "tax_id", "notes"):
                if getattr(args, key) is not None:
                    setattr(client, key, getattr(args, key))
            store.update_client(client)
            out(f"Saved {client.name}.")
        return 0

    if cmd == "invoice":
        return run_invoice(args, store, today)

    if cmd == "pay":
        inv = store.find_invoice(args.invoice)
        amount = parse_decimal(args.amount, "the payment") if args.amount else inv.totals().balance
        inv = store.add_payment(inv.id, Payment(args.date or today, amount, args.method, args.note))  # type: ignore[arg-type]
        left = inv.totals().balance
        out(f"Recorded {format_money(amount, inv.currency)} on {inv.label}. "
            + ("It's fully paid." if left <= 0 else f"{format_money(left, inv.currency)} still owed."))
        return 0

    if cmd == "unpay":
        inv = store.find_invoice(args.invoice)
        store.delete_payment(inv.id, args.payment_id)  # type: ignore[arg-type]
        out(f"Removed payment #{args.payment_id} from {inv.label}.")
        return 0

    if cmd == "report":
        return run_report(args, store, today)

    if cmd == "export":
        names = {c.id: c.name for c in store.clients(archived=True)}
        text = (reports.invoices_csv(store.invoices(), names, today) if args.kind == "invoices"  # type: ignore[arg-type]
                else reports.payments_csv(store.invoices(), names))  # type: ignore[arg-type]
        if args.output:
            args.output.write_text("﻿" + text, encoding="utf-8", newline="")
            out(f"Wrote {args.output}")
        else:
            sys.stdout.write(text.replace("\r\n", "\n"))
        return 0

    if cmd == "backup":
        out(f"Backed up to {store.backup(args.file)}")
        return 0
    return 2


def print_invoices(invoices: list[Invoice], names: dict[int, str], today: date) -> None:
    if not invoices:
        out("No invoices.")
        return
    rows = [["Invoice", "Client", "Date", "Due", "Total", "Owed", "Status"]]
    for inv in invoices:
        t = inv.totals()
        rows.append([inv.label, names.get(inv.client_id, "?"), str(inv.issue_date), str(inv.due_date), format_money(t.total, inv.currency),
                     format_money(t.balance, inv.currency) if inv.state == "issued" else "–", STATUS_TEXT[inv.status(today)]])
    out(table(rows, {4, 5}))


def run_invoice(args: argparse.Namespace, store: Store, today: date) -> int:
    action = args.action
    default_tax = Decimal(store.setting("default_tax_rate"))
    if action == "new":
        client = store.find_client(args.client)
        items = [parse_line(text, default_tax) for text in args.line]  # read everything before saving anything
        discount = parse_discount(args.discount) if args.discount else ("none", Decimal(0))
        inv = store.new_invoice(client.id, issue_date=args.date or today, due_date=args.due, currency=args.currency)  # type: ignore[arg-type]
        inv.items = items
        inv.discount_type, inv.discount_value = discount
        inv.reference, inv.notes = args.reference, args.notes
        try:
            store.save_draft(inv)
        except (InvoiceError, MoneyError):
            store.delete_draft(inv.id)  # type: ignore[arg-type]
            raise
        if args.issue:
            inv = store.issue(inv.id)  # type: ignore[arg-type]
            out(f"Issued {inv.label} for {client.name}: {format_money(inv.totals().total, inv.currency)}, due {inv.due_date}.")
        else:
            out(f"Created {inv.label} for {client.name}. Add lines with: billwise invoice add-line {inv.id} \"Work | 1 | 100\"")
            out(f"Issue it with: billwise invoice issue {inv.id}")
        return 0
    if action == "list":
        names = {c.id: c.name for c in store.clients(archived=True)}
        invoices = store.invoices(client_id=store.find_client(args.client).id) if args.client else store.invoices()
        if args.status == "open":
            invoices = reports.open_invoices(invoices, today)
        elif args.status:
            invoices = [i for i in invoices if i.status(today) == args.status]
        print_invoices(invoices, names, today)  # type: ignore[arg-type]
        return 0
    inv = store.find_invoice(args.invoice)
    assert inv.id is not None
    if action == "show":
        show_invoice(store, inv, today)
    elif action == "add-line":
        inv.items.append(parse_line(args.line, default_tax))
        store.save_draft(inv)
        out(f"Added. {inv.label} now totals {format_money(inv.totals().total, inv.currency)}.")
    elif action == "remove-line":
        if not 1 <= args.number <= len(inv.items):
            raise InvoiceError(f"{inv.label} has {len(inv.items)} lines")
        removed = inv.items.pop(args.number - 1)
        store.save_draft(inv)
        out(f"Removed \"{removed.description}\". {inv.label} now totals {format_money(inv.totals().total, inv.currency)}.")
    elif action == "set":
        if args.date:
            inv.issue_date = args.date
        if args.due:
            inv.due_date = args.due
        if args.currency:
            inv.currency = args.currency
        if args.discount is not None:
            inv.discount_type, inv.discount_value = parse_discount(args.discount)
        if args.reference is not None:
            inv.reference = args.reference
        if args.notes is not None:
            inv.notes = args.notes
        store.save_draft(inv)
        out(f"Saved {inv.label}.")
    elif action == "issue":
        inv = store.issue(inv.id)
        out(f"Issued {inv.label}: {format_money(inv.totals().total, inv.currency)}, due {inv.due_date}.")
        out(f"Make a printable copy with: billwise invoice html {inv.label}")
    elif action == "void":
        out(f"{store.void(inv.id).label} is void.")
    elif action == "delete":
        store.delete_draft(inv.id)
        out(f"Deleted {inv.label}.")
    elif action == "duplicate":
        new = store.duplicate(inv.id, issue_date=today)
        out(f"Copied {inv.label} into {new.label}.")
    elif action == "html":
        target = args.output or Path(f"{inv.label.replace(' ', '-')}.html")
        target.write_text(invoice_document(inv, store.client(inv.client_id), store.settings(), today), encoding="utf-8")
        out(f"Wrote {target}. Open it in a browser and print, or 'Save as PDF'.")
    return 0


def run_report(args: argparse.Namespace, store: Store, today: date) -> int:
    invoices = store.invoices()
    names = {c.id: c.name for c in store.clients(archived=True)}
    if args.kind == "owed":
        aging = reports.aging(invoices, today)
        if not aging:
            out("Nobody owes you anything right now.")
        for cur, row in aging.items():
            out(f"Owed to you in {cur}: {format_money(row.total, cur)}")
            for label, amount in zip(reports.AGING_BUCKETS, row.buckets):
                if amount:
                    out(f"  {label:18} {format_money(amount, cur)}")
            out()
            rows = [["Invoice", "Client", "Due", "Days late", "Owed"]]
            rows += [[inv.label, names.get(inv.client_id, "?"), str(inv.due_date), str(late or "–"), format_money(bal, cur)]
                     for inv, late, bal in row.invoices]
            out(table(rows, {3, 4}))
            out()
        return 0
    start = args.start or date(today.year, 1, 1)
    end = args.end or today
    out(f"{start} to {end}")
    if args.kind == "monthly":
        data = reports.monthly(invoices, start, end)
        if not data:
            out("No issued invoices or payments in this period.")
        for cur, months in data.items():
            rows = [["Month", "Invoiced", "Received"]]
            rows += [[m, format_money(v["invoiced"], cur), format_money(v["received"], cur)] for m, v in months.items()]
            rows.append(["Total", format_money(sum((v["invoiced"] for v in months.values()), Decimal(0)), cur),
                         format_money(sum((v["received"] for v in months.values()), Decimal(0)), cur)])
            out(f"\n{cur}")
            out(table(rows, {1, 2}))
    else:
        data = reports.tax_summary(invoices, start, end)
        if not data:
            out("No tax was charged on invoices dated in this period.")
        for cur, rates in data.items():
            rows = [["Rate", "Charged on", "Tax"]]
            rows += [[format_rate(rate), format_money(v["base"], cur), format_money(v["tax"], cur)] for rate, v in rates.items()]
            out(f"\n{cur}")
            out(table(rows, {1, 2}))
    return 0


def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]  # a Windows console can't show every name
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        store = Store(args.db or default_path())
    except StoreError as err:
        print(f"billwise: {err}", file=sys.stderr)
        return 1
    try:
        return run(args, store, today or date.today())
    except (InvoiceError, MoneyError) as err:
        print(f"billwise: {err}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    finally:
        store.close()
