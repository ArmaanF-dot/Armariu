"""Full Colombia distress backtest + lead list.
Data: Supersociedades NIIF financials (datos.gov.co, CC BY-SA 4.0) and Supersociedades monthly insolvency files.
Label: first admission to reorganisation / judicial liquidation within 12 months after accounts become public (1 Jun Y+1 .. 31 May Y+2)."""
import io, json, os, re, sys, time, urllib.request, urllib.parse, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
USD = 4000.0
log = lambda *a: print(*a, flush=True)
def get(u, t=600):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as r: return r.read()
        except Exception as e: log("retry", u[:110], repr(e)[:80]); time.sleep(5 * (i + 1))
    raise RuntimeError(u)
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)))
def soda_all(ds, q, page=50000, maxrows=3_000_000, order=":id"):
    rows = []; off = 0
    while off < maxrows:
        r = soda(ds, dict(q, **{"$limit": page, "$offset": off, "$order": order})); rows += r; off += page
        if len(r) < page: break
    return pd.DataFrame(rows)
rep = {}
# ---------- 1. insolvency events ----------
MONTHS = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12,
          "ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,"ago":8,"sep":9,"oct":10,"nov":11,"dic":12}
def snap_date(name):
    n = name.lower()
    y = re.search(r"(20\d\d)", n)
    if "base-insolvencia.xlsx" in n: return pd.Timestamp("2019-12-31")
    if not y: return None
    y = int(y.group(1))
    if "trimestre-1" in n: return pd.Timestamp(y, 3, 31)
    for k in sorted(MONTHS, key=len, reverse=True):
        if re.search(rf"[-_ ]{k}(?![a-z])|{k}[-_ .]", n): return pd.Timestamp(y, MONTHS[k], 1) + pd.offsets.MonthEnd(0)
    return None
def clean_nit(s):
    s = re.sub(r"\D", "", str(s)).lstrip("0")
    return s[:9] if len(s) == 10 and s[0] in "89" else s
h = open(f"{OUT}/superso_informes.html").read()
links = sorted(set(m.replace("&amp;", "&") for m in re.findall(r'href="([^"]+)"', h) if ".xlsx" in m and re.search(r"insolv", m, re.I)))
recs = []; seen_names = set()
for l in links:
    name = l.split("/")[-2]
    sd = snap_date(name)
    if sd is None or name in seen_names: log("skip", name); continue
    seen_names.add(name)
    try:
        xs = pd.read_excel(io.BytesIO(get(urllib.parse.urljoin("https://www.supersociedades.gov.co/", l))), sheet_name=None, dtype=str)
    except Exception as e: log("fail", name, repr(e)[:100]); continue
    for k, v in xs.items():
        v.columns = [str(c).strip().upper().replace(" ", "_") for c in v.columns]
        if "NIT" not in v.columns: continue
        if len(xs) > 1 and "Base-Insolvencia" not in name and k != list(xs)[0]: continue
        adm = next((c for c in v.columns if "AUTO_ADMISION" in c), None)
        yc = next((c for c in v.columns if c.startswith("AÑO_DE_APERTURA")), None)
        d = pd.DataFrame({"nit": v["NIT"].map(clean_nit), "snap": sd, "proceso": v.get("PROCESO"),
                          "adm": pd.to_datetime(v[adm], errors="coerce") if adm else pd.NaT,
                          "open_year": pd.to_numeric(v[yc], errors="coerce").where(lambda x: x.between(1990, 2026)) if yc else np.nan,
                          "cat": v.get("CATEGORIA"), "activo": v.get("ACTIVO"), "nombre": v.get("NOMBRE"), "ciiu": v.get("CIIU"), "macro": v.get("MACROSECTOR")})
        recs.append(d)
    log("ok", name, sd.date(), len(recs[-1]))
ev = pd.concat(recs); ev = ev[ev["nit"].str.len() >= 6]
first_snap = ev.groupby("nit")["snap"].min()
ev1 = ev.sort_values("snap").groupby("nit").agg(first_snap=("snap", "min"), adm=("adm", "min"), open_year=("open_year", "min"), nombre=("nombre", "last"),
                                              proceso=("proceso", "last"), cat=("cat", "last"), last_snap=("snap", "max")).reset_index()
