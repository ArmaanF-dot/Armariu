# Refinance watch odds table: 90 days and 12 months (BACKTEST, 4 Nov 2026)

Live score **rw3**, scored on months it never saw. Rerun with `python -m radar.refi_accuracy --window 90` and `--window 365` (full output in `output/refi_watch/accuracy_90d.md` and `accuracy_365d.md`).

**Refinanced = switched to a new lender:** within the window a loan that was outstanding is marked repaid **and** a new charge is filed for a lender that held none of the repaid loans. "Any refinance" (repaid plus any new charge, same lender allowed) is shown after.

## What each level means

| Level | Plain English |
|---|---|
| Top 1% | The 1 in 100 firms in the group the score rates most likely to refinance. |
| Top 5% | The best-rated 1 in 20 firms (includes the top 1%). |
| Top 10% | The best-rated 1 in 10 firms (includes the top 5%). |
| Top 25% | The best-rated quarter of the group (includes the top 10%). |
| Rest | The other three quarters of the group, which the score rates least likely. |
| Baseline | How often a firm in that group refinanced anyway, with no score at all. |
| Lift | Hit rate divided by baseline: 3x means three times as likely as a random firm in the same group. |
| 95% range | Where the true hit rate very probably lies, given how many firms there are. Wide range = few firms, treat with care. |
| Firms / Firm-months | Distinct companies, and company-by-month checks (a firm is scored once a month, so it can count several times). Hit rate is per firm-month. |

## How it was tested

- **90 days:** learned on 10 monthly dates 2024-01-01 to 2024-10-01 (every outcome closed before 2025); tested on 19 monthly dates 2025-01-01 to 2026-07-01 (104,889 firm-months, 6,294 firms).
- **12 months:** learned on 12 monthly dates 2023-01-01 to 2023-12-01 (every outcome closed before 2025); tested on 10 monthly dates 2025-01-01 to 2025-10-01 (51,749 firm-months, 5,516 firms).
- Test windows end before 20 Oct 2026, when the radar began watching (every company in the data borrowed after that day).
- On each date only charges already filed at Companies House count. Levels are ranked **within each group**. Ranges resample firms, not rows, 1,000 times.
- 12-month learning uses 2023 rather than 2024, because a 12-month window starting in 2024 would end inside the 2025 test period.
- Ordinary = 10 or fewer charges filed before the date; serial = 11 or more. Property = main SIC code in property and real estate.

## 90 days: switched lender

