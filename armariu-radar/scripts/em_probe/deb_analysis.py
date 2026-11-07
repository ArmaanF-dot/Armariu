"""Brazil debentures registry (debentures.com.br SND): coverage, comparison with CVM FRE, and a maturity-wall test with complete maturity data."""
import io, json, re, sys, time, urllib.request, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT, HERE = sys.argv[1], sys.argv[2]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
u = "https://www.debentures.com.br/exploreosnd/consultaadados/emissoesdedebentures/caracteristicas_e.asp?tip_deb=&op_exc=Nada"
raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600).read()
txt = raw.decode("latin-1")
lines = txt.split("\n"); hi = next(i for i, l in enumerate(lines) if l.startswith("Codigo do Ativo"))
df = pd.read_csv(io.StringIO("\n".join(lines[hi:])), sep="\t", dtype=str, engine="python", on_bad_lines="skip")
df.columns = [re.sub(r"\s+", " ", c).strip() for c in df.columns]
df = df[df["Codigo do Ativo"].notna()].copy()
for c in df.columns: df[c] = df[c].astype(str).str.strip().replace({"nan": np.nan, "-": np.nan, "": np.nan})
D = lambda s: pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
n = lambda s: pd.to_numeric(s.astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
df["cnpj"] = df["CNPJ"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(14)
df["issue"] = D(df["Data de Emissao"]); df["mat"] = D(df["Data de Vencimento"]); df["exit"] = D(df["Data de Saida / Novo Vencimento"])
df["qty"] = n(df["Quantidade Emitida"]); df["vn0"] = n(df["Valor Nominal na Emissao"]); df["vna"] = n(df["Valor Nominal Atual"]); df["mkt"] = n(df["Quantidade em Mercado"])
df["amt0"] = df["qty"] * df["vn0"]; df["amt_now"] = df["mkt"] * df["vna"]
df["status"] = df["Situacao"]; df["reg"] = df["Registro CVM da Emissao"].fillna("")
df["exempt476"] = df["reg"].str.contains("476|DISPENSA", case=False)
df["motivo"] = df["Motivo de Saida"].fillna("")
rep = {"rows": len(df), "columns": list(df.columns)[:12], "issuers": int(df["cnpj"].nunique()), "status": df["status"].value_counts().to_dict(),
       "issue_year_counts": df["issue"].dt.year.value_counts().sort_index().to_dict(), "with_maturity": float(df["mat"].notna().mean()), "with_amount": float(df["amt0"].notna().mean()),
       "ict_476_or_dispensa_share": round(float(df["exempt476"].mean()), 3), "motivo_saida_top": df["motivo"].value_counts().head(15).to_dict(),
       "total_issued_brl_bn": round(float(df["amt0"].sum()) / 1e9, 1), "live_instruments": int((df["status"] == "Registrado").sum()),
       "live_issuers": int(df.loc[df["status"] == "Registrado", "cnpj"].nunique()), "live_outstanding_brl_bn": round(float(df.loc[df["status"] == "Registrado", "amt_now"].sum()) / 1e9, 1),
       "live_with_maturity_after_today": int(((df["status"] == "Registrado") & (df["mat"] > pd.Timestamp("2026-11-07"))).sum())}
print(json.dumps(rep, ensure_ascii=False, default=str)[:3000], flush=True)
df.to_csv(f"{OUT}/br_debentures_registry.csv.gz", index=False)
# --- compare with FRE
fre = pd.read_csv(f"{OUT}/br_fre_debt_rows.csv.gz", dtype=str)
fre["cnpj"] = fre["CNPJ_Companhia"].str.replace(r"\D", "", regex=True).str.zfill(14)
fre_c = set(fre["cnpj"]); reg_c = set(df["cnpj"])
livereg = df[(df["status"] == "Registrado") & (df["mat"] > pd.Timestamp("2026-11-07"))]
rep["vs_fre"] = {"fre_companies": len(fre_c), "fre_companies_in_registry": len(fre_c & reg_c), "registry_issuers": len(reg_c), "registry_issuers_not_in_fre": len(reg_c - fre_c),
                 "live_registry_issuers": int(livereg["cnpj"].nunique()), "live_registry_issuers_in_fre": int(livereg["cnpj"].isin(fre_c).sum() and livereg.loc[livereg["cnpj"].isin(fre_c), "cnpj"].nunique()),
                 "share_of_live_registry_amount_from_fre_companies": round(float(livereg.loc[livereg["cnpj"].isin(fre_c), "amt_now"].sum() / livereg["amt_now"].sum()), 3)}
fre["mat"] = pd.to_datetime(fre["Data_Vencimento"], errors="coerce")
fl = fre[(fre["mat"] > pd.Timestamp("2026-11-07")) & fre["Valor_Mobiliario"].str.contains("Deb", na=False)]
m = fl.assign(bal=pd.to_numeric(fl["Saldo_Devedor"], errors="coerce")).groupby("cnpj")["bal"].sum().rename("fre_bal").to_frame().join(livereg.groupby("cnpj")["amt_now"].sum().rename("reg_bal"), how="inner")
rep["vs_fre"]["matched_companies_amount_compare"] = {"n": len(m), "median_ratio_fre_over_registry": round(float((m["fre_bal"] / m["reg_bal"]).replace([np.inf, -np.inf], np.nan).median()), 2),
                                                     "share_within_25pct": round(float(((m["fre_bal"] / m["reg_bal"]).between(0.75, 1.25)).mean()), 2)}
# --- reconstruct outstanding and test wall -> new issuance
df = df.dropna(subset=["issue", "mat"]); df = df[df["issue"].between("1990-01-01", "2026-12-31") & df["mat"].between("1990-01-01", "2080-01-01")]
df["end"] = df["mat"]; ex = (df["status"] == "Excluído") & df["exit"].notna() & df["motivo"].str.contains("Resgate|Vencimento|Cancel|Convers|Amortiz", case=False)
df.loc[ex & (df["exit"] < df["end"]), "end"] = df.loc[ex & (df["exit"] < df["end"]), "exit"]
df["amt"] = df["amt0"].fillna(0)
issues = {k: np.sort(v.values) for k, v in df.groupby("cnpj")["issue"]}
rows = []
for yr in range(2013, 2026):
    t = pd.Timestamp(yr, 1, 1); t1 = t + pd.DateOffset(years=1); t2 = t + pd.DateOffset(years=2); t6 = t + pd.DateOffset(months=6)
    past = df[df["issue"] < t]; act = past[past["issue"] >= t - pd.DateOffset(years=5)]
    out = past[past["end"] >= t]
    g = act.groupby("cnpj"); B = pd.DataFrame({"n5": g.size()})
    B["last_issue"] = g["issue"].max(); B["amt5"] = g["amt"].sum(); B["tenor"] = (act["mat"] - act["issue"]).dt.days.groupby(act["cnpj"]).mean() / 30.4
    B["is476"] = act["exempt476"].groupby(act["cnpj"]).mean()
    og = out.groupby("cnpj"); B["outstanding"] = og["amt"].sum(); B["n_out"] = og.size(); B["first_mat"] = (og["end"].min() - t).dt.days / 30.4
    B["w12"] = out[out["end"] < t1].groupby("cnpj")["amt"].sum(); B["w6"] = out[out["end"] < t6].groupby("cnpj")["amt"].sum(); B["w24"] = out[out["end"] < t2].groupby("cnpj")["amt"].sum()
    B = B.fillna({"outstanding": 0, "n_out": 0, "w12": 0, "w6": 0, "w24": 0})
    B["m_last"] = (t - B["last_issue"]).dt.days / 30.4; B["share12"] = B["w12"] / B["outstanding"].replace(0, np.nan)
    B["lo"] = np.log1p(B["outstanding"]); B["lw12"] = np.log1p(B["w12"]); B["l5"] = np.log1p(B["amt5"]); B["year"] = yr
    def hit(c):
        a = issues.get(c, []); return int(((a > np.datetime64(t)) & (a <= np.datetime64(t1))).any())
    B["y"] = [hit(c) for c in B.index]
    rows.append(B.reset_index().rename(columns={"index": "cnpj"}))
P = pd.concat(rows, ignore_index=True); P = P[P["n5"] >= 1]
P.to_csv(f"{OUT}/br_deb_panel.csv.gz", index=False)
O = P[P["outstanding"] > 0].copy()
rep["panel"] = {"issuer_years": len(P), "with_outstanding": len(O), "events": int(O["y"].sum()), "base_rate": round(O["y"].mean(), 4), "issuers": int(O["cnpj"].nunique())}
O["bk"] = pd.cut(O["first_mat"], [-1, 3, 6, 12, 24, 1e9], labels=["0-3m", "3-6m", "6-12m", "12-24m", ">24m"])
rep["rate_by_first_maturity"] = O.groupby("bk")["y"].agg(["count", "mean"]).round(3).to_dict("index")
rep["rate_wall12_vs_not"] = {"wall12": round(O[O["w12"] > 0]["y"].mean(), 4), "no_wall": round(O[O["w12"] == 0]["y"].mean(), 4), "n_wall": int((O["w12"] > 0).sum()), "n_no": int((O["w12"] == 0).sum())}
ns = O[O["n5"] <= 3]; rep["non_serial"] = {"n": len(ns), "rate": round(ns["y"].mean(), 4), "wall12": round(ns[ns["w12"] > 0]["y"].mean(), 4), "no_wall": round(ns[ns["w12"] == 0]["y"].mean(), 4), "n_wall": int((ns["w12"] > 0).sum())}
FE = ["n5", "m_last", "l5", "tenor", "is476", "lo", "lw12", "share12", "first_mat", "n_out", "w6", "w24"]; FB = ["n5", "m_last", "l5", "tenor", "is476"]
def fit(tr, te, cols):
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=1.0, random_state=0).fit(tr[cols], tr["y"]).predict_proba(te[cols])[:, 1]
res = {}; sc = []
for ty in range(2018, 2026):
    tr = O[O["year"] < ty]; te = O[O["year"] == ty].copy()
    if te["y"].sum() < 20 or tr["y"].sum() < 60: continue
    te["p_full"] = fit(tr, te, FE); te["p_cad"] = fit(tr, te, FB); te["p_naive"] = -te["m_last"].fillna(999)
    sc.append(te); res[ty] = {"n": len(te), "events": int(te["y"].sum()), **{k: round(roc_auc_score(te["y"], te[k]), 3) for k in ["p_naive", "p_cad", "p_full"]}}; print(ty, res[ty], flush=True)
S = pd.concat(sc); rep["walk_forward"] = res
rep["pooled_auc"] = {k: round(roc_auc_score(S["y"], S[k]), 3) for k in ["p_naive", "p_cad", "p_full"]}
nsS = S[S["n5"] <= 3]; rep["non_serial_pooled_auc"] = {k: round(roc_auc_score(nsS["y"], nsS[k]), 3) for k in ["p_naive", "p_cad", "p_full"]} if nsS["y"].sum() > 20 else None
S["dec"] = pd.qcut(S["p_full"].rank(method="first"), 10, labels=False) + 1
rep["deciles"] = S.groupby("dec")["y"].agg(["count", "sum", "mean"]).round(3).reset_index().to_dict("records")
rep["top"] = {}
for q in [0.05, 0.10, 0.25]:
    th = S["p_full"].quantile(1 - q); tt = S[S["p_full"] >= th]; rep["top"][f"top{int(q*100)}%"] = {"rate": round(tt["y"].mean(), 3), "lift": round(tt["y"].mean() / S["y"].mean(), 2), "capture": round(tt["y"].sum() / S["y"].sum(), 3), "n": len(tt)}
# --- distress: acceleration ("vencimento antecipado") and listed recovery filings
acc = df[df["motivo"].str.contains("antecipad", case=False) & df["motivo"].str.contains("venc", case=False)]
rep["acceleration_events"] = {"instruments": len(acc), "issuers": int(acc["cnpj"].nunique()), "by_year": acc["exit"].dt.year.value_counts().sort_index().to_dict(), "motivos": acc["motivo"].value_counts().head(5).to_dict()}
acc_first = acc.groupby("cnpj")["exit"].min()
O["acc_d"] = O["cnpj"].map(acc_first); t0 = pd.to_datetime(O["year"].astype(str) + "-01-01")
O["yd"] = ((O["acc_d"] > t0) & (O["acc_d"] <= t0 + pd.DateOffset(years=1))).astype(int); Od = O[~(O["acc_d"] <= t0)]
rep["distress_panel"] = {"n": len(Od), "events": int(Od["yd"].sum())}
if Od["yd"].sum() >= 15:
    a = {}
    for c, sign in [("w12", 1), ("lw12", 1), ("share12", 1), ("first_mat", -1), ("m_last", -1), ("n5", 1), ("lo", 1)]:
        s = Od[c]; mm = s.notna()
        if mm.sum() > 100: a[c] = round(roc_auc_score(Od.loc[mm, "yd"], sign * s[mm]), 3)
    rep["distress_auc_single"] = a
    rep["distress_rate_wall"] = {"wall12": round(Od[Od["w12"] > 0]["yd"].mean(), 4), "no_wall": round(Od[Od["w12"] == 0]["yd"].mean(), 4), "n_wall": int((Od["w12"] > 0).sum())}
rj = pd.read_csv(f"{HERE}/br_rj_companies.csv"); rj["cnpj"] = rj["CNPJ_Companhia"].str.replace(r"\D", "", regex=True).str.zfill(14); rj["d"] = pd.to_datetime(rj["Data_Entrega"], errors="coerce")
rjd = rj.groupby("cnpj")["d"].min(); O["rj_d"] = O["cnpj"].map(rjd); Or = O[~(O["rj_d"] <= t0)].copy(); t1_ = pd.to_datetime(Or["year"].astype(str) + "-01-01")
Or["yr"] = ((Or["rj_d"] > t1_) & (Or["rj_d"] <= t1_ + pd.DateOffset(years=1))).astype(int)
rep["recovery_panel"] = {"n": len(Or), "events": int(Or["yr"].sum())}
if Or["yr"].sum() >= 10:
    rep["recovery_auc_single"] = {c: round(roc_auc_score(Or["yr"], s * Or[c].fillna(0)) if False else roc_auc_score(Or["yr"], Or[c].fillna(0) * sg), 3) for c, sg in [("w12", 1), ("lw12", 1), ("share12", 1), ("lo", 1), ("n5", 1)]}
json.dump(rep, open(f"{OUT}/deb_analysis.json", "w"), indent=1, default=str, ensure_ascii=False)
print(json.dumps({k: rep[k] for k in ["vs_fre", "panel", "rate_by_first_maturity", "rate_wall12_vs_not", "non_serial", "pooled_auc", "top", "acceleration_events", "distress_auc_single", "recovery_auc_single"] if k in rep}, indent=1, default=str, ensure_ascii=False)[:5000])
