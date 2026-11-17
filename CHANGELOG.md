# Changelog

## 0.2.0 — unreleased

- **Quotes.** Draft and send quotes with their own number format and counter, record the client's answer, and convert an accepted quote into a draft invoice (once). Quotes are kept out of everything that counts money: what's owed, monthly income, tax and the CSV export. Available as `billwise quote ...` and a Quotes tab.
- **Client statements.** A printable statement of a client's invoices and payments with a running balance (`billwise client statement`, or the Statement button on a client's page).
- Data files from 0.1 are upgraded automatically the first time they're opened.
- Invoice commands refuse a quote's number with a hint to use `billwise quote`, so a quote can't be paid or voided through the wrong command.

### Fixed since 0.1

- Removed invoice lines stayed visible in the editor on phones.
- `billwise serve` crashed with a traceback when the port was in use.
- Paying an already-paid invoice gave a confusing "more than $0.00 still owed" message.

## 0.1.0 — unreleased

The first version.

- Clients: add, edit, archive and restore. Clients with invoices can't be deleted.
- Invoices: drafts, lines with per-line tax, percent or fixed discounts, reference and notes. Issuing gives a gap-free number and locks the invoice. Issued invoices can be voided or duplicated.
- Payments: full and partial, with automatic unpaid / partly paid / overdue / paid status. Overpayments are refused.
- Printable invoice page with status stamp and payment instructions, ready for "Save as PDF".
- Reports: aging of money owed, invoiced and received per month, and tax charged per rate. CSV export of invoices and payments; backup of all data.
- Browser app served on 127.0.0.1 with form tokens, host checks and a strict Content Security Policy; command line for everything.
