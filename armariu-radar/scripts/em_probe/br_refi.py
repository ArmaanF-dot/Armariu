"""Brazil refinancing-timing test. Does a maturity wall predict a new debenture / promissory-note issue within 12 months?
Data: CVM open data, public offerings (oferta_distribuicao.zip: ICVM 400/476 incl. maturity dates; oferta_resolucao_160.csv for 2023+)."""
import io, json, re, sys, urllib.request, zipfile, time, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u):
    for i in range(3):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600).read()
        except Exception as e: print("retry", u, e, flush=True); time.sleep(5)
z = zipfile.ZipFile(io.BytesIO(get("https://dados.cvm.gov.br/dados/OFERTA/DISTRIB/DADOS/oferta_distribuicao.zip")))
legacy = pd.read_csv(z.open("oferta_distribuicao.csv"), sep=";", encoding="latin-1", dtype=str, low_memory=False)
r160 = pd.read_csv(z.open("oferta_resolucao_160.csv"), sep=";", encoding="latin-1", dtype=str, low_memory=False)
dig = lambda s: re.sub(r"\D", "", str(s)) if isinstance(s, str) else None
D = lambda s: pd.to_datetime(s, errors="coerce")
legacy["cnpj"] = legacy["CNPJ_Emissor"].map(dig); r160["cnpj"] = r160["CNPJ_Emissor"].map(dig)
debt_l = legacy["Tipo_Ativo"].fillna("").str.contains("DEB[ÊE]NTURES|NOTAS PROMISS|NOTA COMERCIAL", case=False, regex=True)
L = legacy[debt_l & legacy["cnpj"].notna() & (legacy["Tipo_Oferta"] != "Secundária")].copy()
L["issue_date"] = D(L["Data_Inicio_Oferta"]).fillna(D(L["Data_Registro_Oferta"])).fillna(D(L["Data_Dispensa_Oferta"]))
L["mat"] = D(L["Data_Vencimento"]); L["emis"] = D(L["Data_Emissao"])
L["amt"] = pd.to_numeric(L["Valor_Total"], errors="coerce")
L = L[L["issue_date"].between("2000-01-01", "2026-12-31")]
L.loc[~L["mat"].between("1995-01-01", "2070-01-01"), "mat"] = pd.NaT
R = r160[r160["Valor_Mobiliario"].fillna("").str.contains("Deb[êe]ntures|Notas Comerciais", case=False, regex=True) & r160["cnpj"].notna() & (r160["Tipo_Oferta"] != "SECUNDARIA")].copy()
R = R[~R["Status_Requerimento"].fillna("").str.contains("Revogada|Caducado|Expirado", regex=True)]
R["issue_date"] = D(R["Data_Registro"])
events = pd.concat([L[["cnpj", "issue_date"]], R[["cnpj", "issue_date"]]]).dropna().drop_duplicates()
# one record per series: keep tranches with maturity
T = L.dropna(subset=["mat"])[["cnpj", "issue_date", "mat", "amt", "Especie_Ativo", "Tipo_Societario_Emissor"]].copy()
rep = {"debt_offering_rows": len(L), "issuers_legacy": int(L["cnpj"].nunique()), "rows_with_maturity": len(T), "r160_debt_rows": len(R),
       "issuers_r160": int(R["cnpj"].nunique()), "events_total": len(events), "legacy_date_range": [str(L["issue_date"].min().date()), str(L["issue_date"].max().date())],
       "tenor_years_median": round(float(((T["mat"] - T["issue_date"]).dt.days / 365.25).median()), 2)}
print(rep, flush=True)
ev_by = {k: np.sort(v.values) for k, v in events.groupby("cnpj")["issue_date"]}
rows = []
for yr in range(2014, 2023):
    t = pd.Timestamp(yr, 1, 1); t1 = t + pd.DateOffset(years=1); t6 = t + pd.DateOffset(months=6)
    past = T[T["issue_date"] < t]
    act = past[past["issue_date"] >= t - pd.DateOffset(years=5)]
    g = act.groupby("cnpj")
    base = pd.DataFrame({"n_series5": g.size()})
    base["last_issue"] = g["issue_date"].max()
    base["amt_issued5"] = g["amt"].sum()
    out = past[past["mat"] >= t]
    og = out.groupby("cnpj")
    base["outstanding"] = og["amt"].sum(); base["n_out"] = og.size()
    m12 = out[out["mat"] < t1].groupby("cnpj")["amt"].sum(); m6 = out[out["mat"] < t6].groupby("cnpj")["amt"].sum()
    base["mat12"] = m12; base["mat6"] = m6
    base["mat12_n"] = out[out["mat"] < t1].groupby("cnpj").size()
    base["first_mat_months"] = (og["mat"].min() - t).dt.days / 30.4
    base["avg_tenor"] = (act["mat"] - act["issue_date"]).dt.days.groupby(act["cnpj"]).mean() / 30.4
    base["collateral"] = act["Especie_Ativo"].fillna("").str.contains("real|flutuante|fidu", case=False).groupby(act["cnpj"]).max().astype(float)
    base["s_a_aberta"] = act["Tipo_Societario_Emissor"].fillna("").str.contains("Aberta").groupby(act["cnpj"]).max().astype(float)
    base = base.fillna({"outstanding": 0, "n_out": 0, "mat12": 0, "mat6": 0, "mat12_n": 0})
    base["months_since_last"] = (t - base["last_issue"]).dt.days / 30.4
    base["share_mat12"] = base["mat12"] / base["outstanding"].replace(0, np.nan)
    base["log_out"] = np.log1p(base["outstanding"]); base["log_mat12"] = np.log1p(base["mat12"]); base["log_issued5"] = np.log1p(base["amt_issued5"])
    base["issued_last12"] = (base["months_since_last"] <= 12).astype(float)
    base["year"] = yr
    def hit(c):
        a = ev_by.get(c, [])
        return int(((a > np.datetime64(t)) & (a <= np.datetime64(t1))).any()) if len(a) else 0
    base["y"] = [hit(c) for c in base.index]
    rows.append(base.reset_index().rename(columns={"index": "cnpj"}))
