# Refinance watch on the full random sample (BACKTEST, 9 Nov 2026)

Batch 1 of the model plan (project file research/model-and-sector-plan-2026-11-09.md). The random sample
(radar/backfill.py, branch `radar-backfill`) now holds 27,216 fetched companies, of which 21,203 had a
scored date. The last test used 1,501. Same rw3 score, same outcome (switched lender), same test rules as
docs/refi_odds_table.md: learned on earlier months, scored on months it never saw, ranges resample companies.

Reproduce: `python -m radar.dbsync pull && python -m radar.dbsync pull --backfill`, then
`python -m radar.refi_accuracy --population sample --window 365` (and `--window 90`),
`python -m radar.refi_gbm --population sample`, `python -m radar.refi_calibration --window 365`.

## 1. rw3 holds, with much tighter ranges

Test dates: 12 months, Jan to Nov 2025 (221,258 company-dates, 21,203 firms); 90 days, Jan 2025 to Aug 2026.

| Group | Window | Average | Top 10% | Lift (95% range) |
|---|---|---:|---:|---|
| Ordinary (10 loans or fewer) | 12 months | 2.5% | 7.7% | 3.08x (2.80 to 3.39) |
| Ordinary, not property | 12 months | 2.0% | 5.1% | 2.60x (2.21 to 3.03) |
| Property | 12 months | 3.8% | 13.0% | 3.44x (3.06 to 3.81) |
| Serial (11+ loans) | 12 months | 6.0% | 20.1% | 3.33x (2.73 to 4.03) |
| Ordinary | 90 days | 0.5% | 1.6% | 3.23x (2.91 to 3.57) |
| Ordinary, not property | 90 days | 0.4% | 1.0% | 2.76x (2.32 to 3.18) |

- The earlier ordinary figure (3.8x, range 2.3 to 5.6, 1,501 firms) holds as 3.1x with a range a quarter the width.
- **Ordinary, not property is now measured, not "likely"**: 2.6x at 12 months and 2.8x at 90 days, both ranges
  well above 1. Monthly lift stays between 2.5x and 2.9x at 12 months (11 months, none under 1.5x).
- Still true: 92% of the top 10% did not switch within 12 months. It is a sort order, not a forecast.

## 2. LightGBM beats rw3 on this sample, including ordinary firms (a reversal, not yet adopted)

Earlier runs (docs/refi_gbm.md) used the radar's own data and found LightGBM losing on ordinary firms at
12 months. On the random sample the best variant ("clues + numeric charge clues, long history") is ahead.
Top 10%, switched lender, gap against rw3 with its 95% range:

| Group | Window | rw3 | LightGBM | Gap |
|---|---|---:|---:|---|
| Ordinary | 12 months | 7.7% | 8.5% | +0.8 pts (+0.2 to +1.4) |
| Ordinary, not property | 12 months | 5.1% | 6.3% | +1.1 pts (+0.3 to +1.9) |
| Property | 12 months | 13.0% | 14.3% | +1.4 pts (+0.3 to +2.4) |
| Serial | 12 months | 20.1% | 21.2% | +1.2 pts (-2.6 to +4.8), not clear |
| Ordinary | 90 days | 1.6% | 2.0% | +0.5 pts (+0.3 to +0.6) |
| Ordinary, not property | 90 days | 1.0% | 1.3% | +0.4 pts (+0.2 to +0.5) |

**Not adopted.** Eight variants were compared on these same months, so the best one is flattered, and the
12-month months overlap a window earlier work had already used. By `docs/model_rules.md` it replaces rw3 only if it
wins on the locked window (months from Nov 2026). Meanwhile the half-and-half blend of rw3 and LightGBM is a
safe middle: it is ahead on ordinary firms at 90 days (+0.3, +0.2 to +0.4) and level at 12 months (+0.4, -0.1 to +0.9).
Next step: score the locked months as they close (90-day outcomes from Feb 2027).

## 3. Shown odds: the live list read about 1.7 times too high, now recalibrated

`python -m radar.refi_calibration` compares the odds shown beside a lead ("any refinance within the window") with what
happened, in ten equal score bins, on the sample's later months. Learning always ends before the test months.

| 12 months, top score bin | Shown | Actual |
|---|---:|---:|
| Ordinary, learned on radar data (how the list worked) | 14.3% | 8.0% |
| Ordinary, learned on the random sample | 8.3% | 8.9% |
| Serial, learned on radar data | 39.0% | 24.9% |
| Serial, learned on the random sample | 22.6% | 24.6% |

Mean gap across all bins: ordinary 1.7 pts (radar) against 0.5 (sample); serial 4.4 against 2.0. At 90 days the
same pattern holds (ordinary top bin shown 3.8% against 1.6% actual from radar data; 1.8% against 1.8% from the sample).
The old odds were inflated because radar data holds only firms active after 20 Oct 2026.

**Change made:** `refi_watch.recalibrate` re-reads the score-to-odds bands from the random sample (points and list
order unchanged). It runs in `python -m radar.refi_watch` when `data/backfill.db` is present; `--no-calibrate`
skips it. Live top odds on 9 Nov: ordinary top about 10% (was 60% in a tiny top band), serial top about 29% (was 68%).
A tiny top band (a short last chunk) is now folded into the band below.

## Limits

- Switched lender is not a deal: no amounts or terms. Survivorship: firms dissolved since are missing from the frame.
- England and Wales only. Test months overlap earlier work, so the LightGBM gain needs the locked window.
