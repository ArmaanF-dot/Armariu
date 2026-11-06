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

**Results (1,501 companies with a charge register, 5 Nov 2026).** The same live rw3 score, tested on a
random sample. Switched lender, 95% range in brackets, lift over the group's own average. "Radar data" is the
table in docs/refi_odds_table.md.

| | Random sample | Radar data |
|---|---|---|
| Ordinary firms, all, 90 days | 0.4% (0.2 to 0.5) | 1.4% |
| Ordinary firms, top 10%, 90 days | 1.7% (0.8 to 2.8), 4.7x (2.7 to 6.6) | 4.6%, 3.3x |
| Ordinary firms, all, 12 months | 1.9% (1.2 to 2.6) | 6.8% |
| Ordinary firms, top 10%, 12 months | 7.1% (3.9 to 11.4), 3.8x (2.3 to 5.6) | 21.6%, 3.2x |
| Property, top 10%, 12 months | 23.3% (12.4 to 35.6), 4.9x (3.1 to 7.2) | 40.9%, 3.4x |
| Ordinary, not property, top 10%, 90 days | 1.3% (0.4 to 2.3), 4.6x (1.7 to 7.5) | 2.2%, 2.6x |
| Ordinary, not property, top 10%, 12 months | 4.7% (1.5 to 8.9), 3.0x (1.2 to 5.1) | 10.7%, 2.3x |

Scored: 21,474 company-dates (1,198 companies) at 90 days and 11,130 (1,159) at 12 months.
Leads per switch, top 10%: ordinary firms about 14 at 12 months and 60 at 90 days; ordinary non-property
about 21 and 78 (radar data: 5 and 22; 9 and 45).
Month by month at 12 months the lift never fell below 2.5x for ordinary firms or for ordinary
non-property firms (10 months each). At 90 days the single months are too thin to read: ordinary firms had
no switch in the top 10% in 3 of 19 months and ordinary non-property firms in 6 of 19.

**What it shows.**
1. **Absolute rates are about 3 times lower** than the radar's own data suggests (ordinary firms at
   12 months: 1.9% against 6.8%). Firms that went quiet are in this sample and rarely switch lender.
2. **The ranking holds on the random sample**, at about 3 to 5 times the group average for ordinary
   and property firms, with ranges above 1 at both windows.
3. **Ordinary firms outside property now show a lift too** (3.0x at 12 months, range 1.2 to 5.1; 4.6x at
   90 days, range 1.7 to 7.5). The first 1,012 companies had put this in doubt; the ranges are still wide
   and the lower ends are close to 1, so describe it as likely, not proven.
4. **The top 1% and top 5% rows rest on a few dozen firms**: do not quote them.

Quote the **random-sample rates**, not the radar-data rates, for anything a lender will see.

**Limits.** England and Wales only, and only companies still on the register. Reruns will move the figures
as the sample grows; the sampler resumes with `python -m radar.population sample --target N`.

**Where the portal gets its figures.** The client portal and terminal quote only the random-sample rows
above, typed into `radar/refi_sample.py` (change both together after a rerun). The export drops the model's
absolute odds, the radar-data backtest tiers and the 90-day radar-data table. Each listed company shows its rank
band inside its own group (property, ordinary non-property, serial borrowers); only the top 10% band carries a
measured rate, serial borrowers none.
