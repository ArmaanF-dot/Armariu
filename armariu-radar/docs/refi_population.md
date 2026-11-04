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

**Results (first 1,012 companies, 4 Nov 2026).** The same live rw3 score, tested on a random sample.
Switched lender, with the 95% range in brackets. "Radar data" is the table in docs/refi_odds_table.md.

| | Random sample | Radar data |
|---|---|---|
| Ordinary firms, all, 90 days | 0.4% (0.2 to 0.6) | 1.4% |
| Ordinary firms, top 10%, 90 days | 1.7% (0.5 to 3.2), 4.2x | 4.6%, 3.3x |
| Ordinary firms, all, 12 months | 2.1% (1.2 to 3.0) | 6.8% |
| Ordinary firms, top 10%, 12 months | 7.3% (3.0 to 12.4), 3.5x (1.8 to 5.7) | 21.6%, 3.2x |
| Ordinary, not property, top 10%, 12 months | 3.6% (0.6 to 8.4), 2.2x (0.5 to 5.1) | 10.7%, 2.3x |
| Property, top 10%, 12 months | 20.0% (8.4 to 32.7), 3.9x | 40.9%, 3.4x |

Leads per switch, ordinary firms top 10%: about 14 at 12 months and 61 at 90 days (radar data: 5 and 22).
Month by month, 12 months, ordinary firms: lift between 2.7x and 4.8x, never under 1.5x (10 months).
Ordinary non-property firms: under 1.5x in 4 of 10 months at 12 months.

**What it shows.**
1. **Absolute rates are about 3 times lower** than the radar's own data suggests. This is the correction
   the audit expected: firms that went quiet are in the sample, and they rarely switch lender.
2. **The ranking still works** for ordinary firms and property firms: about 3 to 4 times the group
   average, and the ranges stay above 1 for them.
3. **For ordinary firms outside property the ranking is not shown to work**: the 12-month range is
   0.5x to 5.1x and the 90-day range is similar.
4. **The ranges are wide** because the sample is small (about 800 firms scored per window). The tables
   will be rerun at 1,500 companies; do not quote the top 1% rows at all.

Quote the **random-sample rates**, not the radar-data rates, for anything a lender will see.