| Group | Level | Firms | Firm-months | Hits | Hit rate | Baseline | Lift | 95% range |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Everyone | Top 1% | 198 | 1,048 | 293 | **28.0%** | 2.7% | 10.4x | 22.6% to 33.3% |
| Everyone | Top 5% | 753 | 5,244 | 1,070 | **20.4%** | 2.7% | 7.6x | 17.6% to 23.0% |
| Everyone | Top 10% | 1,334 | 10,488 | 1,483 | **14.1%** | 2.7% | 5.3x | 12.7% to 15.9% |
| Everyone | Top 25% | 2,868 | 26,222 | 2,049 | **7.8%** | 2.7% | 2.9x | 7.1% to 8.6% |
| Everyone | Rest | 5,593 | 78,667 | 756 | **1.0%** | 2.7% | 0.4x | 0.9% to 1.1% |
| Ordinary companies (10 loans or fewer) | Top 1% | 253 | 856 | 75 | **8.8%** | 1.4% | 6.1x | 6.2% to 11.6% |
| Ordinary companies (10 loans or fewer) | Top 5% | 850 | 4,284 | 271 | **6.3%** | 1.4% | 4.4x | 5.2% to 7.5% |
| Ordinary companies (10 loans or fewer) | Top 10% | 1,465 | 8,568 | 411 | **4.8%** | 1.4% | 3.3x | 4.1% to 5.6% |
| Ordinary companies (10 loans or fewer) | Top 25% | 2,675 | 21,421 | 727 | **3.4%** | 1.4% | 2.4x | 3.0% to 3.8% |
| Ordinary companies (10 loans or fewer) | Rest | 4,725 | 64,265 | 504 | **0.8%** | 1.4% | 0.6x | 0.7% to 0.9% |
| Serial borrowers (11+ loans) | Top 1% | 70 | 192 | 61 | **31.8%** | 8.2% | 3.9x | 22.3% to 42.1% |
| Serial borrowers (11+ loans) | Top 5% | 170 | 960 | 295 | **30.7%** | 8.2% | 3.8x | 25.0% to 37.0% |
| Serial borrowers (11+ loans) | Top 10% | 285 | 1,920 | 593 | **30.9%** | 8.2% | 3.8x | 26.2% to 35.8% |
| Serial borrowers (11+ loans) | Top 25% | 572 | 4,800 | 1,071 | **22.3%** | 8.2% | 2.7x | 19.3% to 25.4% |
| Serial borrowers (11+ loans) | Rest | 1,056 | 14,403 | 503 | **3.5%** | 8.2% | 0.4x | 3.0% to 4.0% |
| Property | Top 1% | 129 | 578 | 146 | **25.3%** | 3.6% | 7.0x | 19.1% to 31.4% |
| Property | Top 5% | 462 | 2,894 | 629 | **21.7%** | 3.6% | 6.0x | 18.6% to 24.9% |
| Property | Top 10% | 860 | 5,789 | 968 | **16.7%** | 3.6% | 4.6x | 14.5% to 19.2% |
| Property | Top 25% | 1,665 | 14,472 | 1,462 | **10.1%** | 3.6% | 2.8x | 9.0% to 11.3% |
| Property | Rest | 3,264 | 43,418 | 635 | **1.5%** | 3.6% | 0.4x | 1.3% to 1.6% |
| Not property | Top 1% | 73 | 469 | 163 | **34.8%** | 1.5% | 23.1x | 23.2% to 45.3% |
| Not property | Top 5% | 319 | 2,349 | 297 | **12.6%** | 1.5% | 8.4x | 8.8% to 16.7% |
| Not property | Top 10% | 630 | 4,699 | 364 | **7.8%** | 1.5% | 5.1x | 5.8% to 9.8% |
| Not property | Top 25% | 1,243 | 11,749 | 477 | **4.1%** | 1.5% | 2.7x | 3.2% to 5.1% |
| Not property | Rest | 2,407 | 35,250 | 231 | **0.7%** | 1.5% | 0.4x | 0.5% to 0.8% |
| Ordinary, not property | Top 1% | 114 | 401 | 17 | **4.2%** | 0.9% | 4.8x | 1.9% to 6.9% |
| Ordinary, not property | Top 5% | 389 | 2,005 | 60 | **3.0%** | 0.9% | 3.4x | 1.9% to 4.2% |
| Ordinary, not property | Top 10% | 645 | 4,010 | 91 | **2.3%** | 0.9% | 2.6x | 1.6% to 2.9% |
| Ordinary, not property | Top 25% | 1,129 | 10,027 | 164 | **1.6%** | 0.9% | 1.9x | 1.3% to 2.1% |
| Ordinary, not property | Rest | 2,074 | 30,081 | 187 | **0.6%** | 0.9% | 0.7x | 0.5% to 0.8% |

## 12 months: switched lender

