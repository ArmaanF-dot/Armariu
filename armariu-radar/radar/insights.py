"""Market insights for the customer portal: trends, sector cuts, league movement and the shape of
the Refinance watch. Company-level facts only, computed from the same loaded charges as the rest
of the snapshot (individuals are already masked there and are left out of every count by lender).

Every weekly series carries `observed_only` for weeks before the radar started watching the live
stream: those weeks only include borrowers that later filed a charge, so they undercount.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from .lender_view import _count, week_of

AGE_BANDS = [("Under 2 years", 0, 2), ("2 to 5 years", 2, 5), ("5 to 10 years", 5, 10),
             ("10 to 20 years", 10, 20), ("20 years or more", 20, 1000)]
# Lender classes grouped for sector and age cuts (fewer parts read better in a stacked bar).
GROUPS = {"direct_lender": "private_credit", "trustee_agent": "private_credit", "bank": "bank",
          "property": "property", "abl_invoice": "specialist", "asset_finance": "specialist",
          "government_backed": "specialist", "other": "other", "individual": "other"}
GROUP_LABELS = {"private_credit": "Private credit and agents", "bank": "Banks", "property": "Property lenders",
                "specialist": "ABL, asset finance and public", "other": "Other"}
HIGH_ODDS = 0.40
ODDS_BUCKETS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 1.01]


def primary_class(charge) -> str:
    """One class per charge, the same rule as the snapshot's weekly series."""
    cls = sorted({cl for _, _, cl in charge.lenders}, key=lambda x: (x == "trustee_agent", x))
    return cls[0] if cls else "other"


