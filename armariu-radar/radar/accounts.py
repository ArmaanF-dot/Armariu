"""Company accounts from Companies House's free bulk accounts data, for the Refinance watch.

Companies House publishes every electronically filed set of accounts (iXBRL) in monthly zips, plus a
zip per working day for the current month, at https://download.companieshouse.gov.uk/. Each zip holds
one file per filing, named Prod<n>_<run>_<company number>_<balance sheet date>.html.

The zips are 2 to 3.5 GB, so nothing is downloaded whole. The zip index is read with HTTP range
requests, and only the files of companies in our database are fetched (about 700 a month). From each
filing a handful of balance-sheet facts are kept:

  loans_1y / loans_after   bank loans and borrowings due within one year / after one year
  creditors_1y / creditors_after   all creditors due within / after one year
  net_current              net current assets (negative = more due within a year than in hand)
  net_assets, total_assets_less_cl, cash, investment_property, fixed_assets
  officers                 how many officers the filing names, and a fingerprint of the set (no names
                           are stored: the fingerprint only tells whether the board changed)

A filing counts as known from the end of the month its zip covers (monthly zips) or the day of its
daily zip, never from its balance sheet date, so a backtest never sees accounts before they were public.

Facts go to output/accounts/facts.csv (git-ignored); each source zip is listed in done.txt, so a rerun
only fetches what is new. Rules only: no AI.

    python -m radar.accounts fetch [--since 2023-01] [--workers 12]
    python -m radar.accounts status
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html
import re
import struct
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from . import config

BASE = "https://download.companieshouse.gov.uk/"
OUT_DIR = config.OUTPUT_DIR / "accounts"
FACTS = OUT_DIR / "facts.csv"
DONE = OUT_DIR / "done.txt"
FIELDS = ("company_number", "balance_sheet_date", "known_from", "source", "loans_1y", "loans_after",
          "creditors_1y", "creditors_after", "net_current", "net_assets", "total_assets_less_cl", "cash",
          "investment_property", "fixed_assets", "prev_loans_1y", "prev_loans_after", "prev_creditors_1y",
          "prev_creditors_after", "prev_net_current", "prev_total_assets_less_cl", "officers", "officers_fp")
PREV = ("loans_1y", "loans_after", "creditors_1y", "creditors_after", "net_current", "total_assets_less_cl")
MONTHS = [calendar.month_name[i] for i in range(1, 13)]


# ---------------------------------------------------------------- reading zips over HTTP

class RemoteZip:
    """Just enough of the zip format to list a remote zip and fetch single members by range request."""

    def __init__(self, url: str, session: requests.Session | None = None):
        self.url, self.s = url, session or requests.Session()
        self.size = int(self._head().headers["content-length"])

    def _head(self):
        for i in range(5):
            try:
                r = self.s.head(self.url, timeout=60)
                r.raise_for_status()
                return r
            except requests.RequestException:
                if i == 4:
                    raise
                time.sleep(2 ** i)

    def _get(self, start: int, end: int) -> bytes:
        for i in range(5):
            try:
                r = self.s.get(self.url, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
                if r.status_code == 206:
                    return r.content
                raise requests.RequestException(f"HTTP {r.status_code}")
            except requests.RequestException:
                if i == 4:
                    raise
                time.sleep(2 ** i)

    def index(self) -> dict[str, tuple[int, int, int]]:
        """{filename: (local header offset, compressed size, method)}."""
        tail = self._get(max(0, self.size - 65_557), self.size - 1)
        p = tail.rfind(b"PK\x05\x06")
        cd_size, cd_off = struct.unpack("<II", tail[p + 12:p + 20])
        z64 = tail.rfind(b"PK\x06\x06")   # zip64 end record, for archives over 4 GB or 65k files
        if z64 != -1 and (cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or z64 < p):
            cd_size, cd_off = struct.unpack("<QQ", tail[z64 + 40:z64 + 56])
        cd = self._get(cd_off, cd_off + cd_size - 1)
        out, i = {}, 0
        while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02":
            method, = struct.unpack("<H", cd[i + 10:i + 12])
            csize, usize = struct.unpack("<II", cd[i + 20:i + 28])
            nlen, elen, clen = struct.unpack("<HHH", cd[i + 28:i + 34])
            off, = struct.unpack("<I", cd[i + 42:i + 46])
            name = cd[i + 46:i + 46 + nlen].decode("utf-8", "replace")
            if 0xFFFFFFFF in (csize, off, usize):   # zip64 extra field holds the real values
                extra, j = cd[i + 46 + nlen:i + 46 + nlen + elen], 0
                while j + 4 <= len(extra):
                    tag, ln = struct.unpack("<HH", extra[j:j + 4])
                    if tag == 1:
                        vals, k = extra[j + 4:j + 4 + ln], 0
                        if usize == 0xFFFFFFFF:
                            k += 8
                        if csize == 0xFFFFFFFF:
                            csize, = struct.unpack("<Q", vals[k:k + 8]); k += 8
                        if off == 0xFFFFFFFF:
                            off, = struct.unpack("<Q", vals[k:k + 8])
                    j += 4 + ln
            out[name] = (off, csize, method)
            i += 46 + nlen + elen + clen
        return out

    def read(self, entry: tuple[int, int, int]) -> bytes:
        off, csize, method = entry
        raw = self._get(off, off + 30 + 1024 + csize)
        nlen, elen = struct.unpack("<HH", raw[26:30])
        data = raw[30 + nlen + elen:30 + nlen + elen + csize]
        if len(data) < csize:   # unusually long extra field
            data = self._get(off + 30 + nlen + elen, off + 30 + nlen + elen + csize - 1)
        return zlib.decompress(data, -15) if method == 8 else data


# ---------------------------------------------------------------- parsing one filing

_CTX = re.compile(r"<(?:[\w-]+:)?context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:[\w-]+:)?context>", re.S | re.I)
_INSTANT = re.compile(r"<(?:[\w-]+:)?(?:instant|endDate)>\s*(\d{4}-\d{2}-\d{2})", re.I)
_MEMBER = re.compile(r"<(?:[\w-]+:)?explicitMember[^>]*dimension=\"(?:[\w-]+:)?([^\"]+)\"[^>]*>\s*(?:[\w-]+:)?([^<\s]+)", re.I)
_TYPED = re.compile(r"<(?:[\w-]+:)?typedMember[^>]*dimension=\"(?:[\w-]+:)?([^\"]+)\"", re.I)
_IXNUM = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
_IXTXT = re.compile(r"<ix:nonNumeric\b([^>]*)>(.*?)</ix:nonNumeric>", re.S | re.I)
_ATTR = re.compile(r"([\w:-]+)=\"([^\"]*)\"")
_XMLFACT = re.compile(r"<([\w-]+):(\w+)\b([^>]*\bcontextRef=\"[^\"]+\"[^>]*)>([^<]*)</\1:\2>", re.S)
_TAGS = re.compile(r"<[^>]+>")

MATURITY = {"WithinOneYear": "1y", "CurrentFinancialInstruments": "1y", "AfterOneYear": "after",
            "Non-currentFinancialInstruments": "after", "BetweenOneFiveYears": "after",
            "BetweenOneTwoYears": "after", "BetweenTwoFiveYears": "after", "MoreThanFiveYears": "after",
            "AfterOneYearWithinFiveYears": "after", "AfterFiveYears": "after"}
MATURITY_DIMS = {"MaturitiesOrExpirationPeriodsDimension", "FinancialInstrumentCurrentNon-currentDimension"}
LOANS = ("TotalBorrowings", "BankBorrowingsOverdrafts", "BankBorrowings", "LoansFromBanks", "Borrowings",
         "OtherBorrowings", "BankOverdrafts", "MortgagesAndSecuredLoans", "OtherLoans")
PLAIN = {"NetCurrentAssetsLiabilities": "net_current", "NetAssetsLiabilities": "net_assets",
         "TotalAssetsLessCurrentLiabilities": "total_assets_less_cl", "CashBankOnHand": "cash",
         "InvestmentProperty": "investment_property", "InvestmentPropertyFairValueModel": "investment_property",
         "FixedAssets": "fixed_assets"}


def _num(text: str, attrs: dict) -> float | None:
    t = html.unescape(_TAGS.sub("", text)).strip().replace(",", "").replace(" ", "")
    if t in ("", "-", "—", "–", "nil", "Nil"):
        v = 0.0
    else:
        t = t.strip("()")
        try:
            v = float(t)
        except ValueError:
            return None
    try:
        v *= 10 ** int(attrs.get("scale", "0") or 0)
    except ValueError:
        pass
    return -v if attrs.get("sign") == "-" else v


@dataclass
class Fact:
    concept: str
    when: str
    dims: dict
    value: float


def parse(doc: str) -> tuple[list[Fact], list[str]]:
    """All numeric facts with their period end and dimensions, plus the officer names filed."""
    ctx = {}
    for cid, body in _CTX.findall(doc):
        when = _INSTANT.findall(body)
        dims = {d: m.split(":")[-1] for d, m in _MEMBER.findall(body)}
        for d in _TYPED.findall(body):
            dims.setdefault(d, "typed")
        ctx[cid] = (max(when) if when else "", dims)
    facts = []
    for a, text in _IXNUM.findall(doc):
        attrs = dict(_ATTR.findall(a))
        c = ctx.get(attrs.get("contextRef", ""))
        v = _num(text, attrs)
        if c and v is not None:
            facts.append(Fact(attrs.get("name", "").split(":")[-1], c[0], c[1], v))
    if not facts:   # plain XBRL (.xml)
        for _, concept, a, text in _XMLFACT.findall(doc):
            attrs = dict(_ATTR.findall(a))
            c = ctx.get(attrs.get("contextRef", ""))
            v = _num(text, attrs)
            if c and v is not None:
                facts.append(Fact(concept, c[0], c[1], v))
    officers = []
    for a, text in _IXTXT.findall(doc):
        if dict(_ATTR.findall(a)).get("name", "").split(":")[-1] == "NameEntityOfficer":
            n = re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text))).strip().upper()
            if n:
                officers.append(n)
    return facts, sorted(set(officers))


def summarise(facts: list[Fact], officers: list[str], bs_date: str) -> dict:
    """The kept balance-sheet values at the balance sheet date, plus the prior year's comparatives."""
    when = bs_date if any(f.when == bs_date for f in facts) else max((f.when for f in facts), default="")
    out = _values(facts, when)
    earlier = sorted({f.when for f in facts if f.when and f.when < when and (f.concept == "Creditors" or f.concept in PLAIN)})
    if earlier:
        prev = _values(facts, earlier[-1])
        out.update({f"prev_{k}": prev[k] for k in PREV if k in prev})
    out["officers"] = len(officers)
    out["officers_fp"] = hashlib.sha1("|".join(officers).encode()).hexdigest()[:10] if officers else ""
    return out