base0 = ev["snap"].min()
est = ev1["first_snap"].copy()
init = (ev1["first_snap"] == base0) & ev1["open_year"].notna() & (ev1["open_year"] <= 2019)
est[init] = pd.to_datetime(ev1.loc[init, "open_year"].astype(int).astype(str) + "-07-01")
ev1["event_date"] = pd.concat([est, ev1["adm"]], axis=1).min(axis=1)
ev1.to_csv(f"{OUT}/co_events.csv", index=False)
rep["events"] = {"firms": len(ev1), "files": len(recs), "by_year": ev1["event_date"].dt.year.value_counts().sort_index().to_dict(),
                 "closed_before_latest": int((ev1["last_snap"] < ev["snap"].max()).sum()), "with_true_adm_date": int(ev1["adm"].notna().sum())}
log(rep["events"])
# ---------- 2. financials ----------
BS = ["Total de activos", "Total pasivos", "Pasivos corrientes totales", "Activos corrientes totales", "Efectivo y equivalentes al efectivo", "Patrimonio total"]
bs_where = "concepto in (" + ",".join("'" + c + "'" for c in BS) + ")"
inc_where = "(concepto like 'Ganancia (p%' OR concepto in ('Ingresos de actividades ordinarias','Costos financieros'))"
fin = []; concept_log = {}
for year in range(2016, 2026):
    t = time.time()
    fc = f"fecha_corte='{year}-12-31T00:00:00.000'"
    b = soda_all("pfdp-zks5", {"$select": "nit,punto_entrada,concepto,periodo,valor", "$where": f"{fc} AND {bs_where}"})
    i = soda_all("prwj-nzxa", {"$select": "nit,punto_entrada,concepto,periodo,valor", "$where": f"{fc} AND {inc_where}"})
    u = soda_all("6hqw-m3dm", {"$select": "nit,concepto,valor", "$where": f"{fc} AND concepto like 'Unidad de dilig%'"})
    log("pulled", year, len(b), len(i), len(u), round(time.time() - t))
    d = pd.concat([b, i]); d["year"] = year
    d["valor"] = pd.to_numeric(d["valor"], errors="coerce")
    rep.setdefault("periodo_values", {})[year] = d["periodo"].astype(str).value_counts().head(6).to_dict()
    pl = d["periodo"].astype(str).str.lower()
    d = d[(pl.str.contains(str(year)) | (pl.str.contains("actual") & ~pl.str.contains("anterior")))]
    d["indiv"] = d["punto_entrada"].astype(str).str.contains("Individual", case=False)
    concept_log[year] = d["concepto"].value_counts().head(12).to_dict()
    # unit multiplier
    if len(u):
        u["unit"] = u["valor"].str.lower().map(lambda s: 1e6 if "mill" in s else (1e3 if "mil" in s else 1.0))
        um = u.groupby("nit")["unit"].first()
        rep.setdefault("unit_values", {})[year] = u["valor"].value_counts().head(8).to_dict()
        d["unit"] = d["nit"].map(um).fillna(1e3)
    else: d["unit"] = 1e3  # assume thousands of pesos when the unit field is missing
    d["valor"] = d["valor"] * d["unit"]
    d = d.sort_values("indiv", ascending=False).drop_duplicates(["nit", "concepto"])
    fin.append(d[["nit", "year", "concepto", "valor"]])
fin = pd.concat(fin)
rep["concepts_sample"] = concept_log
def pick(names, pat, excl=None):
    c = [n for n in names if re.search(pat, n, re.I) and not (excl and re.search(excl, n, re.I))]
    return c
allc = fin["concepto"].value_counts()
net_name = next((n for n in allc.index if re.fullmatch(r"ganancia \(p.rdida\)", n, re.I)), None)
op_name = next((n for n in allc.index if re.search(r"ganancia \(p.rdida\) por actividades de operaci", n, re.I)), None)
rep["profit_concepts"] = {"net": net_name, "op": op_name, "ganancia_like": allc[[n for n in allc.index if n.lower().startswith("ganancia (p")]].to_dict()}
log(rep["profit_concepts"])
w = fin.pivot_table(index=["nit", "year"], columns="concepto", values="valor", aggfunc="first").reset_index()
ren = {"Total de activos": "A", "Total pasivos": "L", "Pasivos corrientes totales": "CL", "Activos corrientes totales": "CA", "Efectivo y equivalentes al efectivo": "C",
       "Patrimonio total": "E", "Ingresos de actividades ordinarias": "R", "Costos financieros": "FC"}
