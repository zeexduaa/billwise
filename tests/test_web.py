import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal as D

import pytest

from billwise.model import Client, Item
from billwise.store import Store
from billwise.web import App, make_server

TODAY = date(2026, 6, 15)


@pytest.fixture
def app():
    store = Store()
    store.save_settings({"business_name": "Ali Studio"})
    return App(store, today=lambda: TODAY)


def post(app, path, **fields):
    form = {k: v if isinstance(v, list) else [v] for k, v in fields.items()}
    form.setdefault("csrf", [app.token])
    return app.request("POST", path, form)


def text(response):
    return response.body.decode()


def test_first_run_guides_you(app):
    page = text(app.request("GET", "/"))
    assert "Add your first client" in page
    assert app.request("GET", "/invoices/new").headers["Location"] == "/clients/new"


def test_full_flow(app):
    r = post(app, "/clients/new", name="Acme Corp", email="ap@acme.test", address="1 Main St\nSpringfield")
    assert r.status == 303
    client_id = int(r.headers["Location"].rsplit("/", 1)[1])
    r = post(app, "/invoices/new", client=str(client_id))
    edit = r.headers["Location"]
    assert edit.endswith("/edit")
    inv_id = int(edit.split("/")[2])
    assert "Edit Draft" in text(app.request("GET", edit))
    r = post(app, edit, client=str(client_id), issue_date="2026-06-01", due_date="2026-06-30", currency="usd", reference="PO-9",
             desc=["Website", "Hosting", ""], qty=["1", "12", "1"], price=["1,200", "15.50", ""], tax=["", "17", ""],
             discount_type="percent", discount_value="10", notes="Thanks!", then="view")
    assert r.status == 303
    inv = app.store.invoice(inv_id)
    assert len(inv.items) == 2 and inv.currency == "USD" and inv.totals().total == D("1275.86")
    r = post(app, f"/invoices/{inv_id}/issue")
    assert "Issued as INV-2026-0001" in text(app.request("GET", r.headers["Location"]))
    preview = text(app.request("GET", f"/invoices/{inv_id}/print"))
    assert "INV-2026-0001" in preview and "Springfield" in preview and "$1,275.86" in preview and "Thanks!" in preview
    r = post(app, f"/invoices/{inv_id}/payments", amount="275.86", date="2026-06-10", method="Bank transfer")
    assert "$1,000.00 still owed" in text(app.request("GET", r.headers["Location"]))
    assert "Partly paid" in text(app.request("GET", "/invoices"))
    assert "$1,000.00" in text(app.request("GET", "/reports"))
    csv_text = app.request("GET", "/export/invoices.csv").body.decode("utf-8-sig")
    assert "INV-2026-0001,partial,Acme Corp" in csv_text


def test_mistakes_keep_what_was_typed(app):
    app.store.add_client(Client("Acme"))
    inv = app.store.new_invoice(1, issue_date=TODAY)
    r = post(app, f"/invoices/{inv.id}/edit", client="1", issue_date="2026-06-15", due_date="2026-06-30", currency="USD",
             desc=["Design work", "Second line"], qty=["2", "1"], price=["150", "12,5"], tax=["", ""], discount_type="none", then="view")
    page = text(r)
    assert r.status == 400 and "price on line 2" in page
    assert "Design work" in page and "12,5" in page   # nothing has to be typed again
    assert app.store.invoice(inv.id).items == []


def test_line_without_price(app):
    app.store.add_client(Client("Acme"))
    inv = app.store.new_invoice(1, issue_date=TODAY)
    r = post(app, f"/invoices/{inv.id}/edit", client="1", issue_date="2026-06-15", due_date="2026-06-30", currency="USD",
             desc=["Forgot the price"], qty=["1"], price=[""], tax=[""], discount_type="none")
    assert r.status == 400 and "needs a price" in text(r)


