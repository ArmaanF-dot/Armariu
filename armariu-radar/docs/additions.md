# Armariu additions: what was built and how to use it

Built on branch `additions-modules-and-portal` against the plan "Armariu additions: execution plan"
(3 Nov 2026). Rules only, no paid AI, no API calls to any AI service. Nothing was scheduled and
nothing was published: the plan's gate still applies (see the end of this file).

## Commands

```
python -m radar.aliases review            # Module A: unmapped lender names, most frequent first
python -m radar.aliases coverage          # Module A: share of charges that map to one lender
python -m radar.charge_signals --start 2026-10-26 --end 2026-11-01 --sample 30   # Module B (+ 30 to hand-check)
python -m radar.lender_view --week-start 2026-10-26                              # Module C
python -m radar.scorecard backtest [--window 180]                                # Module D (BACKTEST)
python -m radar.weekly_pack --week-start 2026-10-26                              # Module E
python -m radar.contracts leads awards.csv                                       # Module F (manual sample)
python -m radar.snapshot                  # data for the review app pages and the client portal
python -m radar.portal                    # the client portal, read-only, http://127.0.0.1:8790
```

The review app (`python -m radar.ui`) has three new pages under **Additions**: Lender view,
Scorecard and Weekly pack, plus a live banner on the overview.

## Module by module

**A. Lender names.** `radar/aliases.py`. Order: hand alias table, Week 1 group table, security
agents kept as their own class (role phrases such as "as security trustee for the secured parties"
are dropped from the name, not from the class), parent firm, then fuzzy matching with a 0.94
threshold and the same first four letters. Anything else stays its own entity and goes to the
review file; nothing below the threshold is merged. Individuals are masked as "A private
individual" everywhere downstream.
Measured on 3 Nov 2026: 76.5% of the last 12 months' register charges map (7,250 of 9,481), before
any review round. The plan's target is 90% after two rounds; the top 20 unmapped names are in
`output/review/unmapped_lenders_<date>.csv` (mostly buy-to-let lenders such as Godiva, Fleet,
The Mortgage Lender).

**B. Signals.** `radar/charge_signals.py`, rule version `v1`. First facility (no charge in the prior
3 years, only when the borrower's full history was fetched), refinancing (another lender's charge
satisfied within 90 days either side; dated when both filings are public), add-on (same lender,
earlier charge still outstanding). Charges created the same day for the same lenders are one deal,
so a property portfolio does not produce a run of add-ons. Explanations state filing facts only.
Week of 26 Oct 2026: 3,293 signals (1,606 first facility, 580 refinancing, 1,107 add-on).
Acceptance still needs Armaan: hand-check the 30 in `output/signals/handcheck_<date>.csv`
(target 80% sensible).

**C. Lender view.** `radar/lender_view.py`. League table with trailing 4 and 13 weeks, sector cut,
new entrants, top 10 refinancing flows, syndication proxy. Every table prints its window and the
"filings, not deals" caveat; lenders with under 5 charges in 13 weeks are flagged; totals reconcile
(4,478 raw charges in the week of 26 Oct, all with a lender). Weeks before 20 Oct cover only
borrowers that later filed a charge, and the output says so.

**D. Scorecard (backtest).** `radar/scorecard.py`. Outcome proposed: a further charge filed within
90 days. Baseline: charges without the signal in the same sector and company-age band, weighted to
match. 95% Wilson intervals. Only signals whose window closed before 20 Oct 2026 count, so there is
no look-ahead. Same database, same numbers (tested).

| Signal | n | Hit rate | Baseline | Lift |
|---|---:|---:|---:|---:|
| First facility | 2,053 | 26.1% | 68.2% | 0.38 |
| Refinancing | 3,726 | 81.8% | 69.0% | 1.19 |
| Add-on | 9,822 | 80.6% | 67.4% | 1.20 |

Read with the bias note: history exists only for borrowers active on the stream from 20 Oct 2026.

**E. Weekly pack.** `radar/weekly_pack.py` writes `weekly_pack.md` in the issue template (the read is
left for Armaan), plus `commentary_prompt.md` to paste into a Claude chat by hand. A failure exits
non-zero, writes `WEEKLY_PACK_FAILED.txt` and, if `NTFY_TOPIC` is set, sends an ntfy notice.

**F. Contract wins.** `radar/contracts.py` works on a CSV exported by hand from Contracts Finder or
Find a Tender (the endpoints and fields were not verified from here, as the plan asks). Matches by
company number first; name matches always need `confirmed=yes`. Large primes (`data/large_primes.csv`)
and companies over 20 years old are dropped. Every lead links to its notice.

## Client portal

`radar/portal/` is a separate, read-only app for customers: league table, signals with filing links,
lender profiles, the backtest scorecard and the method, with CSV downloads. It has no accounts,
payments, review queues or write endpoints and only shows `public` data (no alias review, no
individuals). It listens on 127.0.0.1 only. `python -m radar.portal export DIR` writes a static copy
for hosting later.

**This sits against the plan's gate**, which says no dashboards or public web app before the
validation passes. It was built because Armaan asked for it; it is not hosted anywhere. Hosting it
(GitHub Pages or elsewhere) is his decision after the gate, ideally with the legal check on the
not-advice wording.

Imagery: both apps draw an animated City-at-dusk skyline. A real photo can be dropped in as
`static/img/hero.jpg` in either app and fades in over it (see the README in each `img` folder).

## Decisions for Armaan (the plan's open questions, with the defaults used)

1. Hit definition: further charge within 90 days (used) or 180 days, or insolvency within 12 months.
2. Property lenders: hidden by default in the portal and the pack, shown in the owner view.
3. Featured companies: the pack names companies (company-level facts only); switch to aggregates if preferred.
4. Where the scorecard is published: portal, newsletter, or both.
5. Legal check on the not-advice wording before charging.

## After the gate (not built on purpose)

Scheduled lender view and pack jobs, the live `signals_published.csv` log, the club, all-assets and
cluster rules, and any automation of Module F.