| Group | Level | Firms | Firm-months | Hits | Hit rate | Baseline | Lift | 95% range |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Everyone | Top 1% | 168 | 517 | 336 | **65.0%** | 9.8% | 6.6x | 57.0% to 72.7% |
| Everyone | Top 5% | 525 | 2,587 | 1,207 | **46.7%** | 9.8% | 4.8x | 42.2% to 51.1% |
| Everyone | Top 10% | 961 | 5,174 | 1,919 | **37.1%** | 9.8% | 3.8x | 33.7% to 40.2% |
| Everyone | Top 25% | 2,160 | 12,937 | 3,023 | **23.4%** | 9.8% | 2.4x | 21.4% to 25.1% |
| Everyone | Rest | 4,722 | 38,812 | 2,061 | **5.3%** | 9.8% | 0.5x | 4.8% to 5.9% |
| Ordinary companies (10 loans or fewer) | Top 1% | 132 | 424 | 126 | **29.7%** | 7.0% | 4.2x | 21.8% to 38.3% |
| Ordinary companies (10 loans or fewer) | Top 5% | 563 | 2,123 | 549 | **25.9%** | 7.0% | 3.7x | 22.1% to 29.4% |
| Ordinary companies (10 loans or fewer) | Top 10% | 948 | 4,246 | 936 | **22.0%** | 7.0% | 3.1x | 19.5% to 24.7% |
| Ordinary companies (10 loans or fewer) | Top 25% | 1,889 | 10,617 | 1,531 | **14.4%** | 7.0% | 2.1x | 12.8% to 16.0% |
| Ordinary companies (10 loans or fewer) | Rest | 3,877 | 31,851 | 1,438 | **4.5%** | 7.0% | 0.7x | 4.0% to 5.1% |
| Serial borrowers (11+ loans) | Top 1% | 39 | 92 | 70 | **76.1%** | 22.8% | 3.3x | 62.4% to 88.1% |
| Serial borrowers (11+ loans) | Top 5% | 133 | 464 | 314 | **67.7%** | 22.8% | 3.0x | 58.5% to 76.0% |
| Serial borrowers (11+ loans) | Top 10% | 202 | 928 | 556 | **59.9%** | 22.8% | 2.6x | 52.7% to 66.8% |
| Serial borrowers (11+ loans) | Top 25% | 392 | 2,320 | 1,137 | **49.0%** | 22.8% | 2.1x | 44.2% to 53.9% |
| Serial borrowers (11+ loans) | Rest | 874 | 6,961 | 978 | **14.1%** | 22.8% | 0.6x | 11.9% to 16.2% |
| Property | Top 1% | 90 | 278 | 190 | **68.3%** | 12.5% | 5.5x | 58.2% to 77.8% |
| Property | Top 5% | 287 | 1,390 | 687 | **49.4%** | 12.5% | 4.0x | 43.4% to 55.3% |
| Property | Top 10% | 567 | 2,780 | 1,166 | **41.9%** | 12.5% | 3.4x | 37.4% to 46.0% |
| Property | Top 25% | 1,181 | 6,950 | 2,091 | **30.1%** | 12.5% | 2.4x | 27.6% to 32.6% |
| Property | Rest | 2,640 | 20,850 | 1,385 | **6.6%** | 12.5% | 0.5x | 5.9% to 7.5% |
| Not property | Top 1% | 53 | 239 | 132 | **55.2%** | 6.7% | 8.2x | 39.9% to 68.9% |
| Not property | Top 5% | 228 | 1,197 | 399 | **33.3%** | 6.7% | 5.0x | 27.0% to 40.1% |
| Not property | Top 10% | 453 | 2,394 | 563 | **23.5%** | 6.7% | 3.5x | 19.1% to 28.4% |
| Not property | Top 25% | 932 | 5,987 | 846 | **14.1%** | 6.7% | 2.1x | 11.9% to 16.4% |
| Not property | Rest | 2,100 | 17,962 | 762 | **4.2%** | 6.7% | 0.6x | 3.5% to 5.1% |
| Ordinary, not property | Top 1% | 62 | 204 | 35 | **17.2%** | 5.0% | 3.5x | 7.1% to 29.4% |
| Ordinary, not property | Top 5% | 266 | 1,023 | 142 | **13.9%** | 5.0% | 2.8x | 9.8% to 18.2% |
| Ordinary, not property | Top 10% | 439 | 2,046 | 226 | **11.1%** | 5.0% | 2.2x | 8.3% to 14.1% |
| Ordinary, not property | Top 25% | 819 | 5,115 | 413 | **8.1%** | 5.0% | 1.6x | 6.2% to 9.8% |
| Ordinary, not property | Rest | 1,783 | 15,348 | 599 | **3.9%** | 5.0% | 0.8x | 3.2% to 4.7% |

## 90 days: any refinance (same lender allowed)