def _weeks(last_week: date, n: int) -> list[date]:
    return [last_week - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def build_insights(companies: dict, sigs: list[dict], refi_rows: list[dict], today: date, cov_d: date,
                   weeks: int = 13) -> dict:
    last_week = week_of(today - timedelta(days=7))[0]
    wk = _weeks(last_week, weeks)
    first, end = wk[0].isoformat(), (last_week + timedelta(days=6)).isoformat()
    by_charge = {(c.company_number, c.charge_id): c for co in companies.values() for c in co.charges}

    # 1. New charges and satisfactions each week; all-asset debentures as a share of new charges.
    flow = {w.isoformat(): {"week_start": w.isoformat(), "observed_only": w < cov_d, "new": 0, "satisfied": 0,
                            "register": 0, "all_assets": 0, "private_credit": 0} for w in wk}
    sector_cls: dict[str, Counter] = {}
    age_cls: dict[str, Counter] = {label: Counter() for label, _, _ in AGE_BANDS}
    class_debenture: dict[str, list[int]] = {}
    for co in companies.values():
        for c in co.charges:
            if c.filed and first <= c.filed <= end:
                k = week_of(date.fromisoformat(c.filed))[0].isoformat()
                cls = primary_class(c)
                if k in flow:
                    flow[k]["new"] += 1
                    if c.on_register:   # older charges come from the history API, which has no charge details
                        flow[k]["register"] += 1
                        flow[k]["all_assets"] += int(bool(c.floating_all))
                    flow[k]["private_credit"] += int(any(cl in ("direct_lender", "trustee_agent") for _, _, cl in c.lenders))
                g = GROUPS.get(cls, "other")
                sector_cls.setdefault(co.sector, Counter())[g] += 1
                band = _age_band(co.created, c.filed)
                if band:
                    age_cls[band][g] += 1
                if c.on_register:
                    d = class_debenture.setdefault(cls, [0, 0])
                    d[0] += 1
                    d[1] += int(bool(c.floating_all))
            if c.satisfied and first <= c.satisfied[:10] <= end:
                k = week_of(date.fromisoformat(c.satisfied[:10]))[0].isoformat()
                if k in flow:
                    flow[k]["satisfied"] += 1

    # 2. Signals each week, by type.
    sig_weeks = {w.isoformat(): {"week_start": w.isoformat(), "observed_only": w < cov_d,
                                 "refinancing": 0, "first_facility": 0, "add_on": 0} for w in wk}
    # 3. Refinancing between lender types (13 weeks): who replaced whom, by class.
    moves = Counter()
    for s in sigs:
        d = s.get("signal_date") or ""
        if not (first <= d <= end):
            continue
        row = sig_weeks.get(week_of(date.fromisoformat(d))[0].isoformat())
        if row is not None and s["signal_type"] in row:
            row[s["signal_type"]] += 1
        if s["signal_type"] == "refinancing" and s.get("other_charge_id"):
            new = by_charge.get((s["company_number"], s["charge_id"]))
            old = by_charge.get((s["company_number"], s["other_charge_id"]))
            if new and old:
                moves[(GROUPS.get(primary_class(old), "other"), GROUPS.get(primary_class(new), "other"))] += 1

    # 4. League movement: rank in the last 4 weeks vs the 4 weeks before, non-property lenders.
    now, _ = _count(companies, last_week - timedelta(days=21), last_week + timedelta(days=6))
    before, _ = _count(companies, last_week - timedelta(days=49), last_week - timedelta(days=22))
    skip = ("property", "individual")
    rank_before = {k: i + 1 for i, (k, _) in enumerate(sorted(
        ((k, d) for k, d in before.items() if d["cls"] not in skip), key=lambda kv: (-len(kv[1]["n"]), kv[0])))}
    ranked = sorted(((k, d) for k, d in now.items() if d["cls"] not in skip), key=lambda kv: (-len(kv[1]["n"]), kv[0]))
    league_moves = [{"lender": d["lender"], "class": d["cls"], "rank": i + 1, "charges_4w": len(d["n"]),
                     "prior_rank": rank_before.get(k), "prior_4w": len(before.get(k, {}).get("n", ()))}
                    for i, (k, d) in enumerate(ranked[:15])]
    prior_observed = last_week - timedelta(days=49) < cov_d

    # 5. Private credit concentration: share of private credit charges held by the top lenders (13 weeks).
    pc13, _ = _count(companies, wk[0], last_week + timedelta(days=6))
    pc = sorted((len(d["n"]) for d in pc13.values() if d["cls"] == "direct_lender"), reverse=True)
    concentration = {"lenders": len(pc), "charges": sum(pc),
                     "top5": sum(pc[:5]), "top10": sum(pc[:10]), "top25": sum(pc[:25])}

    # 6. Refinance watch: how the odds spread across every company scored.
    hist = [0] * (len(ODDS_BUCKETS) - 1)
    sec = {}
    for r in refi_rows:
        o = r["odds_12m"]
        for i in range(len(hist)):
            if ODDS_BUCKETS[i] <= o < ODDS_BUCKETS[i + 1]:
                hist[i] += 1
                break
        s = sec.setdefault(r["sector"], {"sector": r["sector"], "companies": 0, "high": 0, "odds_sum": 0.0})
        s["companies"] += 1
        s["odds_sum"] += o
        s["high"] += int(o >= HIGH_ODDS)
    watch_sectors = sorted(({"sector": s["sector"], "companies": s["companies"], "high": s["high"],
                             "mean_odds": round(s["odds_sum"] / s["companies"], 4)}
                            for s in sec.values() if s["companies"] >= 30), key=lambda s: -s["mean_odds"])

    base = refi_rows[0]["base_rate"] if refi_rows else 0
    sectors_out = sorted(({"sector": k, "total": sum(v.values()), **{g: v.get(g, 0) for g in GROUP_LABELS}}
                          for k, v in sector_cls.items() if k != "Sector not known yet"), key=lambda r: -r["total"])
    return {
        "window": {"start": first, "end": end, "weeks": weeks, "coverage_start": cov_d.isoformat()},
        "group_labels": GROUP_LABELS,
        "flow_weeks": list(flow.values()),
        "signal_weeks": list(sig_weeks.values()),
        "refi_moves": [{"from": a, "to": b, "signals": n} for (a, b), n in sorted(moves.items(), key=lambda kv: -kv[1])],
        "league_moves": league_moves, "league_moves_prior_partial": prior_observed,
        "concentration": concentration,
        "sectors": sectors_out[:12],
        "borrower_age": [{"band": label, "total": sum(age_cls[label].values()),
                          **{g: age_cls[label].get(g, 0) for g in GROUP_LABELS}} for label, _, _ in AGE_BANDS],
        "debentures": sorted(({"class": k, "charges": v[0], "all_assets": v[1]} for k, v in class_debenture.items()
                              if k != "individual" and v[0] >= 20), key=lambda r: -r["all_assets"] / r["charges"]),
        # relative scores only: the model's absolute odds come from the radar's own data and run about 3x high
        "watch": {"scored": len(refi_rows),
                  "sectors": [{"sector": x["sector"], "companies": x["companies"],
                               "mean_lift": round(x["mean_odds"] / base, 2) if base else None} for x in watch_sectors[:12]]},
    }


def _age_band(created: str | None, on: str) -> str | None:
    try:
        years = (date.fromisoformat(on[:10]) - date.fromisoformat(created[:10])).days / 365.25
    except (TypeError, ValueError):
        return None
    for label, lo, hi in AGE_BANDS:
        if lo <= years < hi:
            return label
    return None
