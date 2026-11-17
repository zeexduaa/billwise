import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from billwise import reports
from billwise.model import Client, InvoiceError, Item, Payment
from billwise.store import Store, StoreError, next_number

TODAY = date.today()


@pytest.fixture
def store():
    s = Store()
    s.save_settings({"business_name": "Ali Studio", "terms_days": "14"})
    s.add_client(Client("Acme Corp", email="ap@acme.test"))
    s.add_client(Client("Acme Bakery"))
    s.add_client(Client("Zed Ltd"))
    return s


def draft(store, *items, client=1, issue_date=None):
    inv = store.new_invoice(client, issue_date=issue_date or TODAY)
    inv.items = list(items) or [Item("Work", D(1), D("100"))]
    store.save_draft(inv)
    return store.invoice(inv.id)


def test_new_invoice_uses_payment_terms(store):
    inv = store.new_invoice(1, issue_date=date(2026, 3, 1))
    assert inv.due_date == date(2026, 3, 15) and inv.state == "draft" and inv.number is None


def test_amounts_come_back_exactly(store, tmp_path):
    path = tmp_path / "b.db"
    s = Store(path)
    s.add_client(Client("A"))
    inv = s.new_invoice(1)
    inv.items = [Item("x", D("3"), D("0.1"), D("7.25"))]
    s.save_draft(inv)
    s.close()
    again = Store(path).invoice(inv.id)
    assert again.items[0].unit_price == D("0.1") and again.items[0].tax_rate == D("7.25")


def test_numbers_are_sequential_per_year_without_gaps(store):
    a = draft(store, issue_date=date(2026, 1, 5))
    b = draft(store, issue_date=date(2026, 2, 5))
    store.delete_draft(b.id)                       # a deleted draft never had a number, so it leaves no gap
    c = draft(store, issue_date=date(2026, 3, 5))
    d = draft(store, issue_date=date(2027, 1, 2))
    assert [store.issue(x.id).number for x in (a, c, d)] == ["INV-2026-0001", "INV-2026-0002", "INV-2027-0001"]


def test_custom_number_format_and_collisions(store):
    store.save_settings({"number_format": "A{NNN}"})
    first = store.issue(draft(store).id)
    assert first.number == "A001"
    store.save_settings({"number_format": "A{NNNN}"})
    store.save_settings({"number_format": "A{NNN}"})
    store.db.execute("UPDATE settings SET value = '0' WHERE key = 'seq:all'")  # as if the counter were reset
    assert store.issue(draft(store).id).number == "A002"  # skips the number already used


def test_number_format_needs_a_counter(store):
    with pytest.raises(InvoiceError):
        store.save_settings({"number_format": "INV-{YYYY}"})
    assert next_number("{YY}/{NN}", date(2026, 1, 1), 7) == "26/07"


def test_issued_invoices_are_locked(store):
    inv = store.issue(draft(store).id)
    inv.items.append(Item("sneaky", D(1), D("5")))
    with pytest.raises(InvoiceError, match="can't be edited"):
        store.save_draft(inv)
    with pytest.raises(InvoiceError):
        store.delete_draft(inv.id)
    with pytest.raises(InvoiceError):
        store.issue(inv.id)


def test_cannot_issue_empty_or_negative(store):
    inv = store.new_invoice(1)
    with pytest.raises(InvoiceError, match="at least one line"):
        store.issue(inv.id)
    inv.items = [Item("refund", D(1), D("-10"))]
    store.save_draft(inv)
    with pytest.raises(InvoiceError, match="negative"):
        store.issue(inv.id)


def test_due_date_before_invoice_date(store):
    inv = store.new_invoice(1)
    inv.due_date = inv.issue_date - timedelta(days=1)
    with pytest.raises(InvoiceError):
        store.save_draft(inv)


def test_payments(store):
    inv = store.issue(draft(store, Item("Work", D(1), D("100"))).id)
    with pytest.raises(InvoiceError, match="more than"):
        store.add_payment(inv.id, Payment(TODAY, D("150")))
    inv = store.add_payment(inv.id, Payment(TODAY, D("60"), "Cash"))
    assert inv.status(TODAY) == "partial" and inv.totals().balance == D("40")
    inv = store.add_payment(inv.id, Payment(TODAY, D("40")))
    assert inv.status(TODAY) == "paid"
    with pytest.raises(InvoiceError, match="already fully paid"):
        store.add_payment(inv.id, Payment(TODAY, D("1")))
    inv = store.delete_payment(inv.id, inv.payments[-1].id)
    assert inv.status(TODAY) == "partial"


def test_payment_checks(store):
    d = draft(store)
    with pytest.raises(InvoiceError, match="issue the invoice"):
        store.add_payment(d.id, Payment(TODAY, D("1")))
    inv = store.issue(d.id)
    with pytest.raises(InvoiceError, match="decimal places"):
        store.add_payment(inv.id, Payment(TODAY, D("1.005")))
    with pytest.raises(InvoiceError, match="date"):
        store.add_payment(inv.id, Payment(TODAY + timedelta(days=30), D("1")))
    with pytest.raises(InvoiceError):
        store.add_payment(inv.id, Payment(TODAY, D("0")))


