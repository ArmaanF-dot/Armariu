# Refinance watch: LightGBM vs the live score (rw3). Backtest of 5 Nov 2026

**Result: LightGBM doesn't beat rw3 for ordinary companies at 12 months, the headline. It ranks serial and
property borrowers clearly better. The live list is unchanged.** This is an experiment (`radar/refi_gbm.py`),
not live code.

The modelling approach follows the user's RoadSense pothole model (ArmaanF-dot/Pothole-simulator-,
`roadsense/models/real_risk.py`): a LightGBM classifier, monotone constraints on clues that should only push a
company up, one pooled model with the segment as an input and weights that balance the segments, a paired
cluster bootstrap against the baseline that reports how often the model comes out ahead, and SHAP reasons for each
row (`reasons()`). LightGBM is a discriminative model: it learns the odds of a switch from the clues. It does not
generate data.

## How it was tested
- Same protocol as `radar.refi_accuracy` (docs/refi_odds_table.md). The 90-day models learn on 2024 dates and
  are tested on 19 monthly dates from Jan 2025 to Jul 2026 (116,151 company-dates). The 12-month models learn on
  2023 dates and are tested on 10 monthly dates in 2025 (57,327 company-dates). Only charges filed before each
  date count. The target is a switched lender.
- "Long history" variants also learn on monthly dates from 2019, with every outcome still closed before 2025.
- Early stopping uses the latest 20% of the learning dates, never the test dates.
- Gap ranges: 95%, paired, 1,000 resamples of companies.
- **Population: the radar's own data.** The random-sample backfill (`data/backfill.db`) had fetched 0 companies
  on 5 Nov, and the earlier 1,012-company sample was session-local and is gone. Absolute rates here run about
  3x high (docs/refi_population.md). Compare rows with each other; don't quote the rates.

## Best variant (RoadSense-style, long history) vs rw3, switched lender, top 10% unless stated
| Group | 90 days: rw3 | LightGBM | Gap (95%) | 12 months: rw3 | LightGBM | Gap (95%) |
|---|---:|---:|---|---:|---:|---|
| Ordinary (10 loans or fewer) | 4.6% | 5.4% | +0.8 (+0.3 to +1.3) | 21.8% | 20.0% | **-1.8 (-3.7 to -0.0)** |
| Ordinary, not property | 2.2% | 2.0% | -0.2 (-0.8 to +0.3) | 10.7% | 11.4% | +0.7 (-1.6 to +3.6) |
| Property | 15.7% | 17.5% | +1.8 (+0.7 to +2.8) | 40.6% | 41.6% | +1.0 (-1.9 to +4.4) |
| Serial (11+ loans) | 29.6% | 36.3% | +6.7 (+3.5 to +10.2) | 58.6% | 67.5% | +8.9 (+3.2 to +14.5) |
| Serial, top 5% | 30.5% | 49.9% | +19.4 (+13.0 to +26.0) | 66.6% | 78.4% | +11.8 (+3.0 to +19.8) |
| Everyone, top 1% | 26.3% | 48.9% | +22.7 (+16.3 to +28.9) | 62.8% | 78.5% | +15.7 (+7.8 to +23.6) |

Average precision for ordinary companies: 0.041 vs 0.049 at 90 days, 0.173 vs 0.170 at 12 months.
Full tables: `output/refi_watch/gbm/live/gbm.md` after `python -m radar.refi_gbm`.

## What it shows
1. **Ordinary companies:** a small gain at 90 days and a small loss at 12 months, the headline. This is the same
   result as the 4 Nov methods test (docs/refi_methods.md). Patterns drift between learning years and 2025, and
   rw3's smoothed points hold up better.
2. **Serial and property borrowers:** LightGBM is clearly better at both windows, mostly because of the extra
   numeric charge clues (lenders on open loans, company age, loan ages) and the longer history.
3. On rw3's own seven clues alone, LightGBM is slightly *worse* than rw3. The gain comes from the richer
   clues, not from the method.
4. Largest SHAP/gain inputs at 12 months: age and lender type of the newest loan, number of lenders on open
   loans, company age, time since the last repayment, lender size, number of charges, oldest loan age, and the
   lender's repayment rate.

## Next
- Rerun on the random sample once the backfill has data: `python -m radar.dbsync pull --backfill`, then
  `python -m radar.refi_gbm --population sample`.
- If a serial-borrower or property list is ever wanted, LightGBM is the better ranker for it.

## Follow-up the same night: walk-forward test and a locked window (`python -m radar.refi_gbm_wf`)
Each year from 2021 is a fold. The models learn on every monthly date from 2018 whose outcome had closed, and rw3
learns as it does live, on the latest year. The best variant was picked on the walk-forward years only (ordinary
companies, top 10%), then scored **once** on a locked window: Jan to Jul 2026 for 90 days, Jul to Oct 2025 for 12
months. The locked window has now been used and should not be used to choose models again.

Variants: logistic regression, LightGBM (RoadSense-style), LightGBM with recent years weighted (half-life 1 year),
LightGBM with a ranking objective (rank_xendcg), CatBoost with the lender's identity as a category, and rank blends
with rw3.

**Walk-forward years, ordinary companies, top 10%, switched lender**
| | rw3 | Best variant | Gap (95%) |
|---|---:|---:|---|
| 90 days, 2021 to 2025 | 3.3% | 3.8% (LightGBM, recent years weighted) | +0.5 (+0.3 to +0.8) |
| 12 months, 2021 to Jun 2025 | 16.4% | 18.0% (blend of rw3 and LightGBM) | +1.6 (+1.1 to +2.4) |

Most variants beat rw3 in 2021 to 2024, and the gap shrinks in 2025. CatBoost with the lender's identity
did not help (12 months: 15.8%).

**Locked window, best variant vs rw3**
| Group | 90 days: rw3 | LightGBM (recent years) | Gap (95%) | 12 months: rw3 | Blend | Gap (95%) |
|---|---:|---:|---|---:|---:|---|
| Ordinary, top 10% | 5.3% | 5.4% | +0.1 (-0.6 to +0.7) | 21.6% | 21.8% | +0.2 (-1.5 to +1.6) |
| Ordinary, top 1% | 5.3% | 12.0% | +6.7 (+3.3 to +10.7) | 30.6% | 38.3% | +7.7 (-0.7 to +15.7) |
| Ordinary, not property, top 10% | 1.3% | 1.8% | +0.5 (-0.1 to +1.3) | 12.8% | 12.0% | -0.8 (-2.9 to +1.4) |
| Property, top 10% | 16.1% | 18.3% | +2.3 (+1.1 to +3.6) | 42.0% | 45.1% | +3.1 (+0.7 to +5.5) |
| Serial, top 10% | 30.7% | 37.3% | +6.6 (+2.6 to +10.6) | 60.6% | 67.2% | +6.6 (+2.2 to +11.2) |

**Verdict:** for ordinary companies' top 10%, the headline, LightGBM is level with rw3 on unseen months at both
windows, so rw3 stays. LightGBM is clearly better for serial and property borrowers, and at 90 days for the very
top 1% of ordinary companies. All of this is still radar data (rates about 3x high). Retest on the random sample
when the backfill has data.
