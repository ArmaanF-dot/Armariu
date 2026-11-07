"""Test SECOP II payment-plan delays and RUES age/type as add-ons to the Colombia distress score (point in time, held out)."""
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
        except Exception as e: log("retry", u[:160], repr(e)[:100]); time.sleep(8 * (i + 1))
    return "[]"
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)))
def allpages(ds, q, page=50000, maxp=200):
    rows = []
    for p in range(maxp):
        r = soda(ds, dict(q, **{"$limit": page, "$offset": p * page}))
        rows += r
        if len(r) < page: break
    return pd.DataFrame(rows)
def clean_nit(s):
    s = re.sub(r"\D", "", str(s)).lstrip("0")
    return s[:9] if len(s) == 10 and s[0] in "89" else s
PP = "uymx-8p3j"
#log("estado values", soda(PP, {"$select": "estado, count(*) as n", "$group": "estado", "$order": "n desc", "$limit": 20}))
#log("share with real pay date and due date among paid", soda(PP, {"$select": "count(*) as n", "$where": "estado='Pagado' AND fecha_real_de_pago IS NOT NULL AND fecha_de_vencimiento IS NOT NULL"}))
NITLIKE = "(documento_proveedor like '8%' OR documento_proveedor like '9%')"
DL = "date_diff_d(fecha_real_de_pago, fecha_de_vencimiento)"
recs = []
for Y in range(2020, 2025):
    t = time.time()
    # A: invoices paid during year Y (known by 31 Dec Y)
    A = allpages(PP, {"$select": f"documento_proveedor as nit, count(*) as paid_n, sum({DL}) as sum_delay, sum(case({DL}>30,1,true,0)) as late30, sum(valor_total_de_la_factura) as paid_v",
        "$where": f"{NITLIKE} AND fecha_real_de_pago between '{Y}-01-01T00:00:00' and '{Y}-12-31T23:59:59' AND fecha_de_vencimiento between '2016-01-01T00:00:00' AND '{Y}-12-31T23:59:59' AND {DL} between -365 and 1500",
        "$group": "documento_proveedor", "$order": "documento_proveedor"})
    # B: invoices overdue (>30d past due) and still unpaid at 31 Dec Y (point in time)
    B = allpages(PP, {"$select": "documento_proveedor as nit, count(*) as od_n, sum(valor_total_de_la_factura) as od_v",
        "$where": f"{NITLIKE} AND fecha_de_vencimiento between '2016-01-01T00:00:00' AND '{Y}-12-01T00:00:00' AND (fecha_real_de_pago > '{Y}-12-31T23:59:59' OR (fecha_real_de_pago IS NULL AND estado != 'Pagado'))",
        "$group": "documento_proveedor", "$order": "documento_proveedor"})
    # C: invoiced during Y
    C = allpages(PP, {"$select": "documento_proveedor as nit, count(*) as inv_n, sum(valor_total_de_la_factura) as inv_v",
        "$where": f"{NITLIKE} AND fecha_de_emision between '{Y}-01-01T00:00:00' and '{Y}-12-31T23:59:59'",
        "$group": "documento_proveedor", "$order": "documento_proveedor"})
    log(Y, "A", len(A), "B", len(B), "C", len(C), round(time.time() - t))
    d = None
    for x in (A, B, C):
        if len(x):
            x["nit"] = x["nit"].map(clean_nit)
            x = x.groupby("nit").sum(numeric_only=False) if False else x
            for c in x.columns:
                if c != "nit": x[c] = pd.to_numeric(x[c], errors="coerce")
            x = x.groupby("nit").sum().reset_index()
            d = x if d is None else d.merge(x, on="nit", how="outer")
    d["year"] = Y; recs.append(d)
P = pd.concat(recs).fillna(0); P.to_csv(f"{OUT}/co_secop_pay_by_nit_year.csv.gz", index=False)
log("payment panel", P.shape)
# RUES age / type
try:
    raise RuntimeError("skipped in lite run")
    R = allpages("c82u-588k", {"$select": "nit, min(fecha_matricula) as fm, max(tipo_sociedad) as ts, max(organizacion_juridica) as oj",
         "$where": "(nit like '8%' OR nit like '9%')", "$group": "nit", "$order": "nit"})
    R["nit"] = R["nit"].map(clean_nit); R["fm"] = pd.to_datetime(R["fm"], format="%Y%m%d", errors="coerce")
    R = R.groupby("nit").agg(fm=("fm", "min"), ts=("ts", "first"), oj=("oj", "first")).reset_index(); R.to_csv(f"{OUT}/co_rues_age.csv.gz", index=False); log("rues", R.shape)
except Exception as e:
    log("rues fail", repr(e)[:200]); R = pd.DataFrame({"nit": [], "fm": [], "ts": [], "oj": []})
