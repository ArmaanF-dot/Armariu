# UK borrower risk score (9 Nov 2026)

Moved into the package from `scripts/distress_backtest.py` (PR 22) so it can score firms as well as test the idea.
Plan tasks 7 and 8. Insolvency is not loan default, and there is no bureau benchmark: a screening aid.

## Commands

    python -m radar.distress backtest    # strict time split; writes output/distress/report.json
    python -m radar.distress score       # learns on every closed cutoff, scores today's firms -> output/distress/risk_list.csv
    python -m radar.distress_fetch       # Actions only: filing histories (radar firms, dissolved sample, random sample)

The "distress" workflow (`.github/workflows/distress.yml`, run by hand) fetches, saves progress to the
`distress-data` branch every 30 minutes (so a rerun resumes), scores, and uploads `risk_list.csv`.
First run starts from the earlier test data on the PR 22 branch. It shares the Companies House rate limit with the
radar and pauses while the radar job runs.

## Check that the move changed nothing (9 Nov 2026)

Same data, rerun: AUC 0.700 (95% range 0.668 to 0.735), top 10% 4.0% against 1.4% average, top 20% holds 47% of
insolvencies. As in research/distress-score-backtest-2026-11-07.md.

## What the risk list shows

Each firm gets a risk decile and **the insolvency rate that decile actually had** (4.0% for the riskiest tenth, 0.4 to 0.7%
for the safest). The raw model number is kept as `score` but is not a probability: it reaches 60% on a few firms with thin
clues, so it is never shown as odds. `reasons` lists up to three plain clues (accounts overdue, compulsory strike-off
notice, new invoice or asset finance charge, new shares issued, late filings).

First run on the random sample only (the radar firms' filings are not fetched yet): 12,697 firms scored.

## Limits

- Population is firms with an outstanding charge. Event is formal insolvency within 12 months.
- Filing histories for the radar's own firms (the ones with names and lenders) come from the first workflow run.
- The dissolved sample is 466 firms from 42 days; the workflow adds more.
