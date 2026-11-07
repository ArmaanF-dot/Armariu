"""Fourth probe: overlap of Colombian insolvency filers with open financials, and a tiny ratio backtest."""
import io, json, os, re, sys, urllib.request, urllib.parse, time
import pandas as pd, numpy as np
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u, t=600):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as r: return r.read()
        except Exception as e: print("retry", u[:120], e, flush=True); time.sleep(5)
    raise RuntimeError(u)
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)))
rep = {}
h = open(f"{OUT}/superso_informes.html").read()
links = sorted(set(m.replace("&amp;", "&") for m in re.findall(r'href="([^"]+)"', h) if re.search(r"insolv", m, re.I) and m.endswith(tuple("0123456789")) or "INSOLV" in m.upper() and ".xlsx" in m))
frames = []
for l in links:
    if ".xlsx" not in l: continue
    try:
        x = pd.read_excel(io.BytesIO(get(urllib.parse.urljoin("https://www.supersociedades.gov.co/", l))), sheet_name=0, dtype=str)
        x.columns = [str(c).strip().upper() for c in x.columns]
        nitc = [c for c in x.columns if c == "NIT" or c.startswith("NIT")]
        fc = [c for c in x.columns if "ADMISION" in c or "ADMISIÓN" in c or "APERTURA" in c and "FECHA" in c]
        pc = [c for c in x.columns if c.startswith("PROCESO") or c == "TIPO DE PROCESO"]
        if not nitc or not fc: print("skip cols", l[-80:], list(x.columns)[:20]); continue
        f = pd.DataFrame({"nit": x[nitc[0]].str.replace(r"\D", "", regex=True).str[:9], "adm": pd.to_datetime(x[fc[0]], errors="coerce"),
                          "proceso": x[pc[0]] if pc else "", "activo": x.get("ACTIVO"), "file": l.split("/")[-2][:60]})
        frames.append(f); print("ok", l.split("/")[-2], len(f), flush=True)
    except Exception as e: print("fail", l[-80:], repr(e)[:150], flush=True)
allf = pd.concat(frames).dropna(subset=["adm"])
first = allf.sort_values("adm").groupby("nit").first().reset_index()
first.to_csv(f"{OUT}/co_insolv_union.csv", index=False)
rep["union_files"] = len(frames); rep["union_firms"] = len(first)
rep["union_by_year"] = first["adm"].dt.year.value_counts().sort_index().to_dict()
# financials: totals for FY2022 (and FY2021)
def pull(ds, year, pat):
    rows = []; off = 0
    while True:
        q = {"$select": "nit, concepto, periodo, valor", "$where": f"fecha_corte='{year}-12-31T00:00:00.000' AND ({pat})", "$limit": 50000, "$offset": off}
        r = soda(ds, q); rows += r; off += 50000
        if len(r) < 50000: break
    return pd.DataFrame(rows)
res = {}
for year in [2021, 2022]:
    bs = pull("pfdp-zks5", year, "concepto in ('Total de activos','Total de pasivos','Total pasivos','Total activos','Pasivos corrientes totales','Activos corrientes totales','Efectivo y equivalentes al efectivo','Patrimonio total')")
    inc = pull("prwj-nzxa", year, "concepto in ('Ingresos de actividades ordinarias','Ganancia (pérdida)','Ganancia (pérdida) por actividades de operación','Costos financieros')")
    rep[f"concepts_{year}"] = {"bs": bs["concepto"].value_counts().to_dict() if len(bs) else {}, "inc": inc["concepto"].value_counts().to_dict() if len(inc) else {}}
    d = pd.concat([bs, inc])
    if not len(d): continue
    d["valor"] = pd.to_numeric(d["valor"], errors="coerce")
    cur = d[d["periodo"].astype(str).str.contains(str(year)) | d["periodo"].astype(str).str.contains("Actual", case=False)]
    w = cur.groupby(["nit", "concepto"])["valor"].first().unstack()
    w.to_csv(f"{OUT}/co_fin_{year}.csv")
    res[year] = w
    print("fin", year, w.shape, flush=True)
from sklearn.metrics import roc_auc_score
for year, w in res.items():
    start = pd.Timestamp(f"{year+1}-06-01"); end = start + pd.DateOffset(years=1)
    prior = set(first.loc[first["adm"] < start, "nit"])
    pos = set(first.loc[(first["adm"] >= start) & (first["adm"] < end), "nit"])
    w = w[~w.index.isin(prior)].copy()
    y = w.index.isin(pos).astype(int)
    out = {"firms": len(w), "events_12m": int(y.sum()), "insolv_in_window_total": len(pos), "share_of_window_events_covered": round(y.sum() / max(1, len(pos)), 3)}
    def col(*names):
        for n in names:
            if n in w: return w[n]
        return pd.Series(np.nan, index=w.index)
    A = col("Total de activos", "Total activos"); L = col("Total de pasivos", "Total pasivos")
    CA = col("Activos corrientes totales"); CL = col("Pasivos corrientes totales"); P = col("Ganancia (pérdida)"); R = col("Ingresos de actividades ordinarias"); C = col("Efectivo y equivalentes al efectivo")
    feats = {"leverage L/A": L / A, "neg current ratio": -(CA / CL), "neg ROA": -(P / A), "neg cash/A": -(C / A), "neg log assets": -np.log1p(A.clip(lower=0))}
    for k, s in feats.items():
        m = s.replace([np.inf, -np.inf], np.nan).notna()
        if m.sum() > 100 and y[m.values].sum() > 5:
            out[k] = {"auc": round(roc_auc_score(y[m.values], s[m].values), 3), "n": int(m.sum()), "events": int(y[m.values].sum())}
    # simple combined rank score, top-decile lift
    rk = pd.concat([s.replace([np.inf, -np.inf], np.nan).rank(pct=True) for s in list(feats.values())[:4]], axis=1).mean(axis=1)
    m = rk.notna()
    if m.sum():
        out["combined_auc"] = round(roc_auc_score(y[m.values], rk[m].values), 3)
        top = rk[m] >= rk[m].quantile(0.9)
        base = y[m.values].mean(); out["base_rate"] = round(base, 4)
        out["top10_rate"] = round(y[m.values][top.values].mean(), 4); out["top10_lift"] = round(out["top10_rate"] / base, 2) if base else None
        out["top10_capture"] = round(y[m.values][top.values].sum() / max(1, y[m.values].sum()), 3)
    rep[f"backtest_fy{year}"] = out
    print(year, out, flush=True)
json.dump(rep, open(f"{OUT}/test4.json", "w"), indent=1, default=str, ensure_ascii=False)
print("done")
