# Refinance watch on a random sample of all companies (BACKTEST)

**Why.** The radar's own data holds only companies with a charge filing after 20 Oct 2026 (a new loan,
or for about 30% only a repayment), plus their full history. Firms that went quiet are missing, so the
hit rates in docs/refi_odds_table.md run high. This test scores the same live rw3 list on companies drawn
at random.

**How.**
- `python -m radar.population sample --target 1500` draws England and Wales company numbers (1 to
  16.6 million) in a fixed random order, keeps every company whose record links to a charge register,
  and fetches that register. Dissolved companies still on the register are kept. It is resumable, stays
  under the Companies House rate limit, and pauses while the pipeline runs. Companies House's free
  bulk company file would be quicker, but it could not be downloaded from the cloud session.
- `python -m radar.population evaluate` runs radar.refi_accuracy on the sample at 90 days and 12 months.
  The points, odds and lender statistics are learned from the radar's own data, as the live list is.
  Same test dates and outcome rules as the main table.
- Not covered: Scottish and Northern Irish companies, and companies removed from the register.

**Results.** Pending: the sample is still being fetched (4 Nov 2026).
