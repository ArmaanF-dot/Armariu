"""Colombia extra insight: peer benchmarks, change alerts, group links, portfolio stress. Held-out second-stage tests on top of the distress score."""
import json, re, sys, time, urllib.request, urllib.parse, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
log = lambda *a: print(*a, flush=True)
def get(u, t=600):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as r: return r.read()
        except Exception as e: log("retry", repr(e)[:80]); time.sleep(5 * (i + 1))
    raise RuntimeError(u)
def soda(q): return json.loads(get("https://www.datos.gov.co/resource/6hqw-m3dm.json?" + urllib.parse.urlencode(q)))
# ---- 1. caratula: sector, city, parent for all filers
def fetch(fc):
    rows = []; off = 0
    w = (f"fecha_corte='{fc}T00:00:00.000' AND (concepto like 'Clasificaci%n Industrial Internacional Uniforme Versi%n 4 A.C%' OR concepto like 'Ciudad de la direcci%n del domicilio' OR concepto like 'Nombre o raz%n social del controlante%' OR concepto like 'Pa%s del domicilio del controlante%')")
    while True:
        r = soda({"$select": "nit,concepto,valor", "$where": w, "$limit": 50000, "$offset": off, "$order": "nit,concepto"}); rows += r; off += 50000
        log(fc, len(rows))
        if len(r) < 50000: break
    d = pd.DataFrame(rows)
    d["k"] = d["concepto"].map(lambda s: "ciiu" if "A.C" in s else "city" if s.startswith("Ciudad") else "parent" if s.startswith("Nombre o raz") else "parent_country")
    return d.pivot_table(index="nit", columns="k", values="valor", aggfunc="first").reset_index()
ctx = pd.concat([fetch("2025-12-31"), fetch("2024-12-31")]).drop_duplicates("nit", keep="first")
ctx["nit"] = ctx["nit"].astype(str)
ctx["section"] = ctx["ciiu"].str.extract(r"^([A-Z])")[0]; ctx["division"] = ctx["ciiu"].str.extract(r"^[A-Z](\d\d)")[0]
ctx.to_csv(f"{OUT}/co_context_all.csv.gz", index=False)
log("context", ctx.shape, "with sector", ctx.section.notna().mean())
# ---- 2. join to features and scored
F = pd.read_csv(f"{OUT}/co_features.csv.gz", usecols=["nit", "year", "lev", "cur", "roa", "opm", "cash_cl", "cl_share", "usd_assets_m"])
S = pd.read_csv(f"{OUT}/co_backtest_scored.csv.gz")
for d in (F, S, ctx): d["nit"] = d["nit"].astype("int64").astype(str) if d is not ctx else d["nit"]
S = S.merge(F.drop(columns="usd_assets_m"), on=["nit", "year"], how="left").merge(ctx[["nit", "section", "division", "parent", "parent_country", "city"]], on="nit", how="left")
S["lp"] = np.log(S.p_ens.clip(1e-5, 1 - 1e-5) / (1 - S.p_ens.clip(1e-5, 1 - 1e-5)))
S = S.sort_values(["nit", "year"])
# ---- 3. peer-relative features (percentile of firm within sector-year)
RAT = ["lev", "cur", "roa", "opm", "cash_cl", "cl_share"]
def pct(df, key):
    return df.groupby(["year", key])[RAT].rank(pct=True)
P_div = pct(S, "division"); P_sec = pct(S, "section")
n_div = S.groupby(["year", "division"])["nit"].transform("count")
for r in RAT:
    S["pr_" + r] = np.where(n_div >= 25, P_div[r], P_sec[r])
    S["pr_" + r] = S["pr_" + r].fillna(0.5)
