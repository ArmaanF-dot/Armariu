# Refinance watch: which extra datasets help? Backtest of 3 Nov 2026

**Result: none of the free datasets reachable today improves the prediction for ordinary companies,
and the live points score (rw1) stays.** On ordinary companies (10 or fewer charges on record) rw1 also
beat a machine-learning model trained without look-ahead. Beauhurst data was not used: there is no
confirmed API access or licence for it.

## Which kinds of data could plausibly help (best first, for ordinary companies)
| Rank | Data | Why it might help | Free source | Status |
|---|---|---|---|---|
| 1 | Loan end dates (lender or broker data) | The one thing filings never show | None: partner deal | Not available |
| 2 | Property ownership (titles, price paid, date bought) | Bridging and development exits, purchases funded by new loans | HM Land Registry CCOD/OCOD, keyed by company number | Needs a free account and licence; **licence reportedly bars direct marketing**, so check before use |
| 3 | Financials: debt due within a year | Closest free proxy for a loan falling due | Companies House bulk accounts | Tested: no gain (most small firms file no loan split) |
| 4 | Insolvency, winding-up petitions, strike-off notices | Distress forces refinancing or ends it | The Gazette data feeds (OGL; company notices only) | Blocked here: needs `www.thegazette.co.uk` |
| 5 | Fundraising (share allotments, SH01 filings) | New equity often comes with new debt | Companies House filing-history API | Needs `api.company-information.service.gov.uk` (the scheduled pipeline has it; this session does not) |
| 6 | Ownership changes / acquisitions | A new owner usually refinances | Companies House PSC snapshot | Tested: no gain |
| 7 | Directors | Board changes around deals | Companies House officers (pipeline step) | Tested on partial coverage (1,477 of 8,992 firms): no gain yet |
| 8 | Size, location, name changes | Context | Companies House Basic Company Data | **New in this PR**, tested: no gain |
| 9 | Public contracts, grants | Growth signals, weak for property borrowers | Contracts Finder / Find a Tender (OGL), 360Giving GrantNav, UKRI GtR | Blocked here; low expected value |
| 10 | Trade (import/export) | Weak for property | HMRC uktradeinfo, names only, no history | Low expected value |
| – | CCJs, credit ratings, hiring | | **No free source** (Registry Trust is paid; job-ad APIs forbid lead-gen use) | Paid only |

## What was built
- `radar/profile.py` streams Basic Company Data (450 MB zip, not saved) and keeps our 8,992 companies:
  accounts size, company type, registered-office postcode district, how many companies share that
  postcode (formation-agent addresses), year-end month, and dated name changes. No people, no street address.
  Size, type and address are as of the snapshot, so they peek slightly; name-change dates do not.
- `radar/refi_ml.py` reruns a gradient-boosted tree model (scikit-learn, free) with one dataset
  group added at a time. Charge features use only charges filed before each date. Models learn only on
  dates whose 12-month outcome had closed before the first test date (Apr 2022 to Apr 2024), then score
  Apr, Jun, Aug and Oct 2025. Ordinary-company models learn on ordinary companies only.

## Backtest (12-month window, unseen dates)
## All companies: 21,044 company-dates, base rate 10.8%

| Model | Top 1% | Top 5% | Top 10% | Avg precision | Top 10% vs ML charges (95% interval) |
|---|---:|---:|---:|---:|---|
| rw1 as live (learning year overlaps test outcomes) | 52.9% | 40.8% | 37.3% | 0.298 | -2.4% to +1.0% |
| rw1, same learning dates as ML | 51.4% | 40.1% | 37.4% | 0.297 | -2.4% to +1.1% |
| ML: charges | 84.3% | 50.4% | 38.1% | 0.371 |  |
| ML: charges + profile_size | 83.3% | 51.0% | 37.4% | 0.370 | -1.3% to +0.3% |
| ML: charges + profile_place | 84.8% | 50.2% | 36.7% | 0.363 | -2.6% to -0.1% |
| ML: charges + profile_names | 81.4% | 50.5% | 37.8% | 0.369 | -1.1% to +0.8% |
| ML: charges + accounts | 81.9% | 50.5% | 37.7% | 0.360 | -1.5% to +0.8% |
| ML: charges + owners | 83.8% | 50.3% | 37.8% | 0.367 | -1.2% to +1.0% |
| ML: charges + directors | 83.8% | 54.0% | 38.2% | 0.375 | -0.7% to +0.9% |
| ML: all groups | 87.1% | 50.4% | 37.0% | 0.367 | -2.5% to +0.2% |

## Ordinary companies (<= 10 charges on record): 17,234 company-dates, base rate 7.6%

| Model | Top 1% | Top 5% | Top 10% | Avg precision | Top 10% vs ML charges (95% interval) |
|---|---:|---:|---:|---:|---|
| rw1 as live (learning year overlaps test outcomes) | 31.4% | 25.8% | 21.8% | 0.176 | +2.2% to +6.0% |
| rw1, same learning dates as ML | 30.2% | 25.3% | 22.7% | 0.176 | +2.8% to +6.7% |
| ML: charges | 19.2% | 21.0% | 17.7% | 0.145 |  |
| ML: charges + profile_size | 22.7% | 19.3% | 17.5% | 0.147 | -1.2% to +1.2% |
| ML: charges + profile_place | 20.3% | 19.7% | 17.9% | 0.143 | -1.1% to +1.8% |
| ML: charges + profile_names | 20.3% | 20.2% | 18.2% | 0.147 | -0.4% to +1.9% |
| ML: charges + accounts | 19.8% | 18.6% | 17.0% | 0.140 | -2.1% to +0.9% |
| ML: charges + owners | 25.6% | 19.5% | 17.8% | 0.147 | -1.0% to +1.3% |
| ML: charges + directors | 22.7% | 20.6% | 17.7% | 0.148 | -0.8% to +1.0% |
| ML: all groups | 21.5% | 16.3% | 16.3% | 0.137 | -3.0% to +0.1% |

## Reading the numbers
- **All companies:** ML's top 1% is mostly serial property borrowers, who refinance constantly (84% vs
  rw1's 53%). From the top 10% down, ML and rw1 are level.
- **Ordinary companies:** rw1 wins: top 10% 22.7% vs 17.7% for ML. The interval (+2.8 to +6.7 points)
  excludes zero. The earlier ML test (35% vs 31% at the top 1%) did not hold under the strict split.
- **Each new dataset:** every interval against the ML charges-only model includes zero, so no gain is
  measurable. The single best top-1% figure (owners, 25.6%) rests on about 170 company-dates and is noise.
- Accounts and owner data match the rw2 result in docs/refi_accounts.md: no gain.

## To test the remaining sources, allow these domains
`www.thegazette.co.uk` (insolvency and strike-off notices), `api.company-information.service.gov.uk`
(share allotments via filing history), and, once its licence is cleared, `use-land-property-data.service.gov.uk`
(Land Registry company-owned land, which needs a free account and API key).

Rerun: `pip install scikit-learn numpy && python -m radar.profile fetch && python -m radar.accounts fetch && python -m radar.ownership fetch && python -m radar.refi_ml`.