if net_name: ren[net_name] = "NI"
if op_name: ren[op_name] = "OP"
w = w.rename(columns=ren)
for c in ["A", "L", "CL", "CA", "C", "E", "R", "FC", "NI", "OP"]:
    if c not in w: w[c] = np.nan
for c in ["A", "L", "CL", "CA", "C", "E", "R", "FC", "NI", "OP"]: w[c] = pd.to_numeric(w[c], errors="coerce").astype(float)
w["FC"] = w["FC"].abs()
w = w[(w["A"] > 0)].copy()
sf = lambda a, b: (a / b).replace([np.inf, -np.inf], np.nan)
w["lev"] = sf(w["L"], w["A"]); w["cur"] = sf(w["CA"], w["CL"]); w["cash_a"] = sf(w["C"], w["A"]); w["cash_cl"] = sf(w["C"], w["CL"])
w["roa"] = sf(w["NI"], w["A"]); w["opm"] = sf(w["OP"], w["R"]); w["int_burden"] = sf(w["FC"], w["R"].where(w["R"] > 0))
w["icr"] = sf(w["OP"], w["FC"].where(w["FC"] > 0)); w["cl_share"] = sf(w["CL"], w["L"].where(w["L"] > 0)); w["neg_eq"] = (w["E"] < 0).astype(float)
w["loss"] = (w["NI"] < 0).astype(float); w["asset_turn"] = sf(w["R"], w["A"]); w["fin_a"] = sf(w["FC"], w["A"])
w["usd_assets_m"] = w["A"] / USD / 1e6
w["log_a"] = np.log1p(w["usd_assets_m"].clip(lower=0) * 1e6)
w = w.sort_values(["nit", "year"])
g = w.groupby("nit")
w["rev_growth"] = g["R"].pct_change().where(g["year"].diff() == 1)
w["asset_growth"] = g["A"].pct_change().where(g["year"].diff() == 1)
w["lev_chg"] = g["lev"].diff().where(g["year"].diff() == 1)
w["loss_prev"] = g["loss"].shift(1).where(g["year"].diff() == 1)
w["roa_prev"] = g["roa"].shift(1).where(g["year"].diff() == 1)
w.to_csv(f"{OUT}/co_features.csv.gz", index=False)
log("features", w.shape)
FEATS = ["lev", "cur", "cash_a", "cash_cl", "roa", "opm", "int_burden", "icr", "cl_share", "neg_eq", "loss", "asset_turn", "fin_a", "log_a",
         "rev_growth", "asset_growth", "lev_chg", "loss_prev", "roa_prev"]
# ---------- 3. labels ----------
evd = ev1.set_index("nit")["event_date"]
w["ev_date"] = w["nit"].map(evd)
def label(row_year, ev_date):
    s = pd.Timestamp(row_year + 1, 6, 1); e = pd.Timestamp(row_year + 2, 6, 1)
    return (ev_date >= s) & (ev_date < e)
w["prior"] = w.apply(lambda r: pd.notna(r["ev_date"]) and r["ev_date"] < pd.Timestamp(int(r["year"]) + 1, 6, 1), axis=1)
w["y"] = [int(label(int(a), b)) if pd.notna(b) else 0 for a, b in zip(w["year"], w["ev_date"])]
# ---------- 4. walk-forward ----------
def prep(df, med=None):
    X = df[FEATS].replace([np.inf, -np.inf], np.nan)
    lo, hi = X.quantile(0.01), X.quantile(0.99) if med is None else med[1:]
    return X