# join to scored
S = pd.read_csv(f"{OUT}/co_backtest_scored.csv.gz"); S["nit"] = S["nit"].astype("int64").astype(str)
S = S.merge(P, on=["nit", "year"], how="left").merge(R, on="nit", how="left")
has = S.inv_n.fillna(0) + S.paid_n.fillna(0) + S.od_n.fillna(0) > 0
log("coverage: firm-years with any payment-plan record", round(has.mean(), 4), "events covered", round(S[S.y == 1].pipe(lambda d: (d.inv_n.fillna(0) + d.paid_n.fillna(0) + d.od_n.fillna(0) > 0).mean()), 4), "n", int(has.sum()))
S["lp"] = np.log(S.p_ens.clip(1e-5, 1 - 1e-5) / (1 - S.p_ens.clip(1e-5, 1 - 1e-5)))
for c in ["paid_n", "sum_delay", "late30", "paid_v", "od_n", "od_v", "inv_n", "inv_v"]: S[c] = S[c].fillna(0)
S["has_pay"] = has.astype(int)
S["mean_delay"] = np.where(S.paid_n > 0, S.sum_delay / S.paid_n.clip(1), 0).clip(-60, 365)
S["late_share"] = np.where(S.paid_n > 0, S.late30 / S.paid_n.clip(1), 0)
S["od_share"] = np.where((S.od_n + S.paid_n) > 0, S.od_n / (S.od_n + S.paid_n).clip(1), 0)
S["log_od_v"] = np.log1p(S.od_v / 4e6)  # USD
S["log_inv_v"] = np.log1p(S.inv_v / 4e6)
S["gov_rev_ratio"] = (S.inv_v / 4e6) / (S.usd_assets_m * 1e6).clip(1)
S["lgr"] = np.log1p(S.gov_rev_ratio.clip(0, 20))
S["age"] = ((pd.to_datetime(S.year.astype(str) + "-12-31") - S.fm).dt.days / 365).clip(0, 80); S["age"] = S.age.fillna(S.age.median()); S["young"] = (S.age < 5).astype(int)
S["is_sas"] = S.ts.fillna("").str.contains("SIMPLIFICADA|SAS", case=False).astype(int)
# sanity on leakage: payments flag by year
log(S.groupby("year").agg(n=("y", "size"), cov=("has_pay", "mean"), ev=("y", "sum")).round(4).to_string())
def run(feats, name, mask=None, years=(2022, 2023, 2024)):
    res = {}; allp = []
    D = S if mask is None else S[mask]
    for T in years:
        tr = D[D.year < T]; te = D[D.year == T]
        if te.y.sum() < 3 or tr.y.sum() < 5: continue
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        m = LogisticRegression(C=1.0, max_iter=500).fit((tr[feats] - mu) / sd, tr.y)
        p = m.predict_proba((te[feats] - mu) / sd)[:, 1]
        res[T] = round(roc_auc_score(te.y, p), 4); allp.append(pd.DataFrame({"y": te.y.values, "p": p, "year": T}))
    a = pd.concat(allp); res["pooled"] = round(roc_auc_score(a.y, a.p), 4)
    top = a[a.p >= a.groupby("year").p.transform(lambda s: s.quantile(.9))]; res["top10x"] = round(top.y.mean() / a.y.mean(), 2); res["n"] = len(a); res["events"] = int(a.y.sum())
    log(name, res); return res
R_ = {}
pay = ["mean_delay", "late_share", "od_share", "log_od_v", "log_inv_v", "lgr", "has_pay"]
R_["all|base"] = run(["lp"], "all base"); R_["all|+payments"] = run(["lp"] + pay, "all +payments")
#R_["all|+age/type"] = run(["lp", "age", "young", "is_sas"], "all +age/type")
#R_["all|+both"] = run(["lp"] + pay + ["age", "young", "is_sas"], "all +both")
m = S.has_pay == 1
R_["covered|base"] = run(["lp"], "covered base", m); R_["covered|+payments"] = run(["lp"] + pay[:-1], "covered +payments", m)
m2 = (S.paid_n >= 3)
R_["paid3|base"] = run(["lp"], "paid>=3 base", m2); R_["paid3|+payments"] = run(["lp"] + pay[:-1], "paid>=3 +payments", m2)
# raw signal strength: failure rate by overdue / delay buckets among covered
c = S[m & (S.year >= 2019)].copy()
c["od_b"] = pd.cut(c.od_share, [-0.01, 0, 0.1, 0.3, 1.01], labels=["none", "<=10%", "10-30%", ">30%"])
c["dl_b"] = pd.cut(c.mean_delay, [-61, 0, 15, 45, 400], labels=["on time/early", "<=15d late", "15-45d late", ">45d late"])
R_["rate_by_overdue"] = c.groupby("od_b").y.agg(["mean", "count", "sum"]).round(4).reset_index().to_dict("records")
R_["rate_by_delay"] = c[c.paid_n > 0].groupby("dl_b").y.agg(["mean", "count", "sum"]).round(4).reset_index().to_dict("records")
R_["base_rate_covered"] = round(float(c.y.mean()), 4)
# within same score band: do overdue suppliers fail more?
c["band"] = pd.qcut(c.lp.rank(method="first"), 5, labels=False)
R_["overdue_within_score_quintile"] = [(int(b), round(float(x[x.od_share > 0.1].y.mean()), 4), int((x.od_share > 0.1).sum()), round(float(x[x.od_share <= 0.1].y.mean()), 4)) for b, x in c.groupby("band")]
#R_["age_rate"] = S[S.year >= 2019].assign(ab=lambda d: pd.cut(d.age, [0, 3, 5, 10, 20, 100])).groupby("ab").y.agg(["mean", "count"]).round(4).reset_index().astype({"ab": str}).to_dict("records")
log(json.dumps(R_, indent=1, default=str))
json.dump(R_, open(f"{OUT}/co3_results.json", "w"), indent=1, default=str)
