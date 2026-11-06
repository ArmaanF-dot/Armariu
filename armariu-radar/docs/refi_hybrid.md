# Refinance watch: LightGBM for serial and property lists (rw3+gbm1)

**What changed.** Serial borrowers (11+ charges filed) and property companies are now ranked by a LightGBM model.
Everyone else (ordinary, non-property) keeps rw3. Reason: the 5 Nov 2026 tests (docs/refi_gbm.md) found LightGBM
level with rw3 for ordinary companies at 12 months, but clearly better for these two groups, on walk-forward years
and on a locked window. Code: `radar/refi_hybrid.py`, called from `radar/snapshot.py`.

**How it works.** Learns on monthly dates from 2019 whose 12-month outcome had closed (nothing peeks ahead). Target:
switched lender. RoadSense-style (monotone constraints, segment input, balanced weights). A GBM row's
`odds_12m` is the past hit rate of its score band for "refinanced" (same meaning as rw3's odds), read out of sample: a copy of the model refitted on the earlier 80% of learning dates scores the latest 20%, and each score band's hit rate becomes the odds (ten bands; the final model ranks). `reasons` come from LightGBM's SHAP values. Rows carry `scorer` ("lightgbm" or "rw3") and
keep `rw3_score` and `rw3_odds_12m` for comparison. Sorting uses log(odds/base) for GBM rows so one list stays one
ordering.

**It needs** `pip install -r requirements-gbm.txt`. Without lightgbm, or with fewer than 20,000 serial/property
learning rows, or on any error, the list stays on rw3 and `refi_watch.hybrid.status` says why. The scheduled
pipeline does not build the snapshot, so CI and the pipeline are unchanged.

## Time-split backtest (`python -m radar.refi_hybrid backtest`)
Learn on dates to Dec 2023 (12 months) or Oct 2024 (90 days); score every month from Jan 2025 on, with only
charges filed before each date. Switched lender, top 10% within each group, paired 95% ranges from resampling
companies. Ordinary non-property rows are identical to rw3 by construction.

| Group (top 10%) | 12m rw3 | 12m LightGBM | Gap (95%) | 90d rw3 | 90d LightGBM | Gap (95%) |
|---|---:|---:|---|---:|---:|---|
| Serial (11+) | 59.1% | 66.4% | +7.3 (+1.6 to +12.7) | 31.0% | 35.2% | +4.3 (+1.3 to +7.1) |
| Property | 40.8% | 42.0% | +1.2 (-1.5 to +3.8) | 15.4% | 17.2% | +1.8 (+1.0 to +2.8) |
| Serial or property | 42.5% | 43.9% | +1.4 (-0.8 to +3.9) | 16.8% | 18.5% | +1.7 (+0.8 to +2.7) |

Top 5% and top 1% show larger gaps (serial 12m top 5%: 64.1% to 80.9%, +16.8, +9.2 to +24.8; segment 12m top 1%:
70.4% to 84.5%), but those rows rest on a few hundred company-dates from a few hundred companies. At the headline
12-month top 10%, the property gain is within noise; the gain is solid for serial borrowers. Full tables:
`output/refi_watch/hybrid/backtest.md` after the command. Earlier, separate evidence: docs/refi_gbm.md (walk-forward
and locked window).

## Radar rates run high: quote random-sample figures
All hit rates above are on the radar's own data (companies active at Companies House after 20 Oct 2026), which runs
about 3x high. On the random sample of 1,501 companies (PR #18, docs/refi_population.md), rw3's rates were:
ordinary 12-month 1.9% (radar 6.8%); ordinary top 10% 12-month 7.1% (3.9 to 11.4), 3.8x; property top 10%
12-month 23.3% (12.4 to 35.6), 4.9x, against 40.9% on radar data; ordinary top 10% 90-day 1.7%, 4.7x. Serial
borrowers are too few in the sample to quote. **Quote random-sample rates, not radar rates, to lenders.** Compare the
rows here with each other, not with the sample.

## Limits
- LightGBM has not been scored on the random sample (the backfill had no data on 5 Nov). Rerun when it does:
  `python -m radar.refi_gbm --population sample`.
- The shown odds are out-of-sample band hit rates from learning dates (a first version read them in sample and ran to
  100%). They are coarse; the backtest above is the accuracy evidence.
- Models are refitted on each snapshot build (about 6 minutes on a cold cache, because features are built for
  every learning date; later builds reuse `output/refi_watch/gbm/cache`).
- The 5 Nov locked window has been used; do not pick models on it again.
