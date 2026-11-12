"""EXPERIMENT (12 Nov 2026): profit and loss facts from the free bulk accounts, to test whether EBITDA
(or a proxy) helps the Refinance watch. Not used by any live score.

Reads the same Companies House monthly accounts zips as radar.accounts (range requests, only our
companies), but keeps profit and loss lines as well as the balance sheet:
  turnover, gross profit, operating profit, profit before tax, profit after tax, depreciation,
  amortisation, interest payable, average employees
for the current period and the prior-year comparative. EBITDA proxy = operating profit + depreciation
+ amortisation (UK filers rarely tag EBITDA itself).

Companies: the radar's own (data/radar.db) and the random sample (data/backfill.db, fetched rows).
A filing counts from the end of the month its zip covers, never its balance sheet date.

    python scripts/pl_facts.py --since 2021-06 --workers 16
Writes output/pl/pl_facts.csv and output/pl/done.txt (resumable).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import accounts, config  # noqa: E402

OUT = config.OUTPUT_DIR / "pl"
FACTS = OUT / "pl_facts.csv"
DONE = OUT / "done.txt"

# our field -> concepts in order of preference (FRS 102 / FRS 105 taxonomy names)
PL = {
    "turnover": ("TurnoverRevenue", "Revenue"),
    "gross_profit": ("GrossProfitLoss",),
    "op_profit": ("OperatingProfitLoss", "ProfitLossFromOperatingActivities"),
    "pbt": ("ProfitLossOnOrdinaryActivitiesBeforeTax", "ProfitLossBeforeTax"),
    "profit": ("ProfitLoss", "ProfitLossForPeriod", "ProfitLossOnOrdinaryActivitiesAfterTax"),
    "dep": ("DepreciationExpensePropertyPlantEquipment", "DepreciationAmortisationImpairmentExpense",
            "DepreciationImpairmentExpensePropertyPlantEquipment",
            "IncreaseFromDepreciationChargeForYearPropertyPlantEquipment",
            "DepreciationAmortisationExpense"),
    "amort": ("AmortisationExpenseIntangibleAssets", "AmortisationImpairmentExpenseIntangibleAssets",
              "IncreaseFromAmortisationChargeForYearIntangibleAssets"),
    "interest": ("InterestPayableSimilarChargesFinanceCosts", "InterestPayableSimilarCharges", "FinanceCosts",
                 "InterestExpense"),
    "employees": ("AverageNumberEmployeesDuringPeriod", "EmployeesTotal"),
}
BS = ("loans_1y", "loans_after", "creditors_1y", "creditors_after", "net_current", "net_assets",
      "total_assets_less_cl", "cash", "fixed_assets")
FIELDS = ("company_number", "balance_sheet_date", "known_from", "source", "n_facts",
          *PL, *[f"prev_{k}" for k in PL], *BS)


def pl_values(facts, when: str) -> dict:
    out = {}
    for field, concepts in PL.items():
        for c in concepts:
            plain = [f.value for f in facts if f.when == when and f.concept == c and not f.dims]
            if plain:
                out[field] = plain[0]
                break
            dimmed = [f.value for f in facts if f.when == when and f.concept == c]
            if dimmed and field in ("dep", "amort", "employees"):
                out[field] = max(dimmed, key=abs)   # class breakdown with no total: largest line
                break
    for k in ("dep", "amort", "interest"):
        if k in out:
            out[k] = abs(out[k])
    return out


def summarise(facts, bs_date: str) -> dict:
    when = bs_date if any(f.when == bs_date for f in facts) else max((f.when for f in facts), default="")
    out = {"n_facts": len(facts), **pl_values(facts, when)}
    pl_names = {c for cs in PL.values() for c in cs}
    earlier = sorted({f.when for f in facts if f.when and f.when < when and f.concept in pl_names})
    if earlier:
        out.update({f"prev_{k}": v for k, v in pl_values(facts, earlier[-1]).items()})
    bs = accounts.summarise(facts, [], bs_date)
    out.update({k: bs[k] for k in BS if k in bs})
    return out


def numbers() -> set[str]:
    out = set()
    for db, q in ((config.ROOT / "data" / "radar.db", "SELECT company_number FROM companies"),
                  (config.ROOT / "data" / "backfill.db", "SELECT company_number FROM fetched")):
        if db.exists():
            out |= {r[0].upper() for r in sqlite3.connect(db).execute(q)}
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2021-06")
    p.add_argument("--workers", type=int, default=16)
    a = p.parse_args(argv)
    nums = numbers()
    print(f"{len(nums):,} companies", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    new = not FACTS.exists()
    total = 0
    with FACTS.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for url, known in accounts.sources(a.since):
            name = url.rsplit("/", 1)[-1]
            if name in done:
                continue
            t0 = time.time()
            try:
                z = accounts.RemoteZip(url)
                idx = z.index()
            except Exception as e:
                print(f"{name}: index failed {e}", flush=True)
                continue
            mine = []
            for fn, entry in idx.items():
                parts = fn.rsplit("/", 1)[-1].split("_")
                if len(parts) >= 4 and parts[2].upper() in nums:
                    mine.append((parts[2].upper(), parts[3][:8], entry))
            local = threading.local()

            def one(item, url=url, size=z.size, known=known, name=name):
                num, bs, entry = item
                if not hasattr(local, "z"):
                    local.z = accounts.RemoteZip.__new__(accounts.RemoteZip)
                    local.z.url, local.z.s, local.z.size = url, requests.Session(), size
                try:
                    facts, _ = accounts.parse(local.z.read(entry).decode("utf-8", "replace"))
                except Exception:
                    return None
                bsd = f"{bs[:4]}-{bs[4:6]}-{bs[6:8]}"
                return {"company_number": num, "balance_sheet_date": bsd, "known_from": known, "source": name,
                        **summarise(facts, bsd)}

            with ThreadPoolExecutor(a.workers) as ex:
                rows = [r for r in ex.map(one, mine) if r]
            w.writerows(rows); fh.flush()
            with DONE.open("a") as d:
                d.write(name + "\n")
            total += len(rows)
            print(f"{name}: {len(idx):,} filings, {len(rows)} ours, {time.time() - t0:.0f}s", flush=True)
    print(f"{total:,} filings -> {FACTS}")


if __name__ == "__main__":
    main()
