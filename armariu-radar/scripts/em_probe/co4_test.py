"""RUES establishment/branch cancellations as a predictor of insolvency, on top of the distress score (point-in-time reconstruction from registration/cancellation dates)."""
import json, re, sys, time, urllib.request, urllib.parse, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
log = lambda *a: print(*a, flush=True)
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=900) as r: return r.read().decode("utf8", "replace")
        except Exception as e: log("retry", u[:120], repr(e)[:100]); time.sleep(8 * (i + 1))
    return "[]"
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)))
def allpages(ds, q, page=50000, maxp=60):
    rows = []
    for p in range(maxp):
        r = soda(ds, dict(q, **{"$limit": page, "$offset": p * page})); rows += r
        if len(r) < page: break
    return pd.DataFrame(rows)
def clean_nit(s):
    s = re.sub(r"\D", "", str(s)).lstrip("0")
    return s[:9] if len(s) == 10 and s[0] in "89" else s
ES = "nb3d-v3n7"
S0 = pd.read_csv(f"{OUT}/co_backtest_scored.csv.gz"); S0["nit"] = S0["nit"].astype("int64").astype(str)
nits = sorted(set(S0.nit)); rows = []
for i in range(0, len(nits), 150):
    lst = ",".join("'" + n + "'" for n in nits[i:i + 150])
    r = allpages(ES, {"$select": "nit_propietario,fecha_matricula,fecha_cancelacion,estado_matricula,categoria_matricula,matricula,codigo_camara", "$where": f"nit_propietario in ({lst})", "$order": "codigo_camara,matricula"})
    rows.append(r)
    if (i // 150) % 20 == 0: log("chunk", i, len(r))
raw = pd.concat(rows, ignore_index=True); log("establishment rows", raw.shape, "owners", raw.nit_propietario.nunique())
raw["nit"] = raw.nit_propietario.map(clean_nit)
def d(c): return pd.to_datetime(raw[c], format="%Y%m%d", errors="coerce")
raw["m"] = d("fecha_matricula"); raw["c"] = d("fecha_cancelacion")
raw.loc[raw.c.dt.year > 2026, "c"] = pd.NaT; raw.loc[raw.m.dt.year > 2026, "m"] = pd.NaT
log("estado", raw.estado_matricula.value_counts().head(6).to_dict(), "categoria", raw.categoria_matricula.value_counts().head(5).to_dict())
log("cancelled with missing date", int(((raw.estado_matricula.str.upper() == "CANCELADA") & raw.c.isna()).sum()), "missing matricula", int(raw.m.isna().sum()))
raw = raw[raw.m.notna()]
raw.to_csv(f"{OUT}/co_establishments_raw.csv.gz", index=False)
recs = []
for Y in range(2016, 2025):
    c0, c1 = pd.Timestamp(Y, 1, 1), pd.Timestamp(Y, 12, 31)
    act_end = raw[(raw.m <= c1) & (raw.c.isna() | (raw.c > c1))]; act_start = raw[(raw.m < c0) & (raw.c.isna() | (raw.c >= c0))]
    canc = raw[raw.c.between(c0, c1)]; opn = raw[raw.m.between(c0, c1)]
    d_ = pd.DataFrame({"n_end": act_end.groupby("nit").size(), "n_start": act_start.groupby("nit").size(), "n_canc": canc.groupby("nit").size(), "n_open": opn.groupby("nit").size()}).fillna(0).reset_index()
    d_["year"] = Y; recs.append(d_)
P = pd.concat(recs); log("panel", P.shape)
S = S0.merge(P, on=["nit", "year"], how="left")
for c in ["n_end", "n_start", "n_canc", "n_open"]: S[c] = S[c].fillna(0)
S["has_est"] = ((S.n_end + S.n_start + S.n_canc) > 0).astype(int)
S["log_end"] = np.log1p(S.n_end); S["canc_share"] = np.where(S.n_start > 0, S.n_canc / S.n_start.clip(1), 0).clip(0, 1)
S["net"] = ((S.n_open - S.n_canc) / S.n_start.clip(1)).clip(-1, 3); S["any_canc"] = (S.n_canc > 0).astype(int); S["multi"] = (S.n_start >= 3).astype(int)
S["lp"] = np.log(S.p_ens.clip(1e-5, 1 - 1e-5) / (1 - S.p_ens.clip(1e-5, 1 - 1e-5)))
log("coverage firm-years", round(S.has_est.mean(), 4), "events", round(S[S.y == 1].has_est.mean(), 4), "multi-site share", round(S.multi.mean(), 4))
log(S.groupby("year").agg(n=("y", "size"), cov=("has_est", "mean"), ev=("y", "sum")).round(4).to_string())
def run(feats, name, mask=None, years=(2021, 2022, 2023, 2024)):
    D = S if mask is None else S[mask]; res = {}; allp = []
    for T in years:
        tr = D[D.year < T]; te = D[D.year == T]
        if te.y.sum() < 3 or tr.y.sum() < 5: continue
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        m = LogisticRegression(max_iter=500).fit((tr[feats] - mu) / sd, tr.y)
        p = m.predict_proba((te[feats] - mu) / sd)[:, 1]
        res[T] = round(roc_auc_score(te.y, p), 4); allp.append(pd.DataFrame({"y": te.y.values, "p": p, "year": T}))
    a = pd.concat(allp); res["pooled"] = round(roc_auc_score(a.y, a.p), 4)
    top = a[a.p >= a.groupby("year").p.transform(lambda s: s.quantile(.9))]
    res["top10x"] = round(top.y.mean() / a.y.mean(), 2); res["n"] = len(a); res["events"] = int(a.y.sum()); log(name, res); return res
F = ["log_end", "canc_share", "net", "any_canc", "has_est"]
R_ = {"all|base": run(["lp"], "all base"), "all|+est": run(["lp"] + F, "all +est")}
R_["covered|base"] = run(["lp"], "covered base", S.has_est == 1); R_["covered|+est"] = run(["lp"] + F[:-1], "covered +est", S.has_est == 1)
R_["multi|base"] = run(["lp"], "multi base", S.multi == 1); R_["multi|+est"] = run(["lp"] + F[:-1], "multi +est", S.multi == 1)
c = S[(S.year >= 2019) & (S.multi == 1)].copy(); c["b"] = pd.cut(c.canc_share, [-0.01, 0, 0.1, 0.25, 0.5, 1.01], labels=["none", "<=10%", "10-25%", "25-50%", ">50%"])
R_["multi_rate_by_cancel_share"] = c.groupby("b").y.agg(["mean", "count", "sum"]).round(4).reset_index().astype({"b": str}).to_dict("records")
c["q"] = pd.qcut(c.lp.rank(method="first"), 5, labels=False)
R_["multi_cancel>25%_within_score_quintile"] = [(int(q), round(float(x[x.canc_share > .25].y.mean()), 4), int((x.canc_share > .25).sum()), round(float(x[x.canc_share <= .25].y.mean()), 4)) for q, x in c.groupby("q")]
R_["base_rate_multi"] = round(float(c.y.mean()), 4)
log(json.dumps(R_, indent=1, default=str)); json.dump(R_, open(f"{OUT}/co4_results.json", "w"), indent=1, default=str)
