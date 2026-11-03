"""BACKTEST: does each free dataset help a machine-learning Refinance watch? (experiment, not live)

Trains a gradient-boosted tree model (scikit-learn, free, runs locally) on company-dates whose 12-month
outcome had closed before the first test date, then scores later dates it never saw. The model is
retrained with one dataset group added at a time, so each group's gain is measured on the same rows:

  charges    the charge register only (what rw1 sees, in more detail)
  profile    Basic Company Data: accounts size, company type, registered-office postcode district,
             how many companies share that postcode, year-end month, dated name changes (radar.profile)
  accounts   bulk accounts balance-sheet facts known before the date (radar.accounts)
  owners     PSC owner-register changes known before the date (radar.ownership)
  directors  officer appointments and resignations (borrower_officers; partial coverage)

Every charge feature uses only charges FILED before the date (delivered_on), and satisfactions dated
on or before it. Results are reported for all companies and for "ordinary" companies (10 or fewer
charges on record by that date), since serial property borrowers are easy to predict and weak leads.
rw1 (the live points score) is scored on the same test rows for comparison.

    pip install scikit-learn numpy
    python -m radar.refi_ml                # needs profile/accounts/ownership fetched for those groups
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import date, timedelta

from . import refi_watch as rw

WINDOW = 365
TEST = [date(2025, 4, 1), date(2025, 6, 1), date(2025, 8, 1), date(2025, 10, 1)]
ORDINARY_MAX = 10
GROUPS = ("charges", "profile", "accounts", "owners", "directors")


def _train_cutoffs(first_test: date, years: float = 2.0) -> list[date]:
    end = first_test - timedelta(days=WINDOW)   # outcome window closed before the first test date
    out, on = [], end
    while on > end - timedelta(days=int(365.25 * years)):
        out.append(on); on -= timedelta(days=61)
    return sorted(out)


def _days(a, b):
    return (a - b).days if a and b else math.nan


class Feats:
    """Turns a company on a date into numeric and categorical features, one group at a time."""

    def __init__(self, companies, vol, profile, filed):
        self.vol, self.profile, self.filed = vol, profile, filed
        self.prep = {}
        for co in companies.values():
            cs = []
            for c in co.charges:
                f = rw._d(c.filed)
                if f:
                    cs.append((f, rw._start(c) or f, rw._d(c.satisfied), rw._lender(c), c))
            cs.sort(key=lambda t: t[0])
            self.prep[co.number] = cs
        self.cats: dict = {}

    def cat(self, name, v):
        d = self.cats.setdefault(name, {})
        return d.setdefault(v, len(d))

    def charges(self, co, on):
        cs = [t for t in self.prep[co.number] if t[0] < on]
        open_ = [t for t in cs if not (t[2] and t[2] <= on)]
        if not open_:
            return None
        sat = [t[2] for t in cs if t[2] and t[2] <= on]
        newest = max(open_, key=lambda t: t[1])
        key, cls = newest[3]
        v = self.vol.get(key) or {}
        def vol_months(a, b):
            tot = 0
            for k in range(a, b):
                y, m = on.year, on.month - 1 - k
                while m <= 0:
                    m += 12; y -= 1
                tot += v.get((y, m), 0) if v else 0
            return tot
        before = vol_months(6, 18) / 2
        # past refinances: a satisfaction with a new filing within a year, before this date
        past_refi = sum(1 for s in sat if any(s - timedelta(days=365) <= t[0] <= s + timedelta(days=365) and t[0] < on for t in cs))
        ages = [(on - t[1]).days for t in open_]
        return {
            "n_known": len(cs), "n_open": len(open_), "n_sat": len(sat), "past_refi": past_refi,
            "newest_age": (on - newest[1]).days, "oldest_age": max(ages), "mean_age": sum(ages) / len(ages),
            "recent_3m": sum(1 for t in cs if (on - t[0]).days <= 91),
            "recent_6m": sum(1 for t in cs if (on - t[0]).days <= 183),
            "recent_12m": sum(1 for t in cs if (on - t[0]).days <= 365),
            "sat_12m": sum(1 for s in sat if (on - s).days <= 365),
            "since_sat": (on - max(sat)).days if sat else math.nan,
            "since_filed": (on - cs[-1][0]).days,
            "n_lenders_open": len({t[3][0] for t in open_}),
            "lender_cls": self.cat("lender_cls", "bridging" if cls == "bridging" else cls),
            "pullback": (vol_months(0, 6) / before) if before >= 10 else math.nan,
            "lender_size": before,
            "co_age": _days(on, rw._d(co.created)),
            "sic2": self.cat("sic2", (co.sic_main or "")[:2]),
            "property": 1 if co.sector == "Property and real estate" else 0,
        }

    def profile_f(self, co, on):
        p = self.profile.get(co.number) or {}
        ch = [rw._d(x) for x in (p.get("name_changes") or "").split(";") if x]
        ch = [d for d in ch if d < on]
        pc = p.get("postcode_companies")
        return {
            "size": self.cat("size", p.get("account_category", "")),
            "category": self.cat("category", p.get("category", "")),
            "region": self.cat("region", AREA_REGION.get("".join(ch_ for ch_ in (p.get("postcode_district") or "")[:2] if ch_.isalpha()), "other")),
            "pc_share": float(pc) if pc not in (None, "") else math.nan,
            "ye_month": float(p["account_ref_month"]) if (p.get("account_ref_month") or "").isdigit() else math.nan,
            "name_changes": len(ch), "since_rename": (on - max(ch)).days if ch else math.nan,
        }

    def accounts_f(self, co, on):
        accs = [a for a in (self.filed.accounts.get(co.number) or []) if rw._d(a["known_from"]) < on]
        keys = ("loans_1y", "loans_after", "creditors_1y", "creditors_after", "net_current", "net_assets",
                "total_assets_less_cl", "cash", "investment_property", "fixed_assets")
        if not accs:
            return {**{f"a_{k}": math.nan for k in keys}, "a_age": math.nan, "a_short": math.nan, "a_c1_shift": math.nan,
                    "a_after_shift": math.nan, "a_n": 0}
        a = accs[-1]
        g = lambda k: a[k] if a.get(k) is not None else math.nan
        c1, tl = a.get("creditors_1y"), a.get("total_assets_less_cl")
        pc1, pa = a.get("prev_creditors_1y"), a.get("prev_creditors_after")
        return {**{f"a_{k}": g(k) for k in keys}, "a_age": (on - rw._d(a["balance_sheet_date"])).days,
                "a_short": (c1 / (c1 + tl)) if c1 is not None and tl is not None and c1 + tl > 0 else math.nan,
                "a_c1_shift": (c1 / pc1) if c1 is not None and pc1 else math.nan,
                "a_after_shift": ((a.get("creditors_after") or 0) / pa) if pa else math.nan, "a_n": len(accs)}

    def owners_f(self, co, on):
        lag, created = timedelta(days=rw.ownership_lag()), rw._d(co.created)
        recs = self.filed.owners.get(co.number) or []
        changes, active, corp = [], 0, 0
        for o in recs:
            n, c = rw._d(o.get("notified_on")), rw._d(o.get("ceased_on"))
            if n and n + lag < on and not (c and c + lag < on):
                active += 1; corp += o.get("kind", "").startswith("corporate")
            for d, founding in ((n, n and created and (n - created).days <= 60), (c, False)):
                if d and d + lag < on and d != date(2016, 4, 6) and not founding:
                    changes.append(d)
        return {"o_active": active, "o_corporate": corp, "o_changes": len(changes),
                "o_since": (on - max(changes)).days if changes else math.nan}

    def directors_f(self, co, on):
        if co.number not in self.filed.directors:
            return {"d_checked": 0, "d_active": math.nan, "d_since": math.nan}
        lag, created = timedelta(days=rw.ownership_lag()), rw._d(co.created)
        dates, active = [], 0
        for ap, rs in self.filed.directors[co.number]:
            a, r = rw._d(ap), rw._d(rs)
            if a and a + lag < on and not (r and r + lag < on):
                active += 1
            if a and not (created and (a - created).days <= 60):
                dates.append(a)
            if r:
                dates.append(r)
        dates = [d for d in dates if d + lag < on]
        return {"d_checked": 1, "d_active": active, "d_since": (on - max(dates)).days if dates else math.nan}


CATEGORICAL = {"lender_cls", "sic2", "size", "category", "region"}

# postcode area -> broad region (rough; areas straddling borders go to their main region)
REGIONS = {
    "London": "E EC N NW SE SW W WC BR CR DA EN HA IG KT RM SM TW UB",
    "South East": "BN CT GU ME MK OX PO RG RH SL SO TN HP SS CM CO LU SG AL",
    "South West": "BA BH BS DT EX GL PL SN SP TA TQ TR",
    "East of England": "CB IP NR PE",
    "Midlands": "B CV DE DY LE LN NG NN ST TF WR WS WV HR",
    "North West": "BB BL CA CH CW FY L LA M OL PR SK WA WN CH",
    "Yorkshire and North East": "BD DH DL DN HD HG HU HX LS NE S SR TS WF YO",
    "Wales": "CF LD LL NP SA SY",
    "Scotland": "AB DD DG EH FK G HS IV KA KW KY ML PA PH TD ZE",
    "Northern Ireland": "BT",
}
AREA_REGION = {a: r for r, areas in REGIONS.items() for a in areas.split()}


def build(companies, feats: Feats, cutoffs):
    rows = []
    for co in companies.values():
        for on in cutoffs:
            base = feats.charges(co, on)
            if base is None:
                continue
            groups = {"charges": base, "profile": feats.profile_f(co, on), "accounts": feats.accounts_f(co, on),
                      "owners": feats.owners_f(co, on), "directors": feats.directors_f(co, on)}
            rows.append((co, on, groups, rw.outcome(co, on, WINDOW)))
    return rows


def matrix(rows, groups):
    import numpy as np
    names = [k for g in groups for k in rows[0][2][g]]
    X = np.array([[r[2][g][k] for g in groups for k in r[2][g]] for r in rows], dtype=float)
    return X, names


def fit_predict(train, test, groups, seed=0):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    Xtr, names = matrix(train, groups)
    Xte, _ = matrix(test, groups)
    ytr = np.array([r[3] for r in train])
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40,
                                       l2_regularization=1.0, categorical_features=[n in CATEGORICAL for n in names],
                                       random_state=seed)
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


def tiers(scores, ys, qs=(0.01, 0.05, 0.10)):
    ranked = sorted(zip(scores, ys), key=lambda t: -t[0])
    out = {}
    for q in qs:
        k = max(1, int(len(ranked) * q))
        out[q] = (sum(y for _, y in ranked[:k]) / k, k)
    return out


def avg_precision(scores, ys):
    ranked = sorted(zip(scores, ys), key=lambda t: -t[0])
    hits, tot, pos = 0, 0.0, sum(ys)
    for i, (_, y) in enumerate(ranked, 1):
        if y:
            hits += 1; tot += hits / i
    return tot / pos if pos else 0.0


def boot_diff(a, b, ys, q=0.10, n=300, seed=1):
    """95% interval for (top-q hit rate of a) - (of b), resampling test rows."""
    rnd, idx, diffs = random.Random(seed), list(range(len(ys))), []
    for _ in range(n):
        s = [rnd.choice(idx) for _ in idx]
        ta = tiers([a[i] for i in s], [ys[i] for i in s], (q,))[q][0]
        tb = tiers([b[i] for i in s], [ys[i] for i in s], (q,))[q][0]
        diffs.append(ta - tb)
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def _split_profile(rows):
    """Profile as three sub-groups, so each can be tested on its own."""
    parts = {"size": ("size", "category"), "place": ("region", "pc_share"), "names": ("name_changes", "since_rename", "ye_month")}
    for _, _, g, _ in rows:
        p = g["profile"]
        for k, keys in parts.items():
            g[f"profile_{k}"] = {x: p[x] for x in keys}


VARIANTS = [("charges",), ("charges", "profile_size"), ("charges", "profile_place"), ("charges", "profile_names"),
            ("charges", "accounts"), ("charges", "owners"), ("charges", "directors"), GROUPS]


def run(companies, profile, filed, log=print):
    vol = rw.lender_volumes(companies)
    feats = Feats(companies, vol, profile, filed)
    train_dates = _train_cutoffs(TEST[0])
    train = build(companies, feats, train_dates)
    test = build(companies, feats, TEST)
    _split_profile(train); _split_profile(test)
    log(f"train {len(train):,} company-dates ({sum(r[3] for r in train):,} refinanced), test {len(test):,}")
    ys = [r[3] for r in test]
    ordinary = [i for i, r in enumerate(test) if r[2]["charges"]["n_known"] <= ORDINARY_MAX]
    ord_train = [r for r in train if r[2]["charges"]["n_known"] <= ORDINARY_MAX]
    ord_test = [test[i] for i in ordinary]
    # rw1 learned on the same closed-outcome dates (strict), and as it runs live (its own year, which
    # overlaps the test outcomes), both scored on the same test rows
    strict = rw.Model(rw.examples(companies, train_dates, vol, None, WINDOW))
    live = rw.Model(rw.examples(companies, rw._cutoffs(date(2025, 1, 1), years=1.0), vol, None, WINDOW))
    f_te = [rw.features(co, on, vol) for co, on, _, _ in test]
    res = {"all": {"rw1 as live (learning year overlaps test outcomes)": [live.score(x) for x in f_te],
                   "rw1, same learning dates as ML": [strict.score(x) for x in f_te]}}
    res["ordinary"] = {k: [v[i] for i in ordinary] for k, v in res["all"].items()}
    for v in VARIANTS:
        if "profile" in "".join(v) and not profile:
            continue
        name = "ML: " + (" + ".join(v) if v != GROUPS else "all groups")
        res["all"][name] = list(fit_predict(train, test, v if v != GROUPS else GROUPS))
        res["ordinary"][name] = list(fit_predict(ord_train, ord_test, v))   # trained on ordinary companies only
        log(f"  fitted {name}")
    return ys, ordinary, res


def render(ys, ordinary, res) -> str:
    out = ["BACKTEST: machine-learning Refinance watch, one free dataset group at a time (12-month window)",
           f"Test dates {', '.join(d.isoformat() for d in TEST)}. ML models learned only on dates whose outcome had "
           "closed before the first test date; the ordinary-company models learned on ordinary companies only.", ""]
    for key, label, idx in (("all", "All companies", list(range(len(ys)))),
                            ("ordinary", f"Ordinary companies (<= {ORDINARY_MAX} charges on record)", ordinary)):
        y = [ys[i] for i in idx]
        ref = res[key]["ML: charges"]
        out += [f"## {label}: {len(idx):,} company-dates, base rate {sum(y) / len(y):.1%}", "",
                "| Model | Top 1% | Top 5% | Top 10% | Avg precision | Top 10% vs ML charges (95% interval) |",
                "|---|---:|---:|---:|---:|---|"]
        for name, ss in res[key].items():
            t = tiers(ss, y)
            ci = "" if name == "ML: charges" else "{:+.1%} to {:+.1%}".format(*boot_diff(ss, ref, y))
            out.append(f"| {name} | {t[0.01][0]:.1%} | {t[0.05][0]:.1%} | {t[0.10][0]:.1%} | {avg_precision(ss, y):.3f} | {ci} |")
        out.append("")
    return "\n".join(out)


def main(argv=None):
    from . import profile as prof
    from .aliases import Resolver
    from .db import connect
    argparse.ArgumentParser(prog="python -m radar.refi_ml").parse_args(argv)
    conn = connect()
    companies = rw.load_companies(conn, Resolver(conn))
    res = run(companies, prof.load(), rw.load_filed(conn), log=lambda m: print(m, flush=True))
    print(render(*res))


if __name__ == "__main__":
    main()