def test_void(store):
    inv = store.issue(draft(store).id)
    store.add_payment(inv.id, Payment(TODAY, D("10")))
    with pytest.raises(InvoiceError, match="payments"):
        store.void(inv.id)
    inv = store.delete_payment(inv.id, store.invoice(inv.id).payments[0].id)
    voided = store.void(inv.id)
    assert voided.status(TODAY) == "void" and voided.number == inv.number
    with pytest.raises(InvoiceError):
        store.add_payment(inv.id, Payment(TODAY, D("1")))


def test_duplicate(store):
    inv = draft(store, Item("Monthly retainer", D(1), D("800"), D(5)))
    inv.discount_type, inv.discount_value, inv.notes = "percent", D(5), "Thanks!"
    store.save_draft(inv)
    store.issue(inv.id)
    copy = store.duplicate(inv.id)
    assert copy.state == "draft" and copy.number is None and copy.id != inv.id
    assert copy.items[0].description == "Monthly retainer" and copy.discount_value == 5 and copy.notes == "Thanks!"


def test_find_client(store):
    assert store.find_client("zed").name == "Zed Ltd"
    assert store.find_client("2").name == "Acme Bakery"
    assert store.find_client("acme corp").name == "Acme Corp"
    with pytest.raises(InvoiceError, match="matches 2"):
        store.find_client("acme")
    with pytest.raises(InvoiceError):
        store.find_client("nobody")


def test_clients_with_invoices_can_only_be_archived(store):
    draft(store, client=3)
    with pytest.raises(InvoiceError, match="archived"):
        store.delete_client(3)
    store.delete_client(2)
    client = store.client(3)
    client.archived = True
    store.update_client(client)
    assert [c.name for c in store.clients()] == ["Acme Corp"]
    with pytest.raises(InvoiceError, match="archived"):
        store.new_invoice(3)


def test_find_invoice(store):
    inv = store.issue(draft(store).id)
    assert store.find_invoice(inv.number.lower()).id == inv.id
    assert store.find_invoice(str(inv.id)).id == inv.id
    with pytest.raises(InvoiceError):
        store.find_invoice("INV-1999-0001")


def test_settings_are_checked(store):
    for bad in ({"currency": "dollars"}, {"terms_days": "-3"}, {"default_tax_rate": "150"}, {"nope": "x"}):
        with pytest.raises(ValueError):
            store.save_settings(bad)
    store.save_settings({"currency": "pkr", "terms_days": "07"})
    assert store.setting("currency") == "PKR" and store.setting("terms_days") == "7"


def test_backup_is_a_working_copy(store, tmp_path):
    store.issue(draft(store).id)
    copy = store.backup(tmp_path / "copy.db")
    assert len(Store(copy).invoices()) == 1
    with pytest.raises(InvoiceError):
        store.backup(copy)


def test_newer_data_file_is_refused(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(StoreError, match="newer"):
        Store(path)


def test_not_a_database(tmp_path):
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StoreError):
        Store(path)


def test_reports(store):
    today = date(2026, 6, 30)
    a = store.issue(draft(store, Item("A", D(1), D("100"), D(10)), issue_date=date(2026, 5, 1)).id)    # due May 15: 46 days late
    b = store.issue(draft(store, Item("B", D(1), D("200")), issue_date=date(2026, 6, 20)).id)           # due July 4: not due yet
    store.add_payment(b.id, Payment(date(2026, 6, 25), D("50")))
    store.void(store.issue(draft(store, Item("C", D(1), D("999"))).id).id)
    invoices = store.invoices()
    aging = reports.aging(invoices, today)["USD"]
    assert aging.buckets == [D("150"), 0, D("110"), 0, 0] and aging.total == D("260")
    months = reports.monthly(invoices, date(2026, 1, 1), today)["USD"]
    assert months == {"2026-05": {"invoiced": D("110"), "received": 0}, "2026-06": {"invoiced": D("200"), "received": D("50")}}
    assert reports.tax_summary(invoices)["USD"] == {D(10): {"base": D("100"), "tax": D("10")}}
    assert reports.client_balances(invoices, today) == {(1, "USD"): D("260")}
    assert a.id in [i.id for i in reports.open_invoices(invoices, today)]


def test_csv_is_safe_for_spreadsheets(store):
    store.add_client(Client('=HYPERLINK("http://evil.test","click")'))
    inv = store.issue(draft(store, client=4).id)
    store.add_payment(inv.id, Payment(TODAY, D("5"), "-cash"))
    names = {c.id: c.name for c in store.clients()}
    text = reports.invoices_csv(store.invoices(), names, TODAY)
    assert "'=HYPERLINK" in text and "100.00" in text
    assert "'-cash" in reports.payments_csv(store.invoices(), names)