| Group | Level | Firms | Firm-months | Hits | Hit rate | Baseline | Lift | 95% range |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Everyone | Top 1% | 198 | 1,048 | 394 | **37.6%** | 3.1% | 12.0x | 32.0% to 43.9% |
| Everyone | Top 5% | 753 | 5,244 | 1,321 | **25.2%** | 3.1% | 8.1x | 22.2% to 28.2% |
| Everyone | Top 10% | 1,334 | 10,488 | 1,809 | **17.2%** | 3.1% | 5.5x | 15.5% to 19.2% |
| Everyone | Top 25% | 2,868 | 26,222 | 2,451 | **9.3%** | 3.1% | 3.0x | 8.5% to 10.3% |
| Everyone | Rest | 5,593 | 78,667 | 828 | **1.1%** | 3.1% | 0.3x | 0.9% to 1.2% |
| Ordinary companies (10 loans or fewer) | Top 1% | 253 | 856 | 84 | **9.8%** | 1.6% | 6.3x | 7.0% to 12.9% |
| Ordinary companies (10 loans or fewer) | Top 5% | 850 | 4,284 | 292 | **6.8%** | 1.6% | 4.4x | 5.6% to 8.0% |
| Ordinary companies (10 loans or fewer) | Top 10% | 1,465 | 8,568 | 452 | **5.3%** | 1.6% | 3.4x | 4.6% to 6.1% |
| Ordinary companies (10 loans or fewer) | Top 25% | 2,675 | 21,421 | 791 | **3.7%** | 1.6% | 2.4x | 3.3% to 4.1% |
| Ordinary companies (10 loans or fewer) | Rest | 4,725 | 64,265 | 538 | **0.8%** | 1.6% | 0.5x | 0.7% to 0.9% |
| Serial borrowers (11+ loans) | Top 1% | 70 | 192 | 92 | **47.9%** | 10.2% | 4.7x | 37.0% to 59.1% |
| Serial borrowers (11+ loans) | Top 5% | 170 | 960 | 427 | **44.5%** | 10.2% | 4.4x | 37.4% to 51.6% |
| Serial borrowers (11+ loans) | Top 10% | 285 | 1,920 | 791 | **41.2%** | 10.2% | 4.1x | 36.4% to 46.3% |
| Serial borrowers (11+ loans) | Top 25% | 572 | 4,800 | 1,352 | **28.2%** | 10.2% | 2.8x | 25.0% to 31.1% |
| Serial borrowers (11+ loans) | Rest | 1,056 | 14,403 | 598 | **4.2%** | 10.2% | 0.4x | 3.6% to 4.7% |
| Property | Top 1% | 129 | 578 | 216 | **37.4%** | 4.2% | 8.8x | 29.3% to 45.2% |
| Property | Top 5% | 462 | 2,894 | 829 | **28.6%** | 4.2% | 6.8x | 24.8% to 32.5% |
| Property | Top 10% | 860 | 5,789 | 1,205 | **20.8%** | 4.2% | 4.9x | 18.4% to 23.3% |
| Property | Top 25% | 1,665 | 14,472 | 1,765 | **12.2%** | 4.2% | 2.9x | 11.0% to 13.6% |
| Property | Rest | 3,264 | 43,418 | 687 | **1.6%** | 4.2% | 0.4x | 1.4% to 1.8% |
| Not property | Top 1% | 73 | 469 | 197 | **42.0%** | 1.8% | 23.9x | 29.6% to 53.0% |
| Not property | Top 5% | 319 | 2,349 | 363 | **15.4%** | 1.8% | 8.8x | 11.0% to 20.2% |
| Not property | Top 10% | 630 | 4,699 | 443 | **9.4%** | 1.8% | 5.4x | 7.1% to 11.7% |
| Not property | Top 25% | 1,243 | 11,749 | 576 | **4.9%** | 1.8% | 2.8x | 3.8% to 6.1% |
| Not property | Rest | 2,407 | 35,250 | 251 | **0.7%** | 1.8% | 0.4x | 0.6% to 0.9% |
| Ordinary, not property | Top 1% | 114 | 401 | 19 | **4.7%** | 0.9% | 5.0x | 2.2% to 7.7% |
| Ordinary, not property | Top 5% | 389 | 2,005 | 66 | **3.3%** | 0.9% | 3.5x | 2.1% to 4.5% |
| Ordinary, not property | Top 10% | 645 | 4,010 | 98 | **2.4%** | 0.9% | 2.6x | 1.7% to 3.2% |
| Ordinary, not property | Top 25% | 1,129 | 10,027 | 182 | **1.8%** | 0.9% | 1.9x | 1.4% to 2.3% |
| Ordinary, not property | Rest | 2,074 | 30,081 | 196 | **0.7%** | 0.9% | 0.7x | 0.5% to 0.8% |

## 12 months: any refinance (same lender allowed)