def fit_predict(tr, te):
    Xtr = tr[FEATS].replace([np.inf, -np.inf], np.nan); Xte = te[FEATS].replace([np.inf, -np.inf], np.nan)
    lo, hi, med = Xtr.quantile(0.01), Xtr.quantile(0.99), Xtr.median()
    Ztr = Xtr.clip(lo, hi, axis=1).fillna(med); Zte = Xte.clip(lo, hi, axis=1).fillna(med)
    mu, sd = Ztr.mean(), Ztr.std().replace(0, 1)
    lr = LogisticRegression(C=0.3, max_iter=2000, class_weight="balanced").fit((Ztr - mu) / sd, tr["y"])
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0, random_state=0).fit(Xtr, tr["y"])
    p_lr = lr.predict_proba((Zte - mu) / sd)[:, 1]; p_gb = gb.predict_proba(Xte)[:, 1]
    return p_lr, p_gb, dict(zip(FEATS, lr.coef_[0]))
def bands(a): return pd.cut(a, [-1, 5, 20, 1e9], labels=["<$5m", "$5-20m", ">$20m"])
res = {"by_year": {}}; scored = []
pool = w[~w["prior"]]
for ty in range(2019, 2025):
    tr = pool[pool["year"] <= ty - 1]; te = pool[pool["year"] == ty].copy()
    if tr["y"].sum() < 30 or te["y"].sum() < 10: continue
    p_lr, p_gb, coef = fit_predict(tr, te)
    te["p_lr"] = p_lr; te["p_gb"] = p_gb; te["p_base"] = te["lev"].fillna(te["lev"].median())
    te["p_ens"] = (te["p_lr"].rank(pct=True) + te["p_gb"].rank(pct=True)) / 2
    scored.append(te)
    r = {"firms": len(te), "events": int(te["y"].sum())}
    for k in ["p_base", "p_lr", "p_gb", "p_ens"]: r["auc_" + k[2:]] = round(roc_auc_score(te["y"], te[k]), 3)
    res["by_year"][ty] = r; log(ty, r)
sc = pd.concat(scored)
res["pooled"] = {k[2:]: round(roc_auc_score(sc["y"], sc[k]), 3) for k in ["p_base", "p_lr", "p_gb", "p_ens"]}
sc["band"] = bands(sc["usd_assets_m"])
res["by_band"] = {}
for b, d in sc.groupby("band"):
    if d["y"].sum() >= 10: res["by_band"][str(b)] = {"firms": len(d), "events": int(d["y"].sum()), "base_rate": round(d["y"].mean(), 4),
        **{"auc_" + k[2:]: round(roc_auc_score(d["y"], d[k]), 3) for k in ["p_base", "p_lr", "p_gb", "p_ens"]}}
# odds per decile (ensemble), pooled and per band
def deciles(d, col="p_ens"):
    d = d.copy(); d["dec"] = pd.qcut(d[col].rank(method="first"), 10, labels=False) + 1
    t = d.groupby("dec")["y"].agg(["count", "sum", "mean"]).rename(columns={"sum": "events", "mean": "rate"})
    t["lift"] = t["rate"] / d["y"].mean(); return t
dec = deciles(sc); dec.to_csv(f"{OUT}/co_odds_deciles_pooled.csv")
res["deciles_pooled"] = dec.round(4).reset_index().to_dict("records")
res["top_buckets_pooled"] = {}
for q in [0.01, 0.05, 0.10, 0.25]:
    thr = sc["p_ens"].quantile(1 - q); t = sc[sc["p_ens"] >= thr]
    res["top_buckets_pooled"][f"top{int(q*100)}%"] = {"rate": round(t["y"].mean(), 4), "lift": round(t["y"].mean() / sc["y"].mean(), 2), "capture": round(t["y"].sum() / sc["y"].sum(), 3), "n": len(t)}
big = sc[sc["usd_assets_m"] >= 20]
if big["y"].sum() >= 10:
    d2 = deciles(big); d2.to_csv(f"{OUT}/co_odds_deciles_over20m.csv"); res["deciles_over20m"] = d2.round(4).reset_index().to_dict("records")
    res["top_buckets_over20m"] = {}
    for q in [0.05, 0.10, 0.25]:
        thr = big["p_ens"].quantile(1 - q); t = big[big["p_ens"] >= thr]
        res["top_buckets_over20m"][f"top{int(q*100)}%"] = {"rate": round(t["y"].mean(), 4), "lift": round(t["y"].mean() / big["y"].mean(), 2), "capture": round(t["y"].sum() / big["y"].sum(), 3), "n": len(t)}
