from datetime import date

import pytest

from billwise.cli import main, parse_discount, parse_line
from billwise.model import InvoiceError
from billwise.store import Store

TODAY = date(2026, 6, 15)


@pytest.fixture
def run(tmp_path, capsys):
    db = tmp_path / "data.db"

    def go(*args):
        code = main(["--db", str(db), *args], today=TODAY)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    go.db = db
    return go


def test_a_whole_job(run, tmp_path):
    assert run("setup", "--business-name", "Ali Studio", "--terms-days", "10")[0] == 0
    assert "Added Acme" in run("client", "add", "Acme", "--email", "ap@acme.test")[1]
    code, out, _ = run("invoice", "new", "acme", "--date", "2026-06-01", "-l", "Design | 2 | 150", "-l", "Hosting | 12 | 9.99 | 17", "--issue")
    assert code == 0 and "INV-2026-0001" in out and "due 2026-06-11" in out
    code, out, _ = run("invoice", "show", "INV-2026-0001")
    assert "Overdue" in out and "$440.26" in out  # 300 + 119.88 + 17% of 119.88 (20.38)
    code, out, _ = run("pay", "inv-2026-0001", "100", "--date", "2026-06-12")
    assert "still owed" in out
    code, out, _ = run("pay", "INV-2026-0001")
    assert "fully paid" in out
    assert "Paid" in run("invoice", "list")[1]
    assert "Nobody owes you" in run("report", "owed")[1]
    out_file = tmp_path / "inv.html"
    run("invoice", "html", "1", "-o", str(out_file))
    assert "Ali Studio" in out_file.read_text(encoding="utf-8")
    csv_file = tmp_path / "inv.csv"
    run("export", "invoices", "-o", str(csv_file))
    assert csv_file.read_bytes().startswith(b"\xef\xbb\xbfnumber,")


def test_draft_editing(run):
    run("client", "add", "Acme")
    run("invoice", "new", "Acme")
    assert "now totals $50.00" in run("invoice", "add-line", "1", "Consulting | 50")[1]
    run("invoice", "add-line", "1", "Travel | 1 | 20")
    assert "now totals $50.00" in run("invoice", "remove-line", "1", "2")[1]
    run("invoice", "set", "1", "--discount", "10%", "--reference", "PO-1")
    _, out, _ = run("invoice", "show", "1")
    assert "-$5.00" in out and "PO-1" in out and "$45.00" in out


def test_errors_are_short_and_clear(run):
    code, _, err = run("invoice", "new", "nobody")
    assert code == 1 and "no client matches" in err
    run("client", "add", "Acme")
    code, _, err = run("invoice", "new", "Acme", "-l", "Bad | x")
    assert code == 1 and "price" in err
    assert Store(run.db).invoices() == []  # the failed invoice wasn't left behind as an empty draft
    code, _, err = run("invoice", "new", "Acme", "--due", "2020-01-01")
    assert code == 1 and "due date" in err


def test_line_and_discount_parsing():
    from decimal import Decimal as D
    item = parse_line("Logo | 2 | 1,500 | 17", D(0))
    assert (item.description, item.quantity, item.unit_price, item.tax_rate) == ("Logo", 2, D(1500), 17)
    assert parse_line("Logo | 99", D(5)).tax_rate == 5
    with pytest.raises(InvoiceError):
        parse_line("just words", D(0))
    assert parse_discount("10%") == ("percent", 10)
    assert parse_discount("25") == ("amount", 25)
    assert parse_discount("none") == ("none", 0)


def test_port_already_in_use(run):
    import socket
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, _, err = run("serve", "--port", str(port), "--no-browser")
    assert code == 1 and "already running" in err and "Traceback" not in err


def test_no_command_prints_help(run):
    code, out, _ = run()
    assert code == 0 and "serve" in out
