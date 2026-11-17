"""Where everything is kept: one SQLite file. Amounts are stored as text so they come back exactly as saved."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .model import Client, Invoice, InvoiceError, Item, Payment, check_discount
from .money import check_currency, format_money, parse_decimal, round_money

SCHEMA = [
    # version 1
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE clients (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
        tax_id TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
        created TEXT NOT NULL);
    CREATE TABLE invoices (
        id INTEGER PRIMARY KEY, number TEXT UNIQUE, client_id INTEGER NOT NULL REFERENCES clients(id),
        state TEXT NOT NULL CHECK (state IN ('draft', 'issued', 'void')), currency TEXT NOT NULL,
        issue_date TEXT NOT NULL, due_date TEXT NOT NULL, discount_type TEXT NOT NULL DEFAULT 'none',
        discount_value TEXT NOT NULL DEFAULT '0', reference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
        created TEXT NOT NULL, issued_at TEXT NOT NULL DEFAULT '');
    CREATE TABLE items (
        id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, description TEXT NOT NULL, quantity TEXT NOT NULL, unit_price TEXT NOT NULL,
        tax_rate TEXT NOT NULL DEFAULT '0');
    CREATE TABLE payments (
        id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
        date TEXT NOT NULL, amount TEXT NOT NULL, method TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '');
    CREATE INDEX items_by_invoice ON items(invoice_id, position);
    CREATE INDEX payments_by_invoice ON payments(invoice_id, date);
    CREATE INDEX invoices_by_client ON invoices(client_id);
    """,
    # version 2: quotes live in the same table as invoices (same lines, totals and printing), told apart by `kind`.
    # A quote's outcome (accepted / declined) and the invoice it became are kept alongside.
    """
    ALTER TABLE invoices ADD COLUMN kind TEXT NOT NULL DEFAULT 'invoice' CHECK (kind IN ('invoice', 'quote'));
    ALTER TABLE invoices ADD COLUMN outcome TEXT NOT NULL DEFAULT '' CHECK (outcome IN ('', 'accepted', 'declined'));
    ALTER TABLE invoices ADD COLUMN converted_to INTEGER REFERENCES invoices(id);
    CREATE INDEX invoices_by_kind ON invoices(kind, issue_date);
    """,
]

DEFAULTS = {
    "business_name": "",
    "business_address": "",
    "business_email": "",
    "business_tax_id": "",
    "currency": "USD",
    "terms_days": "30",
    "number_format": "INV-{YYYY}-{NNNN}",
    "payment_details": "",
    "footer": "Thank you for your business.",
    "default_tax_rate": "0",
}


class StoreError(RuntimeError):
    """The data file can't be opened or is from a newer version of billwise."""


