# Changelog

## 0.1.0 — unreleased

The first version.

- Clients: add, edit, archive and restore. Clients with invoices can't be deleted.
- Invoices: drafts, lines with per-line tax, percent or fixed discounts, reference and notes. Issuing gives a gap-free number and locks the invoice. Issued invoices can be voided or duplicated.
- Payments: full and partial, with automatic unpaid / partly paid / overdue / paid status. Overpayments are refused.
- Printable invoice page with status stamp and payment instructions, ready for "Save as PDF".
- Reports: aging of money owed, invoiced and received per month, and tax charged per rate. CSV export of invoices and payments; backup of all data.
- Browser app served on 127.0.0.1 with form tokens, host checks and a strict Content Security Policy; command line for everything.