def _values(facts: list[Fact], when: str) -> dict:
    out: dict = {}
    loans: dict = {"1y": {}, "after": {}}
    for f in facts:
        if f.when != when:
            continue
        mat = [MATURITY.get(f.dims[d]) for d in f.dims if d in MATURITY_DIMS]
        other = [d for d in f.dims if d not in MATURITY_DIMS]
        if other:
            continue
        bucket = mat[0] if mat else None
        if f.concept == "Creditors" and bucket:
            k = f"creditors_{bucket}"
            out[k] = max(out.get(k, 0.0), abs(f.value))
        elif f.concept in LOANS and bucket:
            loans[bucket][f.concept] = abs(f.value)
        elif f.concept in PLAIN and not f.dims:
            out[PLAIN[f.concept]] = f.value
    for bucket, vals in loans.items():
        if vals:   # avoid double counting: totals beat their parts, else the largest line
            out[f"loans_{bucket}"] = vals.get("TotalBorrowings", max(vals.values()))
    return out


# ---------------------------------------------------------------- sources and fetching

def sources(since: str = "2023-01") -> list[tuple[str, str]]:
    """(url, known_from) for every monthly zip from `since`, then the daily zips after the last month."""
    s = requests.Session()
    found = {}
    for page, prefix in (("historicmonthlyaccountsdata.html", ""), ("en_monthlyaccountsdata.html", "")):
        for href in re.findall(r'href="([^"]*Accounts_Monthly_Data-(\w+?)(\d{4})\.zip)"', s.get(BASE + page, timeout=60).text):
            url, mon, yr = href
            if mon in MONTHS:
                m = MONTHS.index(mon) + 1
                found[f"{yr}-{m:02d}"] = BASE + prefix + url
    months = sorted(k for k in found if k >= since)
    out = []
    for k in months:
        y, m = map(int, k.split("-"))
        out.append((found[k], date(y, m, calendar.monthrange(y, m)[1]).isoformat()))
    last = months[-1] if months else since
    for href, d in re.findall(r'href="(Accounts_Bulk_Data-(\d{4}-\d{2}-\d{2})\.zip)"', s.get(BASE + "en_accountsdata.html", timeout=60).text):
        if d[:7] > last:
            out.append((BASE + href, d))
    return sorted(set(out), key=lambda t: t[1])


