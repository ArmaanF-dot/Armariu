"""Ownership changes from Companies House's free "persons with significant control" (PSC) snapshot.

The snapshot (https://download.companieshouse.gov.uk/en_pscdata.html, rebuilt daily, about 2.7 GB in
33 parts) lists every company's owners, past and present, with the date each became an owner
(`notified_on`) and stopped (`ceased_on`). The parts are streamed and only companies in our database
are kept. No names are stored: each row keeps the owner kind (person, company, legal person), the
date range and the control band, which is all the Refinance watch needs to see "a new owner arrived".

The register is updated by filings made up to a few weeks after the change, so a change counts as
known 30 days after its date (`LAG_DAYS`). A backtest therefore never uses a change before it was
public. Output: output/ownership/psc.csv (git-ignored). Rules only: no AI.

    python -m radar.ownership fetch
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import sys
import time
import zlib

import requests

from . import config

BASE = "https://download.companieshouse.gov.uk/"
OUT_DIR = config.OUTPUT_DIR / "ownership"
PSC = OUT_DIR / "psc.csv"
LAG_DAYS = 30
FIELDS = ("company_number", "owner_id", "kind", "notified_on", "ceased_on", "control")


def _parts() -> list[str]:
    page = requests.get(BASE + "en_pscdata.html", timeout=60).text
    return sorted(set(re.findall(r'href="(psc-snapshot-\d{4}-\d{2}-\d{2}_\d+of\d+\.zip)"', page)),
                  key=lambda s: int(re.search(r"_(\d+)of", s).group(1)))


def _lines(url: str):
    """Stream the single text file inside one snapshot part, line by line."""
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        it = r.iter_content(1 << 20)
        head = b""
        while len(head) < 30:
            head += next(it)
        nlen, elen = struct.unpack("<HH", head[26:30])
        while len(head) < 30 + nlen + elen:
            head += next(it)
        dec, buf = zlib.decompressobj(-15), b""
        for chunk in _chain([head[30 + nlen + elen:]], it):
            buf += dec.decompress(chunk)
            *lines, buf = buf.split(b"\n")
            yield from lines
            if dec.eof:
                break
        if buf:
            yield buf


def _chain(first, rest):
    yield from first
    yield from rest


def _band(natures: list[str]) -> str:
    for band in ("75-to-100", "50-to-75", "25-to-50"):
        if any(band in n for n in natures):
            return band.replace("-to-", "-") + "%"
    return "other control"


def fetch(numbers: set[str], log=print) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PSC.with_suffix(".tmp")
    n = 0
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for part in _parts():
            t0, kept = time.time(), 0
            for i in range(4):
                try:
                    rows = []
                    for line in _lines(BASE + part):
                        if not line.startswith(b'{"company_number":"'):
                            continue
                        num = line[19:line.index(b'"', 19)].decode().upper()
                        if num not in numbers:
                            continue
                        rec = json.loads(line)
                        d = rec.get("data") or {}
                        kind = d.get("kind", "")
                        if not kind.endswith("person-with-significant-control"):
                            continue   # statements ("no PSC", "steps taken") and exemptions
                        self_link = (d.get("links") or {}).get("self", "")
                        rows.append({"company_number": num, "owner_id": hashlib.sha1(self_link.encode()).hexdigest()[:10],
                                     "kind": kind.replace("-person-with-significant-control", ""),
                                     "notified_on": d.get("notified_on", ""), "ceased_on": d.get("ceased_on", ""),
                                     "control": _band(d.get("natures_of_control") or [])})
                    break
                except (requests.RequestException, zlib.error, StopIteration) as e:
                    log(f"  retry {part}: {e}")
                    time.sleep(2 ** i)
            else:
                raise RuntimeError(f"could not read {part}")
            w.writerows(rows)
            kept, n = len(rows), n + len(rows)
            log(f"{part}: {kept} owner records for our companies, {time.time() - t0:.0f}s")
    tmp.replace(PSC)
    return n


def load(path=PSC) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out.setdefault(r["company_number"], []).append(r)
    return out


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.ownership")
    p.add_argument("cmd", choices=("fetch",))
    p.parse_args(argv)
    numbers = {r[0].upper() for r in connect().execute("SELECT company_number FROM companies")}
    n = fetch(numbers, log=lambda m: print(m, flush=True))
    print(f"{n:,} owner records -> {PSC}")


if __name__ == "__main__":
    sys.exit(main())
