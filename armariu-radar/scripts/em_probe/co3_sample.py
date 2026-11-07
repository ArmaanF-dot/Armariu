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
S0 = pd.read_csv(f"{OUT}/co_backtest_scored.csv.gz"); S0["nit"] = S0["nit"].astype("int64").astype(str)
ev_nits = set(S0[S0.y == 1].nit); rng = np.random.RandomState(7)
others = np.array(sorted(set(S0.nit) - ev_nits)); samp = set(rng.choice(others, 6000, replace=False))
nits = sorted(ev_nits | samp); log("sampled nits", len(nits), "event nits", len(ev_nits))
cols = "documento_proveedor,fecha_de_emision,fecha_de_vencimiento,fecha_real_de_pago,estado,valor_total_de_la_factura,nit_entidad"
rows = []
for i in range(0, len(nits), 150):
    ch = nits[i:i + 150]; lst = ",".join("'" + n + "'" for n in ch)
    r = allpages(PP, {"$select": cols, "$where": f"documento_proveedor in ({lst})", "$order": "id_del_contrato,id_de_pago"}, page=50000, maxp=40)
    rows.append(r); log("chunk", i, len(r), flush=True) if False else log("chunk", i, len(r))
raw = pd.concat(rows, ignore_index=True); log("raw invoices", raw.shape)
raw["nit"] = raw["documento_proveedor"].map(clean_nit)
for c in ["fecha_de_emision", "fecha_de_vencimiento", "fecha_real_de_pago"]: raw[c] = pd.to_datetime(raw[c].str[:10], errors="coerce")
raw["v"] = pd.to_numeric(raw["valor_total_de_la_factura"], errors="coerce").fillna(0)
raw["delay"] = (raw.fecha_real_de_pago - raw.fecha_de_vencimiento).dt.days
raw.to_csv(f"{OUT}/co_secop_pay_raw_sample.csv.gz", index=False)
recs = []
for Y in range(2017, 2025):
    c0, c1 = pd.Timestamp(Y, 1, 1), pd.Timestamp(Y, 12, 31); d16 = pd.Timestamp(2016, 1, 1)
    a = raw[raw.fecha_real_de_pago.between(c0, c1) & (raw.fecha_de_vencimiento >= d16) & raw.delay.between(-365, 1500)]
    A = a.groupby("nit").agg(paid_n=("v", "size"), sum_delay=("delay", "sum"), late30=("delay", lambda x: (x > 30).sum()), paid_v=("v", "sum"))
    unpaid = (raw.fecha_real_de_pago > c1) | (raw.fecha_real_de_pago.isna() & (raw.estado != "Pagado"))
    bb = raw[raw.fecha_de_vencimiento.between(d16, pd.Timestamp(Y, 12, 1)) & unpaid]
    B = bb.groupby("nit").agg(od_n=("v", "size"), od_v=("v", "sum"))
    cc = raw[raw.fecha_de_emision.between(c0, c1)]
    C = cc.groupby("nit").agg(inv_n=("v", "size"), inv_v=("v", "sum"))
    d = A.join(B, how="outer").join(C, how="outer").reset_index(); d["year"] = Y; recs.append(d)
P = pd.concat(recs).fillna(0); log("payment panel", P.shape)
R = pd.DataFrame({"nit": [], "fm": [], "ts": [], "oj": []})
# join to scored
S = S0[S0.nit.isin(set(nits))].copy()
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
    top = a[a.p >= a.groupby("year").p.transform(lambda s: s.quantile(.9))]; res["n"] = len(a); res["events"] = int(a.y.sum())
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
