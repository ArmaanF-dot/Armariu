"""Refinance watch: which companies are most likely to refinance in the next 12 months.

Filings carry no loan end dates, so this is odds, not a forecast. Each company with a charge still
outstanding gets points for a handful of clues, learned from history:

  age_cls   age of its newest open charge, crossed with that lender's type (bridging counted apart)
  recent    how many charges it filed in the last six months (active borrowers move more)
  nopen     how many charges it has outstanding
  pullback  whether the newest open charge's lender has cut its new lending by half or more
  property  whether the company is in property
  since_sat when the company last repaid a loan (rw3)
  lender_rate  for each open loan, how often that lender's loans of the same age a year earlier were
            repaid within that year; the highest across its open loans (rw3; the lender's type when
            the lender has fewer than 20 such loans)

The outcome is a refinance: within 12 months an outstanding charge is marked satisfied and a new
charge is filed. Points are log-odds against the base rate, learned on cutoffs whose outcome window had
closed by the run date, so nothing peeks ahead. The odds shown next to a company are the hit rate of
its score band in that history. `backtest` learns on an earlier year and scores later dates it never
saw. Measured 3 Nov 2026 (12 months): top 1% refinanced 54%, against 11% for all companies; with a
90-day window the same score gave 22% against 3%. History covers only companies that borrowed after
20 Oct 2026, which flatters absolute rates; the lift is the steadier number.

rw2 (tested 3 Nov 2026, not live) adds clues from Companies House's free accounts and owner register
(radar.accounts, radar.ownership): bank loans due within a year, the share of the balance sheet due
within a year, how debts moved against the year before, new owners, and board changes. On the same
unseen months it did worse at the top: top 1% hit 45.7% at 12 months (rw1 52.9%) and 12.7% at 90
days (rw1 21.6%). Owner changes reversed between the learning and test years, and the accounts clues
mostly repeat what recent borrowing already says; most small firms file accounts with no loan split.
`compare` reruns the test, so rw2 can be promoted if it wins once more history has built up.

rw3 (live from 4 Nov 2026) adds since_sat and lender_rate, both from the charge register alone, and now
counts a charge only once it was filed (rw1 counted charges from their creation date, a small look-ahead
in its backtest). On two unseen years (2024 and 2025 dates, docs/refi_features.md) it lifted the top 5%
from 41% to 52% (2025) and 39% to 50% (2024), and the top 10% by about 2 points; for ordinary companies
(10 or fewer charges) the gain is about 1 point in the top 10%. 16 other history clues and the charges'
own wording (fixed or floating, all assets, title numbers, negative pledge) gave nothing measurable.

Odds are read from two tables since 4 Nov 2026, one for ordinary companies and one for serial borrowers
(SERIAL_MIN or more charges filed): a shared table showed ordinary companies' top tenth 28% against 21%
actual and serial borrowers' top fifth 36% against 49%. Logistic, boosted, survival and blended models
were also tested and none ranked ordinary companies better (docs/refi_methods.md).

At 90 days, row by row (ordinary, non-property, property, serial), with counts, 95% ranges and the
"switched lender" definition: radar.refi_accuracy and docs/refi_accuracy_90d.md.

    python -m radar.refi_watch [--as-of 2026-11-03] [--top 200]
    python -m radar.refi_watch backtest
    python -m radar.refi_watch compare   # rw1 against rw3 and rw2, 90 days and 12 months (needs accounts + ownership fetched)
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Company, load_companies

MODEL_VERSION = "rw3"   # the live score: rw1 plus two history clues (4 Nov 2026); rw2 lost on 3 Nov 2026
WINDOW = 365   # days; 90 and 180 were tested too (see the docstring)
CLUES_RW1 = ("age_cls", "recent", "nopen", "pullback", "property")
CLUES_RW3 = CLUES_RW1 + ("since_sat", "lender_rate")
CLUES_FILED = ("due_1y", "short_debt", "debt_shift", "owner_change", "board_change", "director_change")   # rw2: accounts and owner register
CLUES = CLUES_RW3                  # live clues
CLUES_RW2 = CLUES_RW1 + CLUES_FILED   # experiment: `python -m radar.refi_watch compare`
LABELS = {
    "age_cls": "Newest open loan", "recent": "Recent borrowing", "nopen": "Loans outstanding",
    "pullback": "Lender's new lending", "property": "Property company",
    "since_sat": "Last loan repaid", "lender_rate": "Lender's repayment rate at this loan age",
    "due_1y": "Bank loans in the latest accounts", "short_debt": "Share of the balance sheet due within a year",
    "debt_shift": "Change since the year before", "owner_change": "Owners",
    "director_change": "Director appointments", "board_change": "Directors",
}
SERIAL_MIN = 11   # charges filed before the date; serial borrowers get their own odds table
CAVEAT = ("Odds, not a forecast: filings carry no loan end dates. The odds are how often companies with a "
          "similar score refinanced within 12 months in the past.")


def _d(s):
    return date.fromisoformat(s[:10]) if s else None


def _start(c):
    return _d(c.created or c.filed)


def _lender(c) -> tuple[str | None, str]:
    """(key, type) of a charge's main lender; bridging lenders get their own type."""
    if not c.lenders:
        return None, "other"
    name, key, cls = sorted(c.lenders, key=lambda t: (t[2] == "trustee_agent", t[0]))[0]
    return key, ("bridging" if "BRIDG" in (name or "").upper() else cls)


