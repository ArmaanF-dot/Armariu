# Refinance watch accuracy at 90 days, by group (BACKTEST, 4 Nov 2026)

> Superseded by [refi_odds_table.md](refi_odds_table.md) (live score rw3, 90 days and 12 months, top 1/5/10/25% and rest).

**In short:** for ordinary companies (10 loans or fewer) the top 10% of the list switched to a new
lender within 90 days 4.5% of the time, about **3x** their 1.4% average (95% range 2.7x to 3.5x).
For ordinary companies **outside property**, the audience a private credit lender cares about, it is
much weaker: top 10% 2.1% against 0.9% (2.4x, range 1.8x to 3.2x), and the top 1% is only 11 hits in
401. The big numbers in the mixed rows ("Not property" top 1% 19.8%, "Everyone" top 1% 17.0%) come
from serial borrowers who refinance all the time. **This is a lead list, not a forecast.**

Rerun with `python -m radar.refi_accuracy`. The customer site shows the same table on the
Refinance watch page, rebuilt with each snapshot.

## What counts as "refinanced"
Within 90 days of the date:

| Name | Rule | Used for |
|---|---|---|
| **Switched lender** (leads the table) | a loan that was outstanding is marked repaid **and** a new charge is filed for a lender that held none of the repaid loans | the only kind that is a lead for a competing lender |
| Any refinance | a loan is marked repaid and any new charge is filed, same lender included | what the live score is built on |
| Repaid | a loan is marked repaid, replaced or not | context |

A lender is the named lender on the charge (a security trustee only when no lender is named).
Charges with no lender named, and private individuals (all grouped together), never count as a
switch, so the switch rate is if anything a little low.

## How it was tested
- **Learned** the live rw1 points and odds on 10 monthly dates in 2024 (Jan to Oct), so every 90-day
  outcome had closed before 2025.
- **Tested** on the first of every month from Jan 2025 to Jul 2026 (19 dates; 104,889 company-dates,
  6,294 companies). Windows stop before 20 Oct 2026, when the radar began watching: every company in
  the data had a charge filing after that day, so a window running past it would partly see the answer.
- On each date only charges **already filed** at Companies House count (no look-ahead).
- Top 10%, 5% and 1% are ranked **within each group**, pooled across the test dates.
- 95% ranges resample **companies**, not rows (a company can appear on all 19 dates), 1,000 times.
- Ordinary = 10 or fewer charges filed before the date; serial = 11 or more. Property = the company's
  main SIC code is in property and real estate.

## Switched lender within 90 days
Each cell: hit rate, 95% range, hits out of company-dates (lift over that group's own average).

| Group | Everyone in group | Top 10% | Top 5% | Top 1% |
|---|---|---|---|---|
| **Ordinary companies (10 loans or fewer)** | 1.4% (1.3–1.6%), 1,231 of 85,686 | **4.5%** (3.8–5.1%), 382 of 8,568 (3.1x) | 5.9% (4.8–7.1%), 254 of 4,284 (4.1x) | 7.2% (4.5–10.4%), 62 of 856 (5.0x) |
| **Ordinary, not property** | 0.9% (0.7–1.0%), 351 of 40,108 | **2.1%** (1.5–2.8%), 85 of 4,010 (2.4x) | 2.0% (1.2–2.9%), 41 of 2,005 (2.3x) | 2.7% (1.1–5.1%), 11 of 401 (3.1x) |
| Not property (serial included) | 1.5% (1.2–1.8%), 708 of 46,999 | 7.5% (5.5–9.8%), 354 of 4,699 (5.0x) | 11.3% (7.9–14.9%), 266 of 2,349 (7.5x) | 19.8% (12.2–28.4%), 93 of 469 (13.2x) |
| Property | 3.6% (3.3–4.0%), 2,097 of 57,890 | 13.3% (11.4–15.2%), 769 of 5,789 (3.7x) | 14.6% (12.3–17.1%), 423 of 2,894 (4.0x) | 19.0% (12.4–25.3%), 110 of 578 (5.3x) |
| Serial borrowers (11+ loans) | 8.2% (7.1–9.2%), 1,574 of 19,203 | 19.7% (16.4–23.1%), 379 of 1,920 (2.4x) | 21.6% (17.0–26.1%), 207 of 960 (2.6x) | 29.7% (19.5–39.2%), 57 of 192 (3.6x) |
| Everyone | 2.7% (2.4–2.9%), 2,805 of 104,889 | 13.0% (11.6–14.4%), 1,361 of 10,488 (4.9x) | 14.3% (12.3–16.4%), 749 of 5,244 (5.3x) | 17.0% (12.7–21.6%), 178 of 1,048 (6.3x) |

Any refinance (same lender allowed) runs only a little higher: ordinary top 10% 4.9%, ordinary
non-property top 10% 2.4%, everyone top 1% 19.7%. At 90 days most refinances are switches.
Full output, with the any-refinance and repaid columns: `output/refi_watch/accuracy_90d.md`.

## What to take from it
1. **Lead with the ordinary row.** 3x on ordinary companies is a real, steady lift but a small
   absolute rate: of 20 names from the ordinary top 10%, expect about one to switch lender within
   90 days. That is the number to test with brokers and lenders ("how many of 20 would need to be
   live to be worth £X a month?").
2. **Non-property ordinary companies are the weak spot.** The score is barely better than picking
   from the group, and the top 1% is too small to say more. The "Not property" top rows look strong
   only because serial non-property borrowers sit there.
3. **Show the evidence.** Each name on the list now links to the filing of its newest open loan,
   next to the charge register link, so a reader can check why it is there.
4. **Absolute rates run high.** Every company in the data had a charge filing from 20 Oct 2026 on (a new loan, or for 30% only a repayment), so it
   is a set of active borrowers, not the whole market. Compare rows with each other, not with the
   market. Nothing here has been tested with brokers or lenders yet.

Related: docs/refi_methods.md (other models tried; none ranks ordinary companies better).