# sector failure rate using ONLY earlier test years, shrunk to overall
def sector_rate(S):
    out = pd.Series(np.nan, index=S.index)
    for y in sorted(S.year.unique()):
        past = S[S.year < y]
        if past.empty: continue
        base = past.y.mean(); g = past.groupby("section").y.agg(["sum", "count"])
        rate = (g["sum"] + 200 * base) / (g["count"] + 200)
        m = S.year == y
        out[m] = S.loc[m, "section"].map(rate).fillna(base).values
    return out
S["sec_rate"] = sector_rate(S); S["lsec"] = np.log(S.sec_rate.clip(1e-5))
# ---- 4. change alerts: jump in log-odds vs prior year
S["prev_lp"] = S.groupby("nit")["lp"].shift(1); S["prev_year"] = S.groupby("nit")["year"].shift(1)
S["jump"] = np.where(S.prev_year == S.year - 1, S.lp - S.prev_lp, 0.0); S["jump"] = S["jump"].fillna(0).clip(-4, 4)
S["jump_pos"] = S.jump.clip(lower=0)
# ---- 5. group links: has parent, foreign parent, parent-mates flagged high risk last year
S["has_parent"] = S.parent.notna() & (S.parent.astype(str).str.len() > 3)
S["foreign_parent"] = S.parent_country.fillna("").str.upper().map(lambda s: int(len(s) > 2 and "COLOMBIA" not in s))
S["high"] = (S.groupby("year").p_ens.rank(pct=True) >= 0.9).astype(int)
tmp = S.loc[S.has_parent, ["year", "parent", "nit", "high", "y"]]
g = tmp.groupby(["parent", "year"]).agg(n=("nit", "count"), hi=("high", "sum"), ev=("y", "sum")).reset_index()
g["year"] += 1; g = g.rename(columns={"n": "mates_prev", "hi": "mates_hi_prev", "ev": "mates_ev_prev"})
S = S.merge(g, on=["parent", "year"], how="left")
for c in ["mates_prev", "mates_hi_prev", "mates_ev_prev"]: S[c] = S[c].fillna(0)
S["mates_other_hi"] = np.where(S.has_parent, np.maximum(S.mates_hi_prev - S.high.shift(0) * 0, 0), 0)  # includes self-year; shift handled by prior-year grouping
S["mates_hi_share"] = np.where(S.mates_prev > 1, S.mates_hi_prev / S.mates_prev.clip(1), 0)
log("parent coverage", S.has_parent.mean(), "firms with mates", (S.mates_prev > 1).mean())
# ---- 6. held-out second-stage tests (train on earlier test years only)
def run(feats, name, years=(2021, 2022, 2023, 2024)):
    res = {}; allp = []
    for T in years:
        tr = S[(S.year < T)].dropna(subset=feats); te = S[S.year == T].dropna(subset=feats)
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        m = LogisticRegression(C=1.0, max_iter=500).fit((tr[feats] - mu) / sd, tr.y)
        p = m.predict_proba((te[feats] - mu) / sd)[:, 1]
        res[T] = round(roc_auc_score(te.y, p), 4); allp.append(pd.DataFrame({"y": te.y.values, "p": p, "year": T}))
    a = pd.concat(allp); res["pooled"] = round(roc_auc_score(a.y, a.p), 4)
    top = a[a.p >= a.groupby("year").p.transform(lambda s: s.quantile(.9))]
    res["top10_rate"] = round(top.y.mean() / a.y.mean(), 2)
    log(name, res); return res