def _age_band(years: float) -> str:
    return "under 6 months" if years < 0.5 else "6 to 12 months" if years < 1 else "1 to 2 years" if years < 2 \
        else "2 to 5 years" if years < 5 else "over 5 years"


class Volumes(dict):
    """Lender key -> Counter of new charges by (year, month), plus each lender's (and lender type's)
    charges as (filed, start, satisfied) for the repayment-rate clue."""

    def __init__(self):
        super().__init__()
        self.by_key: dict = defaultdict(list)
        self.by_cls: dict = defaultdict(list)
        self.cache: dict = {}


def lender_volumes(companies: dict[str, Company]) -> Volumes:
    vol = Volumes()
    for co in companies.values():
        for c in co.charges:
            f = _d(c.filed)
            if f:
                key, cls = _lender(c)
                vol.setdefault(key, Counter())[(f.year, f.month)] += 1
                t = (f, _start(c) or f, _d(c.satisfied))
                vol.by_key[key].append(t)
                vol.by_cls[cls].append(t)
    for d in (vol.by_key, vol.by_cls):
        for v in d.values():
            v.sort(key=lambda t: t[0])
    return vol


def repay_rate(vol: Volumes, key, cls: str, age_days: int, on: date) -> float | None:
    """Of this lender's loans that were the same age a year before `on`, the share repaid since.

    Uses only charges filed, and repayments filed, by `on` (a satisfaction's date is its filing date).
    Falls back to the lender's type when the lender had fewer than 20 such loans."""
    band = _age_band(age_days / 365.25)

    def table(kind, k, rows):
        """One pass over a lender's (or type's) loans: (loans, repaid) by age band a year before `on`."""
        ck = (kind, k, on)
        if ck not in vol.cache:
            p, out = on - timedelta(days=365), defaultdict(lambda: [0, 0])
            for f, start, sat in rows:
                if f >= p:
                    break
                if sat and sat <= p:
                    continue
                t = out[_age_band((p - start).days / 365.25)]
                t[0] += 1
                t[1] += bool(sat and sat <= on)
            vol.cache[ck] = dict(out)
        n, s = vol.cache[ck].get(band, (0, 0))
        return s / n if n >= 20 else None

    r = table("key", key, vol.by_key.get(key, [])) if key else None
    return r if r is not None else table("cls", cls, vol.by_cls.get(cls, []))


def _rate_band(r: float | None) -> str:
    return "unknown" if r is None else "under 10%" if r < 0.10 else "10 to 20%" if r < 0.20 \
        else "20 to 35%" if r < 0.35 else "over 35%"


def _pullback(vol, key, on: date) -> str:
    v = vol.get(key)
    if not v:
        return "unknown"

    def months(a, b):
        tot = 0
        for k in range(a, b):
            y, m = on.year, on.month - 1 - k
            while m <= 0:
                m += 12; y -= 1
            tot += v[(y, m)]
        return tot

    recent, before = months(0, 6), months(6, 18) / 2
    if before < 10:
        return "small lender"
    r = recent / before
    return "falling by half or more" if r < 0.5 else "steady" if r < 1.5 else "growing"


@dataclass
class Filed:
    """Accounts and owner-register history (radar.accounts, radar.ownership); empty when not fetched."""
    accounts: dict = field(default_factory=dict)   # number -> filings sorted by known_from
    owners: dict = field(default_factory=dict)     # number -> owner records
    directors: dict = field(default_factory=dict)  # number -> [(appointed_on, resigned_on)], from borrower_officers