def test_everything_typed_is_escaped(app):
    evil = "<script>alert(1)</script>"
    post(app, "/clients/new", name=evil, address=evil)
    inv = app.store.new_invoice(1, issue_date=TODAY)
    inv.items = [Item(evil, D(1), D(1))]
    inv.notes = evil
    app.store.save_draft(inv)
    for path in ("/clients", "/clients/1", f"/invoices/{inv.id}", f"/invoices/{inv.id}/print", f"/invoices/{inv.id}/edit", "/invoices?q=" + evil):
        assert "<script>alert" not in text(app.request("GET", path)), path


def test_forms_need_the_token(app):
    r = app.request("POST", "/clients/new", {"name": ["Acme"]})
    assert r.status == 403
    r = app.request("POST", "/clients/new", {"name": ["Acme"], "csrf": ["wrong"]})
    assert r.status == 403
    assert app.store.clients() == []


def test_other_sites_are_refused(app):
    app.allowed_hosts = {"127.0.0.1:8765"}
    assert app.request("GET", "/", headers={"Host": "evil.test:8765"}).status == 421
    r = app.request("POST", "/clients/new", {"name": ["X"], "csrf": [app.token]}, {"Host": "127.0.0.1:8765", "Origin": "http://evil.test"})
    assert r.status == 403
    assert app.request("GET", "/", headers={"Host": "127.0.0.1:8765"}).status == 200
    for origin in ("http://127.0.0.1:8765", "null"):  # browsers send "null" under some referrer policies
        r = app.request("POST", "/clients/new", {"name": ["Y"], "csrf": [app.token]}, {"Host": "127.0.0.1:8765", "Origin": origin})
        assert r.status == 303, origin


def test_removed_lines_stay_hidden_on_phones(app):
    css = app.request("GET", "/static/app.css").body.decode()
    assert "[hidden] { display: none !important; }" in css


def test_security_headers(app):
    headers = app.request("GET", "/").headers
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_issued_invoice_cannot_be_edited_via_the_form(app):
    app.store.add_client(Client("Acme"))
    inv = app.store.new_invoice(1, issue_date=TODAY)
    inv.items = [Item("Work", D(1), D(100))]
    app.store.save_draft(inv)
    app.store.issue(inv.id)
    r = post(app, f"/invoices/{inv.id}/edit", client="1", issue_date="2026-06-15", due_date="2026-06-30", currency="USD",
             desc=["Work"], qty=["1"], price=["1"], tax=[""], discount_type="none")
    assert r.status == 303 and app.store.invoice(inv.id).totals().total == 100
    assert app.request("GET", f"/invoices/{inv.id}/edit").status == 303


def test_errors_are_pages_not_crashes(app):
    app.store.add_client(Client("Acme"))
    assert app.request("GET", "/invoices/999").status == 400
    assert app.request("GET", "/nope").status == 404
    assert app.request("GET", "/reports?from=garbage").status == 400
    assert app.request("POST", "/reports", {"csrf": [app.token]}).status == 405
    r = post(app, "/settings", currency="dollars")
    assert r.status == 400 and "3-letter" in text(r)


def test_settings_and_backup(app):
    r = post(app, "/settings", business_name="New Name", currency="EUR", terms_days="7", number_format="{YY}-{NNN}")
    assert r.status == 303
    assert app.store.setting("business_name") == "New Name" and app.store.setting("currency") == "EUR"
    backup = app.request("GET", "/backup")
    assert backup.body.startswith(b"SQLite format 3")