def default_path() -> Path:
    """BILLWISE_DB if set, otherwise the usual place for app data on this system."""
    if os.environ.get("BILLWISE_DB"):
        return Path(os.environ["BILLWISE_DB"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "billwise" / "billwise.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_number(fmt: str, issue: date, seq: int) -> str:
    """"INV-{YYYY}-{NNNN}" -> "INV-2026-0007". {YYYY}/{YY} are the year, a run of N's is the number padded to that width."""
    out = fmt.replace("{YYYY}", f"{issue.year:04d}").replace("{YY}", f"{issue.year % 100:02d}")
    return re.sub(r"\{(N+)\}", lambda m: f"{seq:0{len(m.group(1))}d}", out)


def check_number_format(fmt: str) -> str:
    fmt = fmt.strip()
    if not re.search(r"\{N+\}", fmt):
        raise InvoiceError("the invoice number format needs a counter like {NNNN}")
    if len(fmt) > 60 or re.search(r"[\x00-\x1f]", fmt):
        raise InvoiceError("the invoice number format is too long or has odd characters")
    return fmt


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except sqlite3.DatabaseError as err:
            raise StoreError(f"can't open {self.path}: {err}") from err

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > len(SCHEMA):
            raise StoreError(f"{self.path} was made by a newer version of billwise; please upgrade")
        for number, script in enumerate(SCHEMA[version:], start=version + 1):
            with self.db:
                self.db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")

    # -- settings --------------------------------------------------------------------------------

    def settings(self) -> dict[str, str]:
        out = dict(DEFAULTS)
        out.update({r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM settings WHERE key NOT LIKE 'seq:%'")})
        return out

    def setting(self, key: str) -> str:
        return self.settings()[key]

    def save_settings(self, values: dict[str, str]) -> None:
        clean = {}
        for key, value in values.items():
            if key not in DEFAULTS:
                raise InvoiceError(f"unknown setting {key!r}")
            value = str(value).strip()
            if key == "currency":
                value = check_currency(value)
            elif key == "terms_days":
                if not value.isdigit() or int(value) > 365:
                    raise InvoiceError("payment terms should be a number of days from 0 to 365")
                value = str(int(value))
            elif key == "number_format":
                value = check_number_format(value)
            elif key == "default_tax_rate":
                rate = parse_decimal(value, "default tax rate")
                if rate > 100:
                    raise InvoiceError("the default tax rate must be between 0 and 100%")
                value = str(rate)
            clean[key] = value
        with self.db:
            self.db.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                                clean.items())

    # -- clients ---------------------------------------------------------------------------------

    @staticmethod
    def _client(row: sqlite3.Row) -> Client:
        return Client(name=row["name"], email=row["email"], address=row["address"], tax_id=row["tax_id"],
                      notes=row["notes"], archived=bool(row["archived"]), id=row["id"])

    def add_client(self, client: Client) -> Client:
        client.validate()
        with self.db:
            cur = self.db.execute("INSERT INTO clients (name, email, address, tax_id, notes, archived, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (client.name, client.email, client.address, client.tax_id, client.notes, int(client.archived), now_iso()))
        client.id = cur.lastrowid
        return client

    def update_client(self, client: Client) -> None:
        client.validate()
        with self.db:
            cur = self.db.execute("UPDATE clients SET name=?, email=?, address=?, tax_id=?, notes=?, archived=? WHERE id=?",
                                  (client.name, client.email, client.address, client.tax_id, client.notes, int(client.archived), client.id))
        if not cur.rowcount:
            raise InvoiceError(f"there's no client {client.id}")

    def client(self, client_id: int) -> Client:
        row = self.db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not row:
            raise InvoiceError(f"there's no client {client_id}")
        return self._client(row)

    def clients(self, *, archived: bool = False) -> list[Client]:
        sql = "SELECT * FROM clients" + ("" if archived else " WHERE archived = 0") + " ORDER BY name COLLATE NOCASE, id"
        return [self._client(r) for r in self.db.execute(sql)]

    def find_client(self, text: str) -> Client:
        """A client by id, exact name, or a part of the name that matches only one client."""
        text = text.strip()
        if text.isdigit():
            return self.client(int(text))
        everyone = self.clients(archived=True)
        exact = [c for c in everyone if c.name.casefold() == text.casefold()]
        if len(exact) == 1:
            return exact[0]
        matches = [c for c in everyone if text.casefold() in c.name.casefold() and not c.archived] or \
                  [c for c in everyone if text.casefold() in c.name.casefold()]
        if not matches:
            raise InvoiceError(f"no client matches {text!r}")
        if len(matches) > 1:
            raise InvoiceError(f"{text!r} matches {len(matches)} clients: " + ", ".join(f"{c.name} (#{c.id})" for c in matches[:6]))
        return matches[0]

    def delete_client(self, client_id: int) -> None:
        """Only a client with no invoices can be deleted; otherwise archive them, so old invoices keep their client."""
        if self.db.execute("SELECT 1 FROM invoices WHERE client_id = ? LIMIT 1", (client_id,)).fetchone():
            raise InvoiceError("this client has invoices, so they can only be archived, not deleted")
        with self.db:
            self.db.execute("DELETE FROM clients WHERE id = ?", (client_id,))

    # -- invoices --------------------------------------------------------------------------------

    def _invoice(self, row: sqlite3.Row) -> Invoice:
        inv = Invoice(client_id=row["client_id"], currency=row["currency"], issue_date=date.fromisoformat(row["issue_date"]),
                      due_date=date.fromisoformat(row["due_date"]), state=row["state"], number=row["number"],
                      discount_type=row["discount_type"], discount_value=Decimal(row["discount_value"]),
                      reference=row["reference"], notes=row["notes"], id=row["id"], created=row["created"], issued_at=row["issued_at"],
                      kind=row["kind"], outcome=row["outcome"], converted_to=row["converted_to"])
        inv.items = [Item(r["description"], Decimal(r["quantity"]), Decimal(r["unit_price"]), Decimal(r["tax_rate"]))
                     for r in self.db.execute("SELECT * FROM items WHERE invoice_id = ? ORDER BY position", (inv.id,))]
        inv.payments = [Payment(date.fromisoformat(r["date"]), Decimal(r["amount"]), r["method"], r["note"], r["id"])
                        for r in self.db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY date, id", (inv.id,))]
        return inv

    def invoice(self, invoice_id: int) -> Invoice:
        row = self.db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not row:
            raise InvoiceError(f"there's no invoice {invoice_id}")
        return self._invoice(row)

    def find_invoice(self, text: str) -> Invoice:
        """An invoice by its number ("INV-2026-0003") or its id."""
        text = text.strip()
        row = self.db.execute("SELECT * FROM invoices WHERE number = ? COLLATE NOCASE", (text,)).fetchone()
        if row:
            return self._invoice(row)
        if text.lstrip("#").isdigit():
            return self.invoice(int(text.lstrip("#")))
        raise InvoiceError(f"there's no invoice {text!r}")

    def invoices(self, *, client_id: int | None = None) -> list[Invoice]:
        sql, args = "SELECT * FROM invoices", ()
        if client_id is not None:
            sql, args = sql + " WHERE client_id = ?", (client_id,)
        return [self._invoice(r) for r in self.db.execute(sql + " ORDER BY issue_date DESC, id DESC", args)]

    def _check(self, inv: Invoice) -> None:
        self.client(inv.client_id)
        inv.currency = check_currency(inv.currency)
        check_discount(inv.discount_type, inv.discount_value)
        if inv.due_date < inv.issue_date:
            raise InvoiceError("the due date can't be before the invoice date")
        for item in inv.items:
            item.validate()
        if len(inv.items) > 500:
            raise InvoiceError("an invoice can have at most 500 lines")

    def _write_items(self, inv: Invoice) -> None:
        self.db.execute("DELETE FROM items WHERE invoice_id = ?", (inv.id,))
        self.db.executemany("INSERT INTO items (invoice_id, position, description, quantity, unit_price, tax_rate) VALUES (?, ?, ?, ?, ?, ?)",
                            [(inv.id, i, it.description, str(it.quantity), str(it.unit_price), str(it.tax_rate)) for i, it in enumerate(inv.items)])

    def new_invoice(self, client_id: int, *, issue_date: date | None = None, due_date: date | None = None,
                    currency: str | None = None) -> Invoice:
        """A new draft for a client, dated today and due after the usual payment terms."""
        settings = self.settings()
        issue = issue_date or date.today()
        inv = Invoice(client_id=client_id, currency=currency or settings["currency"], issue_date=issue,
                      due_date=due_date or issue + timedelta(days=int(settings["terms_days"])), created=now_iso())
        self._check(inv)
        if self.client(client_id).archived:
            raise InvoiceError("this client is archived; restore them first")
        with self.db:
            cur = self.db.execute("INSERT INTO invoices (client_id, state, currency, issue_date, due_date, created) VALUES (?, 'draft', ?, ?, ?, ?)",
                                  (inv.client_id, inv.currency, inv.issue_date.isoformat(), inv.due_date.isoformat(), inv.created))
        inv.id = cur.lastrowid
        return inv

    def save_draft(self, inv: Invoice) -> None:
        """Save changes to a draft. Issued invoices are locked: what the client received must not change underneath them."""
        current = self.invoice(inv.id)  # type: ignore[arg-type]
        if current.state != "draft":
            raise InvoiceError(f"{current.label} has been issued, so it can't be edited; void it and make a new one, or duplicate it")
        self._check(inv)
        with self.db:
            self.db.execute("UPDATE invoices SET client_id=?, currency=?, issue_date=?, due_date=?, discount_type=?, discount_value=?, "
                            "reference=?, notes=? WHERE id=?",
                            (inv.client_id, inv.currency, inv.issue_date.isoformat(), inv.due_date.isoformat(), inv.discount_type,
                             str(inv.discount_value), inv.reference.strip(), inv.notes.strip(), inv.id))
            self._write_items(inv)

    def issue(self, invoice_id: int) -> Invoice:
        """Give a draft its number and lock it. Numbers are handed out in order with no gaps, per year if the format has one."""
        inv = self.invoice(invoice_id)
        if inv.state != "draft":
            raise InvoiceError(f"{inv.label} has already been issued")
        if not inv.items:
            raise InvoiceError("add at least one line before issuing")
        if inv.totals().total < 0:
            raise InvoiceError("the total can't be negative")
        fmt = self.setting("number_format")
        key = f"seq:{inv.issue_date.year}" if re.search(r"\{YY(YY)?\}", fmt) else "seq:all"
        with self.db:
            row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            seq = int(row["value"]) if row else 0
            while True:
                seq += 1
                number = next_number(fmt, inv.issue_date, seq)
                if not self.db.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
                    break
            self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(seq)))
            self.db.execute("UPDATE invoices SET state='issued', number=?, issued_at=? WHERE id=?", (number, now_iso(), invoice_id))
        return self.invoice(invoice_id)

    def void(self, invoice_id: int) -> Invoice:
        """Cancel an issued invoice. It keeps its number (so there's no gap) but no longer counts as owed."""
        inv = self.invoice(invoice_id)
        if inv.state == "draft":
            raise InvoiceError("a draft can just be deleted")
        if inv.state == "void":
            raise InvoiceError(f"{inv.label} is already void")
        if inv.payments:
            raise InvoiceError(f"{inv.label} has payments recorded; remove or refund them before voiding")
        with self.db:
            self.db.execute("UPDATE invoices SET state='void' WHERE id=?", (invoice_id,))
        return self.invoice(invoice_id)

    def delete_draft(self, invoice_id: int) -> None:
        inv = self.invoice(invoice_id)
        if inv.state != "draft":
            raise InvoiceError(f"{inv.label} has been issued; void it instead, so its number isn't lost")
        with self.db:
            self.db.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))

    def duplicate(self, invoice_id: int, *, issue_date: date | None = None) -> Invoice:
        """A new draft with the same client, lines, discount and notes: handy for repeat work."""
        old = self.invoice(invoice_id)
        if self.client(old.client_id).archived:
            raise InvoiceError("this client is archived; restore them first")
        new = self.new_invoice(old.client_id, issue_date=issue_date, currency=old.currency)
        new.items = [Item(i.description, i.quantity, i.unit_price, i.tax_rate) for i in old.items]
        new.discount_type, new.discount_value, new.notes, new.reference = old.discount_type, old.discount_value, old.notes, old.reference
        self.save_draft(new)
        return self.invoice(new.id)  # type: ignore[arg-type]

    # -- payments --------------------------------------------------------------------------------

    def add_payment(self, invoice_id: int, payment: Payment, *, allow_overpay: bool = False) -> Invoice:
        inv = self.invoice(invoice_id)
        if inv.state == "draft":
            raise InvoiceError("issue the invoice before recording a payment")
        if inv.state == "void":
            raise InvoiceError(f"{inv.label} is void")
        if payment.amount <= 0:
            raise InvoiceError("a payment must be more than zero")
        if round_money(payment.amount, inv.currency) != payment.amount:
            raise InvoiceError(f"{inv.currency} amounts can't have that many decimal places")
        balance = inv.totals().balance
        if balance <= 0 and not allow_overpay:
            raise InvoiceError(f"{inv.label} is already fully paid")
        if payment.amount > balance and not allow_overpay:
            raise InvoiceError(f"that's more than the {format_money(balance, inv.currency)} still owed on {inv.label}")
        if payment.date < inv.issue_date - timedelta(days=366) or payment.date > date.today() + timedelta(days=1):
            raise InvoiceError("the payment date doesn't look right (it's in the future or long before the invoice)")
        with self.db:
            self.db.execute("INSERT INTO payments (invoice_id, date, amount, method, note) VALUES (?, ?, ?, ?, ?)",
                            (invoice_id, payment.date.isoformat(), str(payment.amount), payment.method.strip(), payment.note.strip()))
        return self.invoice(invoice_id)

    def delete_payment(self, invoice_id: int, payment_id: int) -> Invoice:
        with self.db:
            cur = self.db.execute("DELETE FROM payments WHERE id = ? AND invoice_id = ?", (payment_id, invoice_id))
        if not cur.rowcount:
            raise InvoiceError("there's no such payment on this invoice")
        return self.invoice(invoice_id)

    # -- backup ----------------------------------------------------------------------------------

    def backup(self, target: str | Path) -> Path:
        """A consistent copy of the whole data file, safe to take while the app is running."""
        target = Path(target)
        if target.exists():
            raise InvoiceError(f"{target} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as dest:
            self.db.backup(dest)
        return target
