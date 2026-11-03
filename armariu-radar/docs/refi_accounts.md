# Refinance watch: accounts and owner clues (rw2), backtest of 3 Nov 2026

**Result: not promoted.** The live score stays rw1. The new clues did not beat it on months the
score never saw, so the data downloads and the comparison are kept so it can be retried later.

## What was added
- `radar/accounts.py` reads Companies House's free bulk accounts (monthly zips Jan 2023 to Sep 2026,
  then the daily zips) by HTTP range requests and keeps only our companies. That came to 18,500
  filings for 6,058 of 8,988 companies. A filing counts from the end of the month it was published.
- `radar/ownership.py` streams the free owner register (PSC snapshot), which holds 16,500 owner records.
  A change counts from 30 days after its date. The 6 Apr 2016 register start and founding owners are ignored.
- Clues in `refi_watch.filed_features`: bank loans due within a year, share of the balance sheet due
  within a year, how debts moved against the prior year's comparatives, owner changes, and board changes
  (officer-set fingerprint, no names stored).

## Backtest (same splits as rw1)
| Window | Score | Base rate | Top 1% | Top 5% | Top 10% |
|---|---|---:|---:|---:|---:|
| 90 days | rw1 | 3.2% | 21.6% (6.7x) | 16.0% (5.0x) | 15.5% (4.8x) |
| 90 days | rw2 | 3.2% | 12.7% (4.0x) | 16.4% (5.1x) | 15.5% (4.8x) |
| 90 days | rw1 + bank loans in the latest accounts | 3.2% | 16.5% (5.1x) | 16.2% (5.1x) | 15.3% (4.8x) |
| 90 days | rw1 + share of the balance sheet due within a year | 3.2% | 22.0% (6.9x) | 16.6% (5.2x) | 15.2% (4.7x) |
| 90 days | rw1 + change since the year before | 3.2% | 14.4% (4.5x) | 15.9% (5.0x) | 15.5% (4.8x) |
| 90 days | rw1 + owners | 3.2% | 16.2% (5.0x) | 15.8% (4.9x) | 15.7% (4.9x) |
| 90 days | rw1 + directors | 3.2% | 21.3% (6.6x) | 15.9% (5.0x) | 15.6% (4.9x) |
| 365 days | rw1 | 10.8% | 52.9% (4.9x) | 41.0% (3.8x) | 37.3% (3.4x) |
| 365 days | rw2 | 10.8% | 45.7% (4.2x) | 38.9% (3.6x) | 36.8% (3.4x) |
| 365 days | rw1 + bank loans in the latest accounts | 10.8% | 33.8% (3.1x) | 40.7% (3.8x) | 37.6% (3.5x) |
| 365 days | rw1 + share of the balance sheet due within a year | 10.8% | 47.1% (4.4x) | 40.0% (3.7x) | 37.4% (3.4x) |
| 365 days | rw1 + change since the year before | 10.8% | 50.5% (4.7x) | 40.1% (3.7x) | 37.7% (3.5x) |
| 365 days | rw1 + owners | 10.8% | 48.6% (4.5x) | 39.2% (3.6x) | 37.2% (3.4x) |
| 365 days | rw1 + directors | 10.8% | 53.3% (4.9x) | 40.4% (3.7x) | 37.1% (3.4x) |


The 90-day rw1 baseline measures 6.7x today. The earlier 7.7x came before the resolver started
masking personal names as individuals.

## Why it didn't help
- Owner changes looked useful in the learning year (1.27x for a change 6 to 12 months ago), then
  reversed in the test year (0.80x).
- Most small companies file accounts with no split of their loans: 76% of filings have no
  "bank loans due within a year" line.
- The accounts clues that held up, such as debts due within a year rising by half (about 1.4x on
  their own), overlap with "recent borrowing" already in rw1. Inside rw1's top 5% they add little.
- An L2 logistic regression on the same clues gave the same picture, so the simple point model
  isn't what's holding it back.

Rerun: `python -m radar.accounts fetch && python -m radar.ownership fetch && python -m radar.refi_watch compare`.
