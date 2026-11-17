# Roadmap

billwise 0.1 covers the core of invoicing: clients, invoices, payments, reports and a printable invoice. These are the next
pieces, roughly in order. Each one is a real gap a small business will hit.

## Next

- [ ] **Credit notes.** The proper way to correct an issued invoice: a numbered credit note linked to the original, instead of voiding it.
- [ ] **Recurring invoices.** "Every month on the 1st" for retainers and subscriptions, creating drafts (or issuing them) automatically.
- [ ] **PDF files directly.** Today you print to PDF from the browser; generate the PDF file itself so it can be attached or archived.
- [ ] **Email an invoice.** Send it from billwise with the PDF attached (SMTP settings), and payment reminders for overdue invoices.

## Later

- [ ] **Your logo** on invoices, and a choice of a few invoice layouts and accent colours.
- [ ] **Products and services list** with saved prices and tax rates, to pick lines instead of typing them.
- [ ] **Late fees** and early-payment discounts written into the payment terms.
- [ ] **Expenses**, so the reports can show profit, not just income.
- [ ] **Multiple businesses** from one install, with a switcher instead of separate `--db` files.
- [ ] **Translations** of the printed invoice (labels, date formats) for clients in other countries.
- [ ] **Import** clients and past invoices from a CSV export of another app.

## Done

- [x] Clients with archive instead of delete
- [x] Drafts, issuing with gap-free numbering, locking, voiding and duplicating
- [x] Per-line tax rates, percent and fixed discounts shared correctly between rates
- [x] Exact money arithmetic with per-currency rounding
- [x] Partial payments, automatic status and overdue tracking
- [x] Aging, monthly and tax reports; CSV export; backups
- [x] Browser app (desktop and phone) and command line
- [x] Quotes with their own numbering, accepted / declined, and converting to an invoice (0.2)
- [x] Client statements with a running balance and balance brought forward (0.2)
