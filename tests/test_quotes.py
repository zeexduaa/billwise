from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from billwise import reports
from billwise.model import Client, InvoiceError, Item, Payment
from billwise.store import Store

TODAY = date.today()


@pytest.fixture
def store():
    s = Store()
    s.add_client(Client("Acme"))
    return s


def sent_quote(store, *items, issue_date=None):
    q = store.new_invoice(1, kind="quote", issue_date=issue_date or TODAY)
    q.items = list(items) or [Item("Website", D(1), D("1500"), D(10))]
    q.discount_type, q.discount_value, q.notes = "percent", D(5), "Valid for 30 days."
    store.save_draft(q)
    return store.issue(q.id)


def test_quotes_have_their_own_numbers_and_validity(store):
    store.save_settings({"quote_valid_days": "14"})
    draft = store.new_invoice(1, kind="quote", issue_date=date(2026, 3, 1))
    assert draft.label == f"Draft quote {draft.id}" and draft.due_date == date(2026, 3, 15)
    q = sent_quote(store)
    inv = store.new_invoice(1)
    inv.items = [Item("x", D(1), D(1))]
    store.save_draft(inv)
    assert q.number == f"Q-{TODAY.year}-0001"
    assert store.issue(inv.id).number == f"INV-{TODAY.year}-0001"  # sending a quote didn't use up an invoice number
    assert store.settings()["quote_number_format"] == "Q-{YYYY}-{NNNN}"  # counters aren't mixed into the settings


def test_quote_status(store):
    q = sent_quote(store)
    assert q.status(TODAY) == "sent"
    assert q.status(q.due_date + timedelta(days=1)) == "expired"
    assert store.set_outcome(q.id, "declined").status(TODAY) == "declined"
    assert store.set_outcome(q.id, "").status(TODAY) == "sent"  # undo a mis-click
    assert store.set_outcome(q.id, "accepted").status(TODAY) == "accepted"


def test_convert_to_invoice(store):
    q = sent_quote(store)
    inv = store.convert(q.id, issue_date=TODAY)
    assert inv.kind == "invoice" and inv.state == "draft"
    assert inv.totals().total == q.totals().total and inv.notes == q.notes and inv.reference == q.number
    q = store.invoice(q.id)
    assert q.converted_to == inv.id and q.status(TODAY) == "converted"
    with pytest.raises(InvoiceError, match="already become"):
        store.convert(q.id)
    with pytest.raises(InvoiceError, match="already become"):
        store.void(q.id)


def test_quote_rules(store):
    draft = store.new_invoice(1, kind="quote")
    with pytest.raises(InvoiceError, match="send the quote first"):
        store.set_outcome(draft.id, "accepted")
    q = sent_quote(store)
    with pytest.raises(InvoiceError, match="is a quote"):
        store.add_payment(q.id, Payment(TODAY, D("1")))
    store.set_outcome(q.id, "declined")
    with pytest.raises(InvoiceError, match="declined"):
        store.convert(q.id)
    with pytest.raises(InvoiceError):
        store.set_outcome(q.id, "maybe")
    inv = store.new_invoice(1)
    with pytest.raises(InvoiceError, match="not a quote"):
        store.set_outcome(inv.id, "accepted")
    assert store.void(q.id).status(TODAY) == "void"


def test_duplicate_keeps_the_kind(store):
    assert store.duplicate(sent_quote(store).id).kind == "quote"


def test_quotes_never_count_as_money_owed(store):
    sent_quote(store, issue_date=TODAY - timedelta(days=100))
    assert store.invoices() == []
    assert len(store.invoices(kind="quote")) == 1 and len(store.invoices(kind=None)) == 1
    everything = store.invoices()
    assert reports.aging(everything, TODAY) == {} and reports.monthly(everything) == {} and reports.tax_summary(everything) == {}


def test_printed_quote(store):
    from billwise.render import invoice_document
    store.save_settings({"payment_details": "IBAN PK00 1234"})
    q = sent_quote(store)
    page = invoice_document(q, store.client(1), store.settings(), TODAY)
    assert "<h2>QUOTE</h2>" in page and "Valid until" in page and "Quoted total" in page
    assert "IBAN" not in page          # nobody should pay a quote
    assert "<h2>INVOICE</h2>" not in page
    accepted = store.set_outcome(q.id, "accepted")
    assert ">Accepted</div>" in invoice_document(accepted, store.client(1), store.settings(), TODAY)