P = pd.concat(rows, ignore_index=True)
P.to_csv(f"{OUT}/br_refi_panel.csv.gz", index=False)
rep["panel"] = {"issuer_years": len(P), "events": int(P["y"].sum()), "base_rate": round(P["y"].mean(), 4), "by_year": P.groupby("year")["y"].agg(["count", "sum", "mean"]).round(3).to_dict("index")}
# headline: effect of a maturity inside 12m
O = P[P["outstanding"] > 0]
rep["with_outstanding"] = {"n": len(O), "rate": round(O["y"].mean(), 4)}
O["bucket"] = pd.cut(O["first_mat_months"], [-1, 3, 6, 12, 24, 1e9], labels=["0-3m", "3-6m", "6-12m", "12-24m", ">24m"])
rep["rate_by_first_maturity"] = O.groupby("bucket")["y"].agg(["count", "mean"]).round(3).to_dict("index")
rep["rate_maturing_vs_not"] = {"maturing_12m": round(O[O["mat12"] > 0]["y"].mean(), 4), "not": round(O[O["mat12"] == 0]["y"].mean(), 4),
                               "n_maturing": int((O["mat12"] > 0).sum()), "n_not": int((O["mat12"] == 0).sum())}
# within lender-type strata: non-serial (<=3 series) issuers
ns = O[O["n_series5"] <= 3]
rep["non_serial"] = {"n": len(ns), "rate": round(ns["y"].mean(), 4), "maturing_12m": round(ns[ns["mat12"] > 0]["y"].mean(), 4), "not": round(ns[ns["mat12"] == 0]["y"].mean(), 4),
                     "n_maturing": int((ns["mat12"] > 0).sum())}
FE = ["n_series5", "months_since_last", "log_issued5", "log_out", "log_mat12", "share_mat12", "first_mat_months", "avg_tenor", "collateral", "s_a_aberta", "n_out", "mat12_n"]
FE_BASE = ["n_series5", "months_since_last", "log_issued5", "avg_tenor", "collateral", "s_a_aberta"]  # no maturity info
def fit(tr, te, cols):
    Xtr = tr[cols]; Xte = te[cols]
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=1.0, random_state=0).fit(Xtr, tr["y"])
    return gb.predict_proba(Xte)[:, 1]
res = {}; sc = []
for ty in range(2018, 2023):
    tr = O[O["year"] < ty]; te = O[O["year"] == ty].copy()
    if te["y"].sum() < 15 or tr["y"].sum() < 50: continue
    te["p_full"] = fit(tr, te, FE); te["p_nomat"] = fit(tr, te, FE_BASE); te["p_naive"] = -te["months_since_last"].fillna(999)
    sc.append(te); res[ty] = {"n": len(te), "events": int(te["y"].sum()), **{k: round(roc_auc_score(te["y"], te[k]), 3) for k in ["p_naive", "p_nomat", "p_full"]}}
    print(ty, res[ty], flush=True)
S = pd.concat(sc)
rep["walk_forward"] = res
rep["pooled_auc"] = {k: round(roc_auc_score(S["y"], S[k]), 3) for k in ["p_naive", "p_nomat", "p_full"]}
S["dec"] = pd.qcut(S["p_full"].rank(method="first"), 10, labels=False) + 1
rep["deciles_full"] = S.groupby("dec")["y"].agg(["count", "sum", "mean"]).round(3).reset_index().to_dict("records")
rep["top_buckets"] = {}
for q in [0.05, 0.10, 0.25]:
    th = S["p_full"].quantile(1 - q); t = S[S["p_full"] >= th]
    rep["top_buckets"][f"top{int(q*100)}%"] = {"rate": round(t["y"].mean(), 3), "lift": round(t["y"].mean() / S["y"].mean(), 2), "capture": round(t["y"].sum() / S["y"].sum(), 3), "n": len(t)}
ns = S[S["n_series5"] <= 3]
if ns["y"].sum() > 20:
    rep["non_serial_pooled_auc"] = {k: round(roc_auc_score(ns["y"], ns[k]), 3) for k in ["p_naive", "p_nomat", "p_full"]}
# timing: for maturing issuers that did re-issue, months between issue and the first maturity
tm = []
for _, r in S[(S["mat12"] > 0) & (S["y"] == 1)].iterrows():
    t = pd.Timestamp(int(r["year"]), 1, 1); a = ev_by[r["cnpj"]]; a = a[(a > np.datetime64(t)) & (a <= np.datetime64(t + pd.DateOffset(years=1)))]
    tm.append(((pd.Timestamp(a[0]) - t).days / 30.4) - r["first_mat_months"])
if tm: rep["reissue_vs_first_maturity_months"] = pd.Series(tm).describe().round(1).to_dict()
# listed-company link: current debt vs cash from DFP (latest year) for context
json.dump(rep, open(f"{OUT}/br_refi.json", "w"), indent=1, default=str, ensure_ascii=False)
print(json.dumps({k: rep[k] for k in ["rate_maturing_vs_not", "non_serial", "pooled_auc", "top_buckets"]}, indent=1, default=str))