def _done() -> set[str]:
    return set(DONE.read_text().split()) if DONE.exists() else set()


def fetch(numbers: set[str], since: str = "2023-01", workers: int = 12, log=print) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done, total = _done(), 0
    lock = threading.Lock()
    new_file = not FACTS.exists()
    with FACTS.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for url, known in sources(since):
            name = url.rsplit("/", 1)[-1]
            if name in done:
                continue
            t0 = time.time()
            z = RemoteZip(url)
            idx = z.index()
            mine = []
            for fn, entry in idx.items():
                parts = fn.rsplit("/", 1)[-1].split("_")
                if len(parts) >= 4 and parts[2].upper() in numbers:
                    mine.append((parts[2].upper(), parts[3][:8], entry))
            local = threading.local()

            def one(item):
                num, bs, entry = item
                if not hasattr(local, "z"):
                    local.z = RemoteZip.__new__(RemoteZip)
                    local.z.url, local.z.s, local.z.size = url, requests.Session(), z.size
                try:
                    doc = local.z.read(entry).decode("utf-8", "replace")
                    facts, officers = parse(doc)
                except Exception as e:   # one bad file never stops the run
                    log(f"  skip {num} {bs}: {e}")
                    return None
                bsd = f"{bs[:4]}-{bs[4:6]}-{bs[6:8]}"
                row = {"company_number": num, "balance_sheet_date": bsd, "known_from": known, "source": name}
                row.update(summarise(facts, officers, bsd))
                return row

            with ThreadPoolExecutor(workers) as ex:
                rows = [r for r in ex.map(one, mine) if r]
            with lock:
                w.writerows(rows); fh.flush()
                with DONE.open("a") as d:
                    d.write(name + "\n")
            total += len(rows)
            log(f"{name}: {len(idx):,} filings, {len(rows)} ours, {time.time() - t0:.0f}s")
    return total


def load(path: Path = FACTS) -> dict[str, list[dict]]:
    """{company number: filings sorted by known_from}; numbers parsed to float, blanks to None."""
    out: dict[str, list[dict]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            for k in FIELDS[4:-1]:
                r[k] = float(r[k]) if r.get(k) not in (None, "") else None
            out.setdefault(r["company_number"], []).append(r)
    for v in out.values():
        v.sort(key=lambda r: (r["known_from"], r["balance_sheet_date"]))
    return out


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.accounts")
    p.add_argument("cmd", choices=("fetch", "status"))
    p.add_argument("--since", default="2023-01")
    p.add_argument("--workers", type=int, default=12)
    a = p.parse_args(argv)
    if a.cmd == "status":
        acc = load()
        print(f"{sum(len(v) for v in acc.values()):,} filings for {len(acc):,} companies; {len(_done())} source zips read")
        return
    numbers = {r[0].upper() for r in connect().execute("SELECT company_number FROM companies")}
    n = fetch(numbers, a.since, a.workers, log=lambda m: print(m, flush=True))
    print(f"{n:,} new filings -> {FACTS}")


if __name__ == "__main__":
    sys.exit(main())