def load_directors(conn) -> dict:
    """Officer appointments the pipeline fetched (ingest.officers); empty until it has run."""
    out: dict = {}
    if conn is None or not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'borrower_officers'").fetchone():
        return out
    for r in conn.execute("SELECT company_number, appointed_on, resigned_on FROM borrower_officers"):
        out.setdefault(r[0], []).append((r[1], r[2]))
    return out


def load_filed(conn=None) -> Filed:
    from . import accounts, ownership
    return Filed(accounts.load(), ownership.load(), load_directors(conn))


def _money(v) -> bool:
    return v is not None and v >= 1000


def filed_features(co: Company, on: date, filed: Filed | None) -> dict:
    """rw2 clues from the latest accounts and the owner register that were public before `on`."""
    if filed is None or not (filed.accounts or filed.owners or filed.directors):
        return {k: "not checked" for k in CLUES_FILED}
    accs = [a for a in filed.accounts.get(co.number, []) if _d(a["known_from"]) < on]
    accs = [a for a in accs if (on - _d(a["balance_sheet_date"])).days <= 730]
    out = {}
    if not accs:
        out["due_1y"] = out["short_debt"] = out["debt_shift"] = "no recent accounts"
        out["board_change"] = "unknown"
    else:
        a = accs[-1]
        l1, la = a["loans_1y"], a["loans_after"]
        due_by = _d(a["balance_sheet_date"]) + timedelta(days=365)
        if l1 is None and la is None:
            out["due_1y"] = "not broken down"
        elif _money(l1) and not _money(la):
            out["due_1y"] = "all due within a year" + (", due date passed" if on > due_by else "")
        elif _money(l1):
            out["due_1y"] = "some due within a year" + (", due date passed" if on > due_by else "")
        elif _money(la):
            out["due_1y"] = "all due after a year"
        else:
            out["due_1y"] = "none"
        c1, ca, tl = a["creditors_1y"], a["creditors_after"] or 0.0, a["total_assets_less_cl"]
        if c1 is None or tl is None or c1 + tl <= 0:
            out["short_debt"] = "not broken down"
        else:
            r = c1 / (c1 + tl)   # current creditors / total assets
            out["short_debt"] = "under a quarter" if r < 0.25 else "a quarter to a half" if r < 0.5 \
                else "half to three quarters" if r < 0.75 else "over three quarters"
        out["debt_shift"] = _debt_shift(a)
        fps = [x["officers_fp"] for x in accs if x["officers_fp"]]
        out["board_change"] = "unknown" if len(fps) < 2 else "changed between the last two accounts" \
            if fps[-1] != fps[-2] else "same as the accounts before"
    created = _d(co.created)
    lag = timedelta(days=ownership_lag())
    changes = []
    for o in filed.owners.get(co.number, []):
        for k in ("notified_on", "ceased_on"):
            d = _d(o.get(k))
            if not d or d + lag >= on or d == date(2016, 4, 6):   # 6 Apr 2016: the register began
                continue
            if k == "notified_on" and created and (d - created).days <= 60:
                continue   # the founding owners, not a change
            changes.append(d)
    last = max(changes, default=None)
    months = (on - last).days / 30.44 if last else None
    out["director_change"] = _director_change(co, on, filed, lag)
    out["owner_change"] = "no change on record" if last is None else "changed in the last 6 months" if months < 6 \
        else "changed 6 to 12 months ago" if months < 12 else "changed 1 to 3 years ago" if months < 36 else "changed over 3 years ago"
    return out


def _debt_shift(a: dict) -> str:
    """How the debts in the latest accounts moved against the year before (comparatives in the same filing)."""
    after, p_after = a.get("creditors_after") or 0.0, a.get("prev_creditors_after")
    c1, p_c1 = a.get("creditors_1y"), a.get("prev_creditors_1y")
    if p_after is None and p_c1 is None:
        return "no comparatives"
    if (p_after or 0) >= 50_000 and after < 0.2 * p_after:
        # long-term debt left the after-one-year line: it fell due (or was repaid)
        return "long-term debt now due or repaid" if (c1 or 0) > (p_c1 or 0) else "long-term debt repaid"
    if c1 is not None and p_c1 and c1 >= 100_000 and c1 > 1.5 * p_c1:
        return "debts due within a year up by half or more"
    if after >= 50_000 and (p_after or 0) < 0.5 * after:
        return "new long-term debt"
    return "little change"


