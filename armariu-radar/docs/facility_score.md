# First-facility score for invoice and asset finance (BACKTEST, 9 Nov 2026)

Question: among UK companies that already have a registered secured loan but have never had an
invoice-finance, asset-based or asset-finance charge, can public filings rank which will register their
first such charge in the next 12 months? Code: `radar/facility_backtest.py`
(`python -m radar.facility_backtest`). Bar set before running: top 10% at least 3 times the average rate.

| | Result |
|---|---|
| Test population | 52,879 company-dates (cutoffs Jul 2024 to Jul 2025), 150 first-facility events |
| Average rate | 0.28% a year |
| Top 10% rate | 1.14%, which is 4.1 times the average (95% range 2.9 to 5.2) |
| Share of all events in the top 10% / top 20% | 41% / 57% |
| AUC | 0.78 |
| Bar (3x, lower end above 1x) | Passed |

Read this carefully:
- The lift is real on this test, but the absolute rate is low: about 1 in 90 of the top 10% takes a first
  invoice or asset-finance charge within a year. It ranks timing, it does not find many ready buyers.
- Only 150 test events and 125 training events. The range is wide and one split is one test.
- The population is firms that already have a registered loan, so this is "next kind of facility", not
  "first ever borrowing". Firms with no charge at all are not covered.
- Lender classes come from the radar's lender table, which is incomplete (about 85% of names). Missed
  asset-finance lenders would count as non-events and understate the true rate.
- Not compared with any bureau or a simple rule (for example "has a bank loan and is young").
  Before any buyer sees it, run that comparison.
- Not yet tied to a buyer: invoice and asset-finance lenders may care about the top 10% only if the
  hit rate justifies outreach cost, which is the question for one conversation.