# how early? months from filing window start to event for hits
sc["lead_months"] = ((sc["ev_date"] - pd.to_datetime(sc["year"].astype(int) + 1, format="%Y") ).dt.days / 30.4).where(sc["y"] == 1)
res["event_timing_months_after_Jan_of_Y+1"] = sc["lead_months"].describe().round(1).to_dict()
sc[["nit", "year", "y", "p_lr", "p_gb", "p_ens", "usd_assets_m"]].to_csv(f"{OUT}/co_backtest_scored.csv.gz", index=False)
# ---------- 5. public-contract signal (SECOP II) ----------
try:
    t0 = time.time()
    sec = soda_all("jbjy-vk9h", {"$select": "documento_proveedor as nit, date_extract_y(fecha_de_firma) as y, sum(valor_del_contrato) as v, count(*) as n",
                                 "$where": "tipodocproveedor='NIT' AND fecha_de_firma>='2016-01-01T00:00:00'", "$group": "documento_proveedor, y"}, page=50000, maxrows=1_500_000, order="nit, y")
    sec["nit"] = sec["nit"].map(clean_nit); sec["y"] = pd.to_numeric(sec["y"], errors="coerce"); sec["v"] = pd.to_numeric(sec["v"], errors="coerce"); sec["n"] = pd.to_numeric(sec["n"], errors="coerce")
    sec = sec.groupby(["nit", "y"])[["v", "n"]].sum().reset_index(); sec.to_csv(f"{OUT}/co_secop_by_nit_year.csv.gz", index=False)
    log("secop", len(sec), round(time.time() - t0))
    m = sc.merge(sec.rename(columns={"y": "year"}), on=["nit", "year"], how="left")
    m["pub_share"] = (m["v"].fillna(0) / (m["usd_assets_m"] * USD * 1e6)).clip(0, 5)
    m["has_pub"] = (m["v"].fillna(0) > 0).astype(int)
    r = {}
    r["share_firms_with_public_contracts"] = round(m["has_pub"].mean(), 3)
    r["event_rate_with_pub"] = round(m.loc[m.has_pub == 1, "y"].mean(), 4); r["event_rate_without_pub"] = round(m.loc[m.has_pub == 0, "y"].mean(), 4)
    # incremental: add pub features to ensemble via a simple second-stage logistic
    for k in ["p_ens"]:
        Z = pd.DataFrame({"s": m[k], "pub": m["has_pub"], "pub_share": m["pub_share"]}).fillna(0)
        from sklearn.model_selection import cross_val_predict, GroupKFold
        pr = cross_val_predict(LogisticRegression(max_iter=1000), Z, m["y"], cv=GroupKFold(5), groups=m["nit"], method="predict_proba")[:, 1]
        r["auc_stage1"] = round(roc_auc_score(m["y"], m[k]), 3); r["auc_with_public_contracts"] = round(roc_auc_score(m["y"], pr), 3)
    res["secop"] = r; log(r)
except Exception as e:
    res["secop_err"] = repr(e)[:300]; log("secop failed", e); sec = pd.DataFrame(columns=["nit", "y", "v", "n"])
# ---------- 6. score FY2025 and build lead lists ----------
tr = pool[pool["year"] <= 2024]; te = w[(w["year"] == 2025)].copy()
live_events = set(ev1.loc[ev1["last_snap"] == ev["snap"].max(), "nit"])
te["in_insolvency_now"] = te["nit"].isin(set(ev1["nit"]))
p_lr, p_gb, coef = fit_predict(tr, te)
te["p_lr"] = p_lr; te["p_gb"] = p_gb; te["risk"] = (pd.Series(p_lr, index=te.index).rank(pct=True) + pd.Series(p_gb, index=te.index).rank(pct=True)) / 2
res["lr_coefficients_std"] = {k: round(v, 3) for k, v in coef.items()}
te = te.merge(sec[sec["y"] == 2025].rename(columns={"v": "secop_2025_cop", "n": "secop_2025_n"})[["nit", "secop_2025_cop", "secop_2025_n"]], on="nit", how="left")
te = te.merge(sec[sec["y"] == 2024].rename(columns={"v": "secop_2024_cop"})[["nit", "secop_2024_cop"]], on="nit", how="left")
leads = te[(te["usd_assets_m"] >= 20) & (~te["in_insolvency_now"])].copy()
res["lead_universe"] = {"firms_2025_total": len(te), "firms_over_20m_not_in_insolvency": len(leads)}
watch = leads.sort_values("risk", ascending=False).head(150)
borrow = leads[(leads["risk"].between(0.35, 0.85)) & (leads["rev_growth"] > 0.15) & (leads["cl_share"] > 0.6) & (leads["cash_cl"] < 0.15) & (leads["roa"] > -0.02)
               ].copy()
