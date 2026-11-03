# Armariu data: what is stored, and where the additions read it

Phase 0 of the additions plan asked for an audit before new code. This is it, checked against the
repository and a copy of the live database on 3 November 2026. Nothing here changes the database.

## How the radar works today

| Question | Answer from the repo |
|---|---|
| What the pipeline does | `python -m radar.pipeline run` follows the Companies House charges stream, enriches borrowers, fetches each borrower's full charge history over REST, applies `data/lenders.csv`, queues unknown lender names for the agents, and writes weekly digests. No AI is called in the pipeline. |
| Where state lives | One SQLite file, `data/radar.db`, saved gzipped on the `radar-data` branch by `radar.dbsync` (about 40 MB uncompressed on 3 Nov 2026). The branch is force-pushed with one commit, so the repo does not grow. |
| How it is scheduled | `.github/workflows/radar.yml`, every 3 hours (`23 */3 * * *`); tests in `tests.yml` on every push. |
| How an issue is produced today | The newsletter agent drafts from `output/weekly/*.md` into `drafts/`; Armaan edits in the review app. The monthly extract (`radar.monthly`) feeds the league table. |

**Decision for history (plan question "SQLite or partitioned CSVs"):** keep the existing SQLite on
the `radar-data` branch. It already holds everything the modules need, Actions already restores it
each run, and it stays well under GitHub's file limit. No schema change was needed: every new
module reads the existing tables and writes derived files.

## The plan's core tables, mapped to what exists

| Plan table | Where it lives | Notes |
|---|---|---|
| charges (one row per charge) | `charges` (stream register) + `borrower_history` (each borrower's full REST history) | Merged by charge id in `radar/charge_history.py`; the register copy wins. |
| lenders | `lender_classifications` (one row per normalised name: category, parent firm, source) | Human rules beat seed rules beat agents, as before. |
| lender_aliases | `data/lender_aliases.csv` (new, hand-curated) on top of `data/lender_groups.csv` and `data/lenders.csv` | Resolution order in `radar/aliases.py`. |
| signals (one row per signal, date, rule version) | Derived file `output/signals/signals_<date>.csv` | Recomputed deterministically from the tables; every row carries `rule_version`. The live, append-only `signals_published.csv` starts after the validation gate. |
| outcomes (Module D) | Derived file `output/scorecard/scorecard_backtest.csv` | Computed from `borrower_history` filing dates. |

## Fields in real records

`tests/fixtures/ch_charges_sample.json` holds 20 real stream records (company chargees only).
Fields present: `charge_code`, `charge_number`, `classification`, `created_on`, `delivered_on`,
`etag`, `links`, `more_than_four_persons_entitled`, `particulars`, `persons_entitled`,
`satisfied_on`, `status`, `transactions`. Not present on these records: `secured_details` (the
amount secured), so nothing downstream may claim an amount.

Statuses seen in the database: `outstanding`, `fully-satisfied`, `part-satisfied`.

Things the plan assumed that the data corrects:
- Only 28 register rows keep `raw_json`, so fixtures come from those.
- `meta.coverage_start` is not set in the live database. The modules fall back to the first
  `first_seen_at` (20 Oct 2026), the same rule the weekly digest uses.
- `borrower_history` has no particulars flags, so the "all-assets security" rule can only be scored
  on register charges, not backtested. It waits until after the gate anyway.