def _director_change(co: Company, on: date, filed: Filed, lag: timedelta) -> str:
    """Latest director appointment or resignation public before `on` (founding directors ignored)."""
    if co.number not in filed.directors:
        return "not checked"
    created, dates = _d(co.created), []
    for appointed, resigned in filed.directors[co.number]:
        a, r = _d(appointed), _d(resigned)
        if a and not (created and (a - created).days <= 60):
            dates.append(a)
        if r:
            dates.append(r)
    dates = [d for d in dates if d + lag < on]
    if not dates:
        return "no change on record"
    months = (on - max(dates)).days / 30.44
    return "changed in the last 6 months" if months < 6 else "changed 6 to 12 months ago" if months < 12 \
        else "changed over a year ago"


def ownership_lag() -> int:
    from .ownership import LAG_DAYS
    return LAG_DAYS


def features(co: Company, on: date, vol, filed: Filed | None = None) -> dict | None:
    """Clues for one company on one date, or None if it has nothing outstanding."""
    known = [c for c in co.charges if _d(c.filed) and _d(c.filed) < on]   # public before `on`
    open_ = [c for c in known if not (_d(c.satisfied) and _d(c.satisfied) <= on)]
    if not open_:
        return None
    sats = [_d(c.satisfied) for c in known if _d(c.satisfied) and _d(c.satisfied) <= on]
    since = (on - max(sats)).days if sats else None
    rates = [repay_rate(vol, *_lender(c), (on - (_start(c) or _d(c.filed))).days, on) for c in open_] if isinstance(vol, Volumes) else []
    newest = max(open_, key=lambda c: (_start(c) or _d(c.filed), c.charge_id))
    key, cls = _lender(newest)
    recent = sum(1 for c in co.charges if _d(c.filed) and on - timedelta(days=183) <= _d(c.filed) < on)
    return {
        "age_cls": f"{_age_band((on - (_start(newest) or _d(newest.filed))).days / 365.25)}, {cls.replace('_', ' ')}",
        "recent": "none" if recent == 0 else "one" if recent == 1 else "two or more",
        "nopen": "one" if len(open_) == 1 else "two or three" if len(open_) <= 3 else "four or more",
        "pullback": _pullback(vol, key, on),
        "property": "yes" if co.sector == "Property and real estate" else "no",
        "since_sat": "never" if since is None else "in the last 6 months" if since < 183 else "6 to 12 months ago" \
            if since < 365 else "1 to 3 years ago" if since < 1096 else "over 3 years ago",
        # highest repayment rate among its open loans: how often that lender's loans of that age were repaid within a year
        "lender_rate": _rate_band(max((r for r in rates if r is not None), default=None)),
        **filed_features(co, on, filed),
        "_newest": newest,
        "_serial": sum(1 for c in co.charges if _d(c.filed) and _d(c.filed) < on) >= SERIAL_MIN,
    }


def outcome(co: Company, on: date, window: int | None = None) -> bool:
    end = on + timedelta(days=window or WINDOW)
    sat = any(_d(c.satisfied) and on < _d(c.satisfied) <= end and _start(c) and _start(c) < on for c in co.charges)
    new = any(_d(c.filed) and on < _d(c.filed) <= end for c in co.charges)
    return sat and new


def examples(companies, cutoffs, vol, filed: Filed | None = None, window: int | None = None):
    out = []
    for co in companies.values():
        for on in cutoffs:
            f = features(co, on, vol, filed)
            if f:
                out.append((f, outcome(co, on, window)))
    return out


