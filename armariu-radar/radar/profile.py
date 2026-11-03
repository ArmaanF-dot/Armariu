"""Company profiles from Companies House's free Basic Company Data, for the Refinance watch experiments.

Basic Company Data is a monthly snapshot of every live company on the register (about 5.6 million
rows, one 450 MB zip) at https://download.companieshouse.gov.uk/en_output.html. It is free to reuse.
The zip is streamed, never saved, and only our companies are kept, plus one count over the whole
register: how many companies share each registered-office postcode (a formation agent's or
accountant's address holds thousands; a trading company's holds a few).

Kept per company (no names of people, no street address):
  postcode_district   the outward half of the registered-office postcode, e.g. "HG1"
  postcode_companies  how many companies on the register share the full postcode
  category            company type (Private Limited Company, LLP, PLC ...)
  account_category    size of the latest accounts (MICRO ENTITY, SMALL, FULL, GROUP, DORMANT ...)
  account_ref_month   financial year end month
  accounts_last / accounts_next_due   latest accounts made up to / next due (snapshot date)
  name_changes        dates of previous-name changes (dated, so a backtest only counts earlier ones)

Snapshot caveat: category, size and address are as of the snapshot, not the backtest date. They
change rarely, but a backtest using them peeks slightly; name-change dates do not.

Output: output/profile/profile.csv (git-ignored). Rules only: no AI.

    python -m radar.profile fetch
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from collections import Counter

import requests

from . import config
from .ownership import _lines

BASE = "https://download.companieshouse.gov.uk/"
OUT_DIR = config.OUTPUT_DIR / "profile"
PROFILE = OUT_DIR / "profile.csv"
FIELDS = ("company_number", "snapshot", "postcode_district", "postcode_companies", "category",
          "account_category", "account_ref_month", "accounts_last", "accounts_next_due", "name_changes")


def _latest() -> str:
    page = requests.get(BASE + "en_output.html", timeout=60).text
    found = sorted(set(re.findall(r'href="(BasicCompanyDataAsOneFile-\d{4}-\d{2}-\d{2}\.zip)"', page)))
    if not found:
        raise RuntimeError("no Basic Company Data file listed")
    return found[-1]


def _iso(d: str) -> str:
    """dd/mm/yyyy -> yyyy-mm-dd ('' if blank)."""
    d = (d or "").strip()
    return f"{d[6:10]}-{d[3:5]}-{d[0:2]}" if len(d) == 10 else ""


def _norm_pc(pc: str) -> str:
    return re.sub(r"\s+", "", (pc or "").upper())


def fetch(numbers: set[str], log=print) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = _latest()
    snapshot = re.search(r"(\d{4}-\d{2}-\d{2})", name).group(1)
    t0, rows, per_pc, header = time.time(), [], Counter(), None
    for i, line in enumerate(_lines(BASE + name)):
        line = line.rstrip(b"\r")
        if not line:
            continue
        rec = next(csv.reader(io.StringIO(line.decode("utf-8", "replace"))))
        if header is None:
            header = {k.strip(): j for j, k in enumerate(rec)}
            continue
        if len(rec) < len(header):
            continue
        pc = _norm_pc(rec[header["RegAddress.PostCode"]])
        if pc:
            per_pc[pc] += 1
        num = rec[header["CompanyNumber"]].strip().upper()
        if num not in numbers:
            continue
        changes = sorted(filter(None, (_iso(rec[header[f"PreviousName_{k}.CONDATE"]]) for k in range(1, 11))))
        raw_pc = (rec[header["RegAddress.PostCode"]] or "").strip().upper()
        rows.append({"company_number": num, "snapshot": snapshot,
                     "postcode_district": raw_pc.split()[0] if " " in raw_pc else raw_pc[:-3] if len(raw_pc) > 4 else "",
                     "_pc": pc, "category": rec[header["CompanyCategory"]].strip(),
                     "account_category": rec[header["Accounts.AccountCategory"]].strip(),
                     "account_ref_month": rec[header["Accounts.AccountRefMonth"]].strip(),
                     "accounts_last": _iso(rec[header["Accounts.LastMadeUpDate"]]),
                     "accounts_next_due": _iso(rec[header["Accounts.NextDueDate"]]),
                     "name_changes": ";".join(changes)})
        if i % 1_000_000 == 0 and i:
            log(f"  {i:,} rows read, {len(rows):,} ours, {time.time() - t0:.0f}s")
    tmp = PROFILE.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            r["postcode_companies"] = per_pc.get(r.pop("_pc"), 0)
            w.writerow(r)
    tmp.replace(PROFILE)
    log(f"{name}: {len(rows):,} of {len(numbers):,} companies, {time.time() - t0:.0f}s")
    return len(rows)


def load(path=PROFILE) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["company_number"]: r for r in csv.DictReader(fh)}


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.profile")
    p.add_argument("cmd", choices=("fetch",))
    p.parse_args(argv)
    numbers = {r[0].upper() for r in connect().execute("SELECT company_number FROM companies")}
    n = fetch(numbers, log=lambda m: print(m, flush=True))
    print(f"{n:,} company profiles -> {PROFILE}")


if __name__ == "__main__":
    sys.exit(main())