| Group | Level | Firms | Firm-months | Hits | Hit rate | Baseline | Lift | 95% range |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Everyone | Top 1% | 168 | 517 | 362 | **70.0%** | 10.6% | 6.6x | 62.0% to 77.7% |
| Everyone | Top 5% | 525 | 2,587 | 1,323 | **51.1%** | 10.6% | 4.8x | 47.1% to 55.5% |
| Everyone | Top 10% | 961 | 5,174 | 2,092 | **40.4%** | 10.6% | 3.8x | 37.3% to 43.8% |
| Everyone | Top 25% | 2,160 | 12,937 | 3,282 | **25.4%** | 10.6% | 2.4x | 23.5% to 27.2% |
| Everyone | Rest | 4,722 | 38,812 | 2,219 | **5.7%** | 10.6% | 0.5x | 5.1% to 6.3% |
| Ordinary companies (10 loans or fewer) | Top 1% | 132 | 424 | 135 | **31.8%** | 7.4% | 4.3x | 23.9% to 41.4% |
| Ordinary companies (10 loans or fewer) | Top 5% | 563 | 2,123 | 580 | **27.3%** | 7.4% | 3.7x | 23.1% to 31.1% |
| Ordinary companies (10 loans or fewer) | Top 10% | 948 | 4,246 | 983 | **23.2%** | 7.4% | 3.1x | 20.4% to 26.1% |
| Ordinary companies (10 loans or fewer) | Top 25% | 1,889 | 10,617 | 1,613 | **15.2%** | 7.4% | 2.0x | 13.6% to 16.7% |
| Ordinary companies (10 loans or fewer) | Rest | 3,877 | 31,851 | 1,527 | **4.8%** | 7.4% | 0.7x | 4.2% to 5.4% |
| Serial borrowers (11+ loans) | Top 1% | 39 | 92 | 75 | **81.5%** | 25.4% | 3.2x | 67.0% to 92.7% |
| Serial borrowers (11+ loans) | Top 5% | 133 | 464 | 339 | **73.1%** | 25.4% | 2.9x | 64.1% to 81.3% |
| Serial borrowers (11+ loans) | Top 10% | 202 | 928 | 607 | **65.4%** | 25.4% | 2.6x | 58.8% to 72.0% |
| Serial borrowers (11+ loans) | Top 25% | 392 | 2,320 | 1,253 | **54.0%** | 25.4% | 2.1x | 49.1% to 58.7% |
| Serial borrowers (11+ loans) | Rest | 874 | 6,961 | 1,108 | **15.9%** | 25.4% | 0.6x | 13.8% to 18.2% |
| Property | Top 1% | 90 | 278 | 198 | **71.2%** | 13.4% | 5.3x | 61.1% to 80.8% |
| Property | Top 5% | 287 | 1,390 | 759 | **54.6%** | 13.4% | 4.1x | 48.4% to 60.0% |
| Property | Top 10% | 567 | 2,780 | 1,264 | **45.5%** | 13.4% | 3.4x | 40.8% to 49.8% |
| Property | Top 25% | 1,181 | 6,950 | 2,245 | **32.3%** | 13.4% | 2.4x | 29.7% to 34.9% |
| Property | Rest | 2,640 | 20,850 | 1,481 | **7.1%** | 13.4% | 0.5x | 6.2% to 8.0% |
| Not property | Top 1% | 53 | 239 | 146 | **61.1%** | 7.4% | 8.2x | 45.2% to 74.6% |
| Not property | Top 5% | 228 | 1,197 | 444 | **37.1%** | 7.4% | 5.0x | 29.8% to 44.0% |
| Not property | Top 10% | 453 | 2,394 | 619 | **25.9%** | 7.4% | 3.5x | 21.4% to 30.3% |
| Not property | Top 25% | 932 | 5,987 | 956 | **16.0%** | 7.4% | 2.1x | 13.6% to 18.4% |
| Not property | Rest | 2,100 | 17,962 | 819 | **4.6%** | 7.4% | 0.6x | 3.8% to 5.4% |
| Ordinary, not property | Top 1% | 62 | 204 | 36 | **17.6%** | 5.3% | 3.3x | 7.2% to 30.2% |
| Ordinary, not property | Top 5% | 266 | 1,023 | 145 | **14.2%** | 5.3% | 2.7x | 10.1% to 18.8% |
| Ordinary, not property | Top 10% | 439 | 2,046 | 238 | **11.6%** | 5.3% | 2.2x | 8.7% to 14.7% |
| Ordinary, not property | Top 25% | 819 | 5,115 | 447 | **8.7%** | 5.3% | 1.6x | 6.9% to 10.7% |
| Ordinary, not property | Rest | 1,783 | 15,348 | 642 | **4.2%** | 5.3% | 0.8x | 3.4% to 5.0% |

## Reading it

- Lead with **ordinary companies**: top 10% switch lender 4.8% of the time in 90 days (3.3x) and 22.0% in 12 months (3.1x). Mixed rows (Everyone, Not property) are flattered by serial borrowers who refinance all the time.
- **Ordinary, not property** is the weakest group: top 10% 2.3% in 90 days and 11.1% in 12 months, about 2.2x to 2.6x.
- The Rest rows are the other side of the coin: firms the score rates low refinance well below their group's average.
- Absolute rates run high because every firm here borrowed again later; compare rows with each other. A lead list, not a forecast, and not yet tested with brokers or lenders.
