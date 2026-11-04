# Refinance watch: new clues from data we already hold. Backtest of 4 Nov 2026

**Result: two clues from the charge register help, and they are live as rw3.** When the company last
repaid a loan (`since_sat`), and how often the lender's loans of that age get repaid (`lender_rate`), lift
the top 5% of the list by about 10 points in both unseen years. For ordinary companies (10 or fewer
charges) the gain is small: about 1 point in the top 10%, positive in both years but not proven.
Sixteen other history clues and the charges' own wording gave nothing measurable. Loan end dates were
not used (only about 0.8% of filings state one).

## How it was tested
- Every clue uses only charges **filed** before the date, and repayments dated on or before it.
  A repayment's date on the register is the day its filing was delivered (checked on 17,958 repayments:
  no gap), so repayment clues do not look ahead.
- Learned on dates whose 12-month outcome had closed before the first test date, then scored four
  unseen dates. Year A: learned Apr 2022 to Apr 2024, tested Apr, Jun, Aug, Oct 2025 (outcomes to Oct 2026).
  Year B: learned to Apr 2023, tested Apr to Oct 2024.
- Each clue was added to the rw1 points score on its own, then the best were re-checked on the
  second year. Intervals resample whole companies (all their dates together), 95%.

## The winners (average of both years, change against rw1)
| Clue | All companies, top 5% | All, top 10% | Ordinary, top 5% | Ordinary, top 10% |
|---|---|---|---|---|
| Last loan repaid + lender's repayment rate (rw3) | **+10.3 pts** (+6.9 to +13.0) | **+2.0** (+0.8 to +3.2) | +1.2 (-0.6 to +2.7) | +1.0 (-0.2 to +2.1) |
| Lender's repayment rate alone | +3.8 (+1.8 to +5.6) | +0.8 (-0.4 to +2.1) | +1.3 (-0.3 to +2.9) | +0.9 (-0.0 to +1.9) |
| Last loan repaid alone | +9.9 (+6.7 to +12.7) | +0.9 (-0.2 to +2.5) | -1.0 | -1.1 |

rw3 as it runs live (`python -m radar.refi_watch backtest`, 12 months, 21,044 company-dates, base 10.8%):

| | Top 1% | Top 5% | Top 10% | Top 25% |
|---|---:|---:|---:|---:|
| rw1 | 52.4% | 40.7% | 37.2% | 25.3% |
| rw3 | 69.1% | 51.7% | 40.9% | 26.3% |
| rw3, a year earlier (2024 dates) | 71.3% | 50.2% | 36.5% | 23.3% |
| rw1, a year earlier | 41.4% | 39.3% | 34.5% | 22.4% |

Much of the top-of-list gain is companies that borrow and repay often. The ordinary-company gain is
about a point, so read it as "no worse, probably slightly better" for them.

## What did not help (top 10%, ordinary companies, 2025 dates; rw1 22.3%)
| Clue | Top 10% | Note |
|---|---:|---|
| Time since last new charge | 20.8% | worse |
| Past refinances (repaid and re-borrowed before) | 21.2% | |
| Loan age against the company's own usual loan life | 21.2% | |
| Lender's 2-year repayment share (all ages) | 23.1% | in both years +0.4 to +0.8, not proven |
| Loan age against the lender's typical loan life | 21.9% | |
| Lender's recent repayments per open loan (run-off) | 22.6% | |
| Company age | 21.9% | |
| Sector (all sectors, and 2-digit SIC) | 21.9% / 22.5% | |
| Charges filed together (facility packages) | 22.2% | |
| Number of different lenders | 22.2% | |
| Loan age in quarters | 20.8% | |
| Age of oldest open loan | 21.3% | |
| Anniversary of the loan in the next quarter (seasonality) | 22.3% | no effect at all |
| Charge wording: all assets / property / shares / vehicles | -0.3 pts | 5,048 companies, both years |
| Number of property titles | 0.0 pts | |
| Negative pledge, floating charge, rent, lenders named | -0.7 to +0.1 pts | |

A jointly fitted version (logistic regression on all 20 clues) did no better than the points score.

## Charge wording
The live database keeps wording only for charges the stream has seen since 20 Oct 2026, so the full
register was fetched for 5,048 of the 8,992 companies (Companies House charges API; the download was
stopped there once every wording clue came out flat). Before 2013 most charges were described
("Legal charge", "Debenture"); since then nearly all say "A registered charge", and the particulars
(fixed or floating, all assets, title numbers) did not separate refinancers from the rest.