borrow["need_score"] = borrow["rev_growth"].clip(0, 1) + borrow["cl_share"] + (0.15 - borrow["cash_cl"]).clip(0, 0.15) * 3
borrow = borrow.sort_values("need_score", ascending=False).head(150)
# names and context from caratula / RUES
def context(nits):
    nits = list(nits); out = []
    for i in range(0, len(nits), 150):
        chunk = nits[i:i + 150]; lst = ",".join("'" + n + "'" for n in chunk)
        try:
            c = soda("6hqw-m3dm", {"$select": "nit,concepto,valor", "$where": f"fecha_corte='2025-12-31T00:00:00.000' AND nit in ({lst}) AND (concepto like 'Raz%n social%' OR concepto like 'Clasificaci%n Industrial Internacional Uniforme Versi%n 4 A.C%' OR concepto like 'Ciudad de la direcci%n del domicilio' OR concepto like 'Nombre o raz%n social del controlante%' OR concepto like 'Pa%s del domicilio del controlante%' OR concepto like 'La sociedad es%')", "$limit": 50000})
            out += c
        except Exception as e: log("ctx fail", repr(e)[:100])
    d = pd.DataFrame(out)
    if d.empty: return pd.DataFrame(columns=["nit"])
    d["k"] = d["concepto"].map(lambda s: "name" if re.match(r"Raz", s) else "ciiu" if "A.C" in s else "city" if s.startswith("Ciudad") else "parent" if s.startswith("Nombre o raz") else "parent_country" if s.startswith("Pa") else "type")
    return d.pivot_table(index="nit", columns="k", values="valor", aggfunc="first").reset_index()
ctx = context(set(watch["nit"]) | set(borrow["nit"]))
cols = ["nit", "name", "ciiu", "city", "parent", "parent_country", "type", "year", "usd_assets_m", "R", "lev", "cur", "cash_cl", "roa", "rev_growth", "cl_share", "risk", "secop_2025_cop", "secop_2025_n"]
def fmt(d, extra=()):
    d = d.merge(ctx, on="nit", how="left")
    d["usd_assets_m"] = d["usd_assets_m"].round(1); d["revenue_usd_m"] = (d["R"] / USD / 1e6).round(1)
    d["risk_pct_rank"] = (d["risk"] * 100).round(0)
    keep = [c for c in ["nit", "name", "ciiu", "city", "parent", "parent_country", "usd_assets_m", "revenue_usd_m", "lev", "cur", "cash_cl", "roa", "rev_growth", "cl_share", "risk_pct_rank", "secop_2025_cop", *extra] if c in d]
    return d[keep].round(3)
watch_f, borrow_f = fmt(watch), fmt(borrow, ["need_score"])
watch_f.to_csv(f"{OUT}/co_watchlist.csv", index=False); borrow_f.to_csv(f"{OUT}/co_borrower_candidates.csv", index=False)
with pd.ExcelWriter(f"{OUT}/colombia-lead-list.xlsx") as xw:
    watch_f.to_excel(xw, sheet_name="Distress watchlist", index=False)
    borrow_f.to_excel(xw, sheet_name="Borrower candidates", index=False)
res["lead_counts"] = {"watchlist": len(watch), "borrower_candidates": len(borrow)}
res["watchlist_top_decile_rate_backtest"] = res.get("top_buckets_over20m", {}).get("top10%")
json.dump(res, open(f"{OUT}/backtest_co.json", "w"), indent=1, default=str, ensure_ascii=False)
log("done"); log(json.dumps({k: res[k] for k in ["pooled", "by_band", "top_buckets_pooled"] if k in res}, indent=1, default=str))
