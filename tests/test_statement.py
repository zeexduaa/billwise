from datetime import date
from decimal import Decimal as D

import pytest

from billwise import reports
from billwise.model import Client, Item, Payment
from billwise.store import Store


@pytest.fixture
def store():
    s = Store()
    s.add_client(Client("Acme"))
    return s


def issued(store, day, amount, currency="USD", reference=""):
    inv = store.new_invoice(1, issue_date=day, currency=currency)
    inv.items, inv.reference = [Item("Work", D(1), D(amount))], reference
    store.save_draft(inv)
    return store.issue(inv.id)


def test_running_balance_with_brought_forward(store):
    a = issued(store, date(2026, 1, 10), "1000")
    store.add_payment(a.id, Payment(date(2026, 1, 20), D("400"), "Bank transfer"))
    b = issued(store, date(2026, 2, 5), "250", reference="PO-9")
    store.add_payment(b.id, Payment(date(2026, 2, 5), D("250")))  # paid the same day it was issued
    store.add_payment(a.id, Payment(date(2026, 2, 28), D("100")))
    st = reports.statement(store.invoices(client_id=1), date(2026, 2, 1), date(2026, 2, 28))["USD"]
    assert st.opening == D("600")
    assert [(l.text, l.charge, l.credit, l.balance) for l in st.lines] == [
        ("Invoice INV-2026-0002 (PO-9)", D("250"), 0, D("850")),
        ("Payment for INV-2026-0002", 0, D("250"), D("600")),
        ("Payment for INV-2026-0001", 0, D("100"), D("500")),
    ]
    assert st.closing == D("500")


def test_without_a_period_everything_is_listed(store):
    a = issued(store, date(2026, 1, 10), "1000")
    store.add_payment(a.id, Payment(date(2026, 1, 20), D("1000"), "Cash"))
    st = reports.statement(store.invoices(client_id=1))["USD"]
    assert st.opening == 0 and len(st.lines) == 2 and st.closing == 0
    assert st.lines[1].text == "Payment for INV-2026-0001, Cash"


def test_currencies_drafts_voids_and_quotes(store):
    issued(store, date(2026, 3, 1), "100")
    issued(store, date(2026, 3, 2), "50", currency="EUR")
    store.void(issued(store, date(2026, 3, 3), "999").id)
    store.new_invoice(1, issue_date=date(2026, 3, 4))
    q = store.new_invoice(1, kind="quote", issue_date=date(2026, 3, 5))
    q.items = [Item("x", D(1), D("5000"))]
    store.save_draft(q)
    store.issue(q.id)
    statements = reports.statement(store.invoices(client_id=1, kind=None))
    assert {cur: s.closing for cur, s in statements.items()} == {"EUR": D("50"), "USD": D("100")}
    assert len(statements["USD"].lines) == 1


def test_a_period_after_everything_still_shows_the_balance(store):
    issued(store, date(2026, 1, 10), "300")
    st = reports.statement(store.invoices(client_id=1), date(2026, 6, 1), date(2026, 6, 30))["USD"]
    assert st.lines == [] and st.opening == st.closing == D("300")