class Model:
    def __init__(self, rows, clues=CLUES):
        self.clues = tuple(clues)
        self.n = len(rows)
        self.base = (sum(y for _, y in rows) / self.n) if self.n else 0.0
        self.points: dict = {}
        for clue in self.clues:
            cnt = defaultdict(lambda: [0, 0])
            for x, y in rows:
                cnt[x[clue]][0] += 1; cnt[x[clue]][1] += y
            for v, (n, h) in cnt.items():   # smoothed towards the base rate; tiny groups score 0
                self.points[(clue, v)] = math.log(((h + 2 * self.base) / (n + 2)) / self.base) if n >= 30 and self.base else 0.0
        # calibration: hit rate by score band (20 equal bands), one table for everyone and one each for
        # ordinary and serial borrowers. Serial borrowers refinance far more often at the same score, so a
        # shared table understated their odds and overstated ordinary companies' (4 Nov 2026 backtest).
        self.bands = self._bands([(self.score(x), y) for x, y in rows])
        self.seg_bands = {}
        for seg in (False, True):
            part = [(self.score(x), y) for x, y in rows if bool(x.get("_serial")) == seg]
            if len(part) >= 400:
                self.seg_bands[seg] = self._bands(part)

    @staticmethod
    def _bands(scored, n=20):
        scored = sorted(scored, key=lambda t: t[0])
        out, k = [], max(1, len(scored) // n)
        for i in range(0, len(scored), k):
            part = scored[i:i + k]
            out.append((part[0][0], sum(y for _, y in part) / len(part)))
        return out

    def score(self, x) -> float:
        return sum(self.points.get((c, x[c]), 0.0) for c in self.clues)

    def odds(self, s: float, serial: bool | None = None) -> float:
        bands = self.seg_bands.get(serial, self.bands) if serial is not None else self.bands
        rate = bands[0][1] if bands else self.base
        for lo, r in bands:
            if s >= lo:
                rate = r
        return rate

    def reasons(self, x, top=3) -> list[str]:
        parts = sorted(((self.points.get((c, x[c]), 0.0), c) for c in self.clues), reverse=True)
        return [f"{LABELS[c]}: {x[c]}" for p, c in parts[:top] if p > 0.05]


def _cutoffs(end: date, years: float = 1.5, step: int = 61) -> list[date]:
    out, on = [], end
    while on > end - timedelta(days=int(365.25 * years)):
        out.append(on); on -= timedelta(days=step)
    return sorted(out)


def learn(companies, as_of: date, vol=None, filed: Filed | None = None) -> Model:
    vol = vol or lender_volumes(companies)
    return Model(examples(companies, _cutoffs(as_of - timedelta(days=WINDOW + 1)), vol, filed))


def watchlist(companies, as_of: date, top: int = 200, model: Model | None = None,
              filed: Filed | None = None) -> tuple[list[dict], Model]:
    vol = lender_volumes(companies)
    model = model or learn(companies, as_of, vol, filed)
    rows = []
    for co in companies.values():
        f = features(co, as_of, vol, filed)
        if not f:
            continue
        s = model.score(f)
        newest = f["_newest"]
        odds = model.odds(s, f["_serial"])
        rows.append({
            "company_number": co.number, "company_name": co.name, "sector": co.sector, "score": round(s, 3),
            "odds_12m": round(odds, 4), "base_rate": round(model.base, 4),
            "lift": round(odds / model.base, 1) if model.base else None,
            "serial_borrower": f["_serial"],
            "reasons": "; ".join(model.reasons(f)),
            "newest_lender": ", ".join(sorted({"A private individual" if cls == "individual" else n for n, _, cls in newest.lenders})),
            "newest_created": newest.created or newest.filed, "newest_filing_url": newest.url, "model_version": MODEL_VERSION,
            "filing_url": f"https://find-and-update.company-information.service.gov.uk/company/{co.number}/charges",
        })
    rows.sort(key=lambda r: (-r["score"], r["company_number"]))
    return rows[:top], model


def backtest(companies, train_end: date = date(2025, 1, 1), test_start: date = date(2025, 4, 1),
             test_end: date = date(2026, 10, 20), filed: Filed | None = None, clues=CLUES,
             window: int | None = None, vol=None, strict: bool = False) -> dict:
    """Learn on the 12 months to train_end, score test cutoffs the model never saw.

    strict=True also drops training cutoffs whose outcome window runs into the test period."""
    window = window or WINDOW
    vol = vol or lender_volumes(companies)
    train = [c for c in _cutoffs(train_end, years=1.0) if not strict or c + timedelta(days=window) <= test_start]
    model = Model(examples(companies, train, vol, filed, window), clues)
    tests, on = [], test_start
    while on <= test_end - timedelta(days=window):
        tests.append(on); on += timedelta(days=61)
    te = examples(companies, tests, vol, filed, window)
    base = sum(y for _, y in te) / len(te) if te else 0.0
    ranked = sorted(((model.score(x), y) for x, y in te), key=lambda t: -t[0])
    tiers = []
    for q in (0.01, 0.05, 0.10, 0.25):
        k = max(1, int(len(ranked) * q))
        hit = sum(y for _, y in ranked[:k]) / k
        tiers.append({"top": f"{int(q * 100)}%", "companies": k, "hit_rate": round(hit, 4), "lift": round(hit / base, 1) if base else None})
    extra = set(clues) - set(CLUES_RW1)
    return {"label": "BACKTEST", "model_version": "rw2" if extra & set(CLUES_FILED) else "rw3" if extra else "rw1",
            "window_days": window, "train_end": train_end.isoformat(),
            "train_cutoffs": [t.isoformat() for t in train], "test_cutoffs": [t.isoformat() for t in tests], "rows": len(te), "base_rate": round(base, 4), "tiers": tiers}


# 90 days: learn on the year to Jul 2025, test Oct 2025 on; 12 months: the default split above
SPLITS = {90: dict(train_end=date(2025, 7, 1), test_start=date(2025, 10, 1), test_end=date(2026, 10, 1)),
          365: dict(train_end=date(2025, 1, 1), test_start=date(2025, 4, 1), test_end=date(2026, 10, 20))}


def compare(companies, filed: Filed) -> list[dict]:
    """rw1 against rw2 and against rw1 plus each new clue alone, on the same unseen dates."""
    vol, out = lender_volumes(companies), []
    variants = [("rw1", CLUES_RW1), ("rw3 (live)", CLUES_RW3), ("rw2", CLUES_RW2)] + [(f"rw1 + {LABELS[c].lower()}", CLUES_RW1 + (c,)) for c in CLUES_FILED]
    for window, split in SPLITS.items():
        for name, clues in variants:
            r = backtest(companies, filed=filed, clues=clues, window=window, vol=vol, **split)
            out.append({"window_days": window, "variant": name, **r})
    return out


def render_compare(results: list[dict]) -> str:
    out = ["BACKTEST: Refinance watch rw1 against rw2 (accounts and owner clues)", "",
           "| Window | Score | Base rate | Top 1% | Top 5% | Top 10% |", "|---|---|---:|---:|---:|---:|"]
    for r in results:
        t = {x["top"]: x for x in r["tiers"]}
        out.append(f"| {r['window_days']} days | {r['variant']} | {r['base_rate']:.1%} | " +
                   " | ".join(f"{t[k]['hit_rate']:.1%} ({t[k]['lift']}x)" for k in ("1%", "5%", "10%")) + " |")
    return "\n".join(out) + "\n"


def render_md(rows, bt, as_of: date) -> str:
    out = [f"# Refinance watch, {as_of.isoformat()}", "", f"_{CAVEAT}_", ""]
    if bt:
        out += [f"Backtest ({bt['model_version']}): learned to {bt['train_end']}, tested on {len(bt['test_cutoffs'])} later dates "
                f"({bt['rows']:,} company-dates). Base rate {bt['base_rate']:.1%}.", "",
                "| Top | Companies | Refinanced in 12 months | Lift |", "|---|---:|---:|---:|"]
        out += [f"| {t['top']} | {t['companies']:,} | {t['hit_rate']:.1%} | {t['lift']}x |" for t in bt["tiers"]]
        out.append("")
    out += ["| Company | Sector | Odds (12 months) | Why |", "|---|---|---:|---|"]
    out += [f"| {r['company_name']} ({r['company_number']}) | {r['sector']} | {r['odds_12m']:.0%} | {r['reasons']} |" for r in rows[:50]]
    return "\n".join(out) + "\n"


def write(rows, bt, out_dir, as_of: date) -> tuple[Path, Path]:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    csv_p, md_p = out / f"refi_watch_{as_of.isoformat()}.csv", out / f"refi_watch_{as_of.isoformat()}.md"
    with csv_p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["company_number"])
        w.writeheader(); w.writerows(rows)
    md_p.write_text(render_md(rows, bt, as_of), encoding="utf-8")
    return csv_p, md_p


def main(argv=None):
    from .aliases import Resolver
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.refi_watch")
    p.add_argument("cmd", nargs="?", default="list", choices=("list", "backtest", "compare"))
    p.add_argument("--as-of", default=date.today().isoformat())
    p.add_argument("--top", type=int, default=200)
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "refi_watch"))
    a = p.parse_args(argv)
    conn = connect()
    companies = load_companies(conn, Resolver(conn))
    if a.cmd == "compare":
        print(render_compare(compare(companies, load_filed(conn))))
        return
    bt = backtest(companies)
    if a.cmd == "backtest":
        print(render_md([], bt, date.fromisoformat(a.as_of)))
        return
    rows, model = watchlist(companies, date.fromisoformat(a.as_of), a.top)
    paths = write(rows, bt, a.out, date.fromisoformat(a.as_of))
    print(f"{len(rows)} companies, base rate {model.base:.1%} -> {paths[0]}")


if __name__ == "__main__":
    main()