def test_real_server_round_trip(tmp_path):
    server = make_server(Store(tmp_path / "x.db"), port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        page = urllib.request.urlopen(base + "/clients/new").read().decode()
        token = re.search(r"name='csrf' value='([^']+)'", page).group(1)
        data = urllib.parse.urlencode({"csrf": token, "name": "Real Client"}).encode()
        urllib.request.urlopen(base + "/clients/new", data=data)
        assert "Real Client" in urllib.request.urlopen(base + "/clients").read().decode()
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Host": "attacker.test"}))
        assert err.value.code == 421
    finally:
        server.shutdown()
        server.server_close()


def test_quote_flow_in_the_browser(app):
    app.store.add_client(Client("Acme"))
    r = post(app, "/quotes/new", client="1")
    quote_id = int(r.headers["Location"].split("/")[2])
    edit = text(app.request("GET", f"/invoices/{quote_id}/edit"))
    assert "Valid until" in edit and "Save and send" in edit
    r = post(app, f"/invoices/{quote_id}/edit", client="1", issue_date="2026-06-15", due_date="2026-07-15", currency="USD",
             desc=["Website"], qty=["1"], price=["2,000"], tax=[""], discount_type="none", then="issue")
    page = text(app.request("GET", r.headers["Location"]))
    assert "Issued as Q-2026-0001" in page and "Turn into invoice" in page and "Record a payment" not in page
    assert "Quotes waiting for an answer" in text(app.request("GET", "/"))
    assert "Q-2026-0001" in text(app.request("GET", "/quotes?status=open"))
    assert "Q-2026-0001" not in text(app.request("GET", "/invoices"))
    r = post(app, f"/invoices/{quote_id}/decline")
    assert "marked declined" in text(app.request("GET", r.headers["Location"]))
    assert "Turn into invoice" not in text(app.request("GET", f"/invoices/{quote_id}"))
    post(app, f"/invoices/{quote_id}/reopen")
    r = post(app, f"/invoices/{quote_id}/convert")
    invoice_id = int(r.headers["Location"].split("/")[2])
    page = text(app.request("GET", f"/invoices/{invoice_id}"))
    assert "Made from quote" in page and "Q-2026-0001" in page
    assert "Became invoice" in text(app.request("GET", f"/invoices/{quote_id}"))
    assert "Invoiced" in text(app.request("GET", "/clients/1"))
    assert post(app, f"/invoices/{quote_id}/convert").status == 400   # only once
    assert post(app, f"/invoices/{invoice_id}/accept").status == 400  # not a quote


def test_client_with_only_quotes_offers_no_delete(app):
    app.store.add_client(Client("Acme"))
    app.store.new_invoice(1, kind="quote")
    page = text(app.request("GET", "/clients/1"))
    assert "/clients/1/delete" not in page and "<h2>Quotes</h2>" in page


def test_quote_settings(app):
    assert "Quote numbers" in text(app.request("GET", "/settings"))
    assert post(app, "/settings", quote_number_format="EST-{NNN}", quote_valid_days="10").status == 303
    assert app.store.setting("quote_valid_days") == "10"
    assert post(app, "/settings", quote_valid_days="0").status == 400


def test_client_statement(app):
    app.store.save_settings({"payment_details": "IBAN PK00 1234"})
    app.store.add_client(Client("Acme", address="1 Main St"))
    for day, amount in ((date(2026, 4, 1), "500"), (date(2026, 5, 1), "300")):
        inv = app.store.new_invoice(1, issue_date=day)
        inv.items = [Item("Work", D(1), D(amount))]
        app.store.save_draft(inv)
        app.store.issue(inv.id)
    from billwise.model import Payment
    app.store.add_payment(1, Payment(date(2026, 4, 15), D("500"), "Cash"))
    assert "/clients/1/statement" in text(app.request("GET", "/clients/1"))
    page = text(app.request("GET", "/clients/1/statement"))
    assert "STATEMENT" in page and "Payment for INV-2026-0001, Cash" in page and "Balance due" in page and "$300.00" in page
    assert "IBAN" in page
    page = text(app.request("GET", "/clients/1/statement?from=2026-05-01&to=2026-06-15"))
    assert "Balance brought forward" in page and "INV-2026-0001" not in page
    assert app.request("GET", "/clients/1/statement?from=2026-06-01&to=2026-01-01").status == 400
