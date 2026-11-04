# Refinance watch: do better modelling methods help? Backtest of 4 Nov 2026

**Result: no method ranks ordinary companies better than the live points score (rw1). One change
is a clear win and is in this PR: the odds shown are now worked out separately for ordinary companies
and serial borrowers, which makes them much more honest.** The list order is unchanged.

## How it was tested
- Same data as docs/refi_datasets.md: 111,712 company-dates, charge features use only charges filed
  before each date.
- Models learn on dates whose 12-month outcome had closed before 1 Apr 2025 (Apr 2022 to Apr 2024),
  then score 1 Apr, Jun, Aug and Oct 2025 (21,044 company-dates; 17,234 for ordinary companies,
  meaning 10 or fewer charges on record). Survival models could also use Apr 2024 to Mar 2025, with
  outcomes cut off at 1 Apr 2025.
- Tuning was done on a time split inside the learning period, never on the test dates.
- 95% intervals resample companies, not rows, since a company appears on up to four test dates.
  Top 1% of ordinary companies is about 170 company-dates, so its intervals are around ±10 points.

## Ordinary companies (the useful leads): rw1 top 1% 30.8%, top 10% 22.5%, base 7.6%
| Method | Top 1% | Top 10% | Avg precision | Top 10% vs rw1 (95%) |
|---|---:|---:|---:|---|
| rw1 (live) | 30.8% | 22.5% | 0.172 | |
| Logistic regression on rw1's clues (+ pairs, + numeric bands) | 28 to 34% | 19.4 to 20.8% | 0.162 to 0.173 | all at or below zero |
| Gradient boosting (LightGBM, shallow, monotone, ordinary-only versions) | 20 to 32% | 18.0 to 18.5% | 0.138 to 0.162 | -7.0 to -1.1 (worse) |
| Survival: discrete-time hazard, Cox, boosted survival | 31 to 38% | 20.0 to 21.0% | 0.173 to 0.179 | all include zero or worse |
| Survival: random survival forest, with the extra recent year | 34.9% | 22.3% | 0.180 | -1.5 to +2.0 |
| rw1 + lender's own track record (shrunk, out-of-fold) | 36.6% | 22.5% | 0.185 | -1.0 to +1.7 |
| Rank blend, half rw1 and half boosted trees | 33.1% | 22.9% | 0.185 | -1.2 to +2.3 |

- Nothing beats rw1 at top 10% with an interval clear of zero. Several look better at top 1%, but
  every one of those intervals spans zero.
- The lender track record is the only idea with a measurable gain: average precision +0.013 (95%
  +0.006 to +0.022), with the same gain inside the learning period. Its top 10% is flat, so it is
  **not** in this PR. Worth retesting once more history builds up.
- **Why flexible models lose:** patterns moved between 2022-24 and 2025. For example, "loan over 5
  years old with a security trustee" was 5.3x the base rate before and 0.1x in 2025. Trees learn
  these cohorts; rw1's crude, smoothed points hold up better.
- Survival models gain about 0.5 to 1.5 points from the extra recent year, but still don't pass rw1.

## All companies (serial borrowers included)
Boosted trees and survival models raise the top 10% from 37.3% to 40 to 41% (intervals above zero),
and the top 1% to 80 to 89%. Nearly all of that is serial property borrowers who refinance all the
time, which are weak leads, so this is not adopted. A separate model for serial borrowers only
reaches 73% top 10% within that group (rw1 53%), if that list is ever wanted.

## The win: separate odds for ordinary and serial borrowers (in this PR)
At the same score, serial borrowers (11+ charges filed) refinance far more often than ordinary
companies. One shared odds table therefore overstated ordinary companies and understated serial ones.

| Odds shown | Calibration error, ordinary | Serial | Ordinary top 10%: shown / actual | Serial top 20%: shown / actual |
|---|---:|---:|---|---|
| One table (before) | 2.3 pts | 6.9 pts | 28.2% / 21.0% | 36.2% / 49.2% |
| Two tables (now) | 1.5 pts | 3.2 pts | 17.9% / 20.7% | 46.1% / 48.7% |

The score and the order of the list are unchanged; only the odds column (and lift) change. Each row
now carries `serial_borrower`. Odds tables need 400+ learning rows per group, else the shared table is used.