R = {}
R["base"] = run(["lp"], "base")
R["peer"] = run(["lp"] + ["pr_" + r for r in RAT], "peer")
R["sector_rate"] = run(["lp", "lsec"], "sector_rate")
R["peer+sector"] = run(["lp", "lsec"] + ["pr_" + r for r in RAT], "peer+sector")
R["jump"] = run(["lp", "jump", "jump_pos"], "jump")
R["group"] = run(["lp", "has_parent", "foreign_parent", "mates_hi_share"], "group")
R["all"] = run(["lp", "lsec", "jump", "jump_pos", "has_parent", "foreign_parent", "mates_hi_share"] + ["pr_" + r for r in RAT], "all")
# sector view
sv = S.groupby("section").agg(firm_years=("y", "count"), events=("y", "sum"), rate=("y", "mean"), avg_lev=("lev", "median"), avg_cur=("cur", "median"), avg_roa=("roa", "median")).reset_index()
sv["lift_vs_all"] = sv.rate / S.y.mean(); sv.to_csv(f"{OUT}/co_sector_view.csv", index=False)
yv = S.groupby(["section", "year"]).y.mean().unstack().round(4); yv.to_csv(f"{OUT}/co_sector_by_year.csv")
# do big score jumps precede failures? top 5% jumpers
jj = S[S.year >= 2020]; thr = jj.jump.quantile(.95)
R["jump_top5_rate_vs_all"] = round(jj[jj.jump >= thr].y.mean() / jj.y.mean(), 2)
R["jump_top5_rate_within_same_score_band"] = None
band = pd.qcut(jj.lp.rank(method="first"), 10, labels=False)
rows = []
for b in range(10):
    x = jj[band == b]; hi = x[x.jump >= x.jump.quantile(.8)]; lo = x[x.jump < x.jump.quantile(.8)]
    rows.append((b + 1, round(hi.y.mean(), 4), round(lo.y.mean(), 4)))
R["jump_by_score_decile(decile,top20%jump rate,rest rate)"] = rows
log(rows)
R["parent_stats"] = {"share_with_parent": round(float(S.has_parent.mean()), 3), "share_with_mates": round(float((S.mates_prev > 1).mean()), 3),
                     "event_rate_has_parent": round(float(S[S.has_parent].y.mean()), 4), "event_rate_no_parent": round(float(S[~S.has_parent].y.mean()), 4),
                     "event_rate_foreign_parent": round(float(S[S.foreign_parent == 1].y.mean()), 4)}
# ---- 7. lead list v3: peer columns + change flag for the latest scored year in watchlist2 / borrowers2
L = F[F.year == F.year.max()].copy(); L["nit"] = L["nit"].astype("int64").astype(str)
Lg = L.merge(ctx[["nit", "section", "division", "parent"]], on="nit", how="left")
for r in RAT: Lg["peer_" + r] = Lg.groupby("division")[r].transform("median")
Lg = Lg[["nit", "division", "parent"] + ["peer_" + r for r in RAT]].drop_duplicates("nit")
for f in ["co_watchlist2", "co_borrower_candidates2"]:
    d = pd.read_csv(f"{OUT}/{f}.csv", dtype={"nit": str}).merge(Lg, on="nit", how="left")
    d["vs_peers"] = np.select([d.lev > d.peer_lev * 1.25, d.lev < d.peer_lev * 0.75], ["more indebted than sector peers", "less indebted than sector peers"], "in line with sector peers")
    d.to_csv(f"{OUT}/{f.replace('2','3')}.csv", index=False)
# ---- 8. portfolio stress tool: expected failures from calibrated deciles (pooled backtest)
dec = pd.read_csv(f"{OUT}/co_odds_deciles_pooled.csv")
b = pd.read_csv(f"{OUT}/co_borrower_candidates3.csv", dtype={"nit": str}).head(100)
b["decile"] = pd.cut(b.risk_pct_rank, [-1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 101], labels=range(1, 11)).astype(int)
b["p12m"] = b.decile.map(dict(zip(dec.dec, dec.rate)))
b["section"] = b.ciiu.astype(str).str[0]
R["demo_portfolio_100_borrower_candidates"] = {"expected_failures_12m": round(float(b.p12m.sum()), 2), "share": round(float(b.p12m.mean()), 4),
    "by_section": b.groupby("section").p12m.agg(["count", "sum"]).round(3).reset_index().to_dict("records")}
json.dump(R, open(f"{OUT}/co_insight.json", "w"), indent=1, default=str)
log("done")
