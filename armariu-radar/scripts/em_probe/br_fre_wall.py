"""Brazil listed issuers: free debt maturity data (CVM FRE) and a maturity-wall test against recovery (RJ) filings."""
import io, json, re, sys, time, urllib.request, zipfile, warnings
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u):
    for i in range(3):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=900).read()
        except Exception as e: print("retry", u, repr(e)[:80], flush=True); time.sleep(5)
num = lambda s: pd.to_numeric(s.astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
rep = {"years": {}}
fre_rows = []; dfp = []
for y in range(2016, 2026):
    try:
        z = zipfile.ZipFile(io.BytesIO(get(f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_{y}.zip")))
        parts = []
        for n in ["outro_valor_mobiliario", "titulo_exterior"]:
            nm = f"fre_cia_aberta_{n}_{y}.csv"
            if nm in z.namelist():
                d = pd.read_csv(z.open(nm), sep=";", encoding="latin-1", dtype=str, engine="python", on_bad_lines="skip"); d["src"] = n
                d = d.rename(columns={"Valor_Nominal": "Valor"}) if "Valor_Nominal" in d.columns and "Valor" not in d.columns else d
                parts.append(d[["CNPJ_Companhia", "Data_Referencia", "Versao", "Valor_Mobiliario", "Data_Emissao", "Data_Vencimento", "Saldo_Devedor", "src"]])
        F = pd.concat(parts); F["year"] = y; fre_rows.append(F)
        z2 = zipfile.ZipFile(io.BytesIO(get(f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{y}.zip")))
        for kind, nm in [("A", "BPA_con"), ("P", "BPP_con"), ("A", "BPA_ind"), ("P", "BPP_ind")]:
            d = pd.read_csv(z2.open(f"dfp_cia_aberta_{nm}_{y}.csv"), sep=";", encoding="latin-1", dtype=str)
            d = d[d["ORDEM_EXERC"].str.contains("LTIMO", na=False)]
            d["kind"] = nm; d["year"] = y; dfp.append(d[["CNPJ_CIA", "CD_CONTA", "DS_CONTA", "VL_CONTA", "ESCALA_MOEDA", "kind", "year", "DT_REFER"]])
        rep["years"][y] = {"fre_rows": len(F)}; print(y, len(F), flush=True)
    except Exception as e: rep["years"][y] = {"err": repr(e)[:200]}; print(y, "fail", e, flush=True)
F = pd.concat(fre_rows)
F["cnpj"] = F["CNPJ_Companhia"].str.replace(r"\D", "", regex=True)
F = F.sort_values("Versao", key=lambda s: pd.to_numeric(s, errors="coerce")).groupby(["cnpj", "year", "Valor_Mobiliario", "Data_Emissao", "Data_Vencimento", "src"], dropna=False).tail(1)
F["ref"] = pd.to_datetime(F["Data_Referencia"], errors="coerce"); F["mat"] = pd.to_datetime(F["Data_Vencimento"], errors="coerce"); F["bal"] = num(F["Saldo_Devedor"])
F["is_debt"] = F["Valor_Mobiliario"].fillna("").str.contains("Deb|Nota|Bond|Commercial|Cédula|Letra|CCB|Certificado de Receb", case=False, regex=True)
Fd = F[F["is_debt"]]
rep["fre"] = {"debt_rows": len(Fd), "with_maturity": int(Fd["mat"].notna().sum()), "with_balance": int(Fd["bal"].notna().sum()), "companies": int(Fd["cnpj"].nunique()),
              "companies_with_future_maturity_by_year": Fd[Fd["mat"] > Fd["ref"]].groupby("year")["cnpj"].nunique().to_dict(),
              "instrument_types": Fd["Valor_Mobiliario"].value_counts().head(10).to_dict()}
print(rep["fre"], flush=True)
Fd.to_csv(f"{OUT}/br_fre_debt_rows.csv.gz", index=False)
# DFP summary per company-year
D = pd.concat(dfp); D["cnpj"] = D["CNPJ_CIA"].str.replace(r"\D", "", regex=True)
D["v"] = pd.to_numeric(D["VL_CONTA"], errors="coerce") * D["ESCALA_MOEDA"].map(lambda s: 1000 if str(s).upper().startswith("MIL") else 1)
def acct(code, desc=None):
    d = D[D["CD_CONTA"] == code]
    if desc: d = d[d["DS_CONTA"].str.contains(desc, case=False, na=False)]
    d = d.assign(pref=(d["kind"].str.endswith("con")).astype(int)).sort_values("pref", ascending=False)
    return d.groupby(["cnpj", "year"])["v"].first()
P = pd.DataFrame({"assets": acct("1"), "cash": acct("1.01.01"), "cur_debt": acct("2.01.04", "Empr"), "lt_debt": acct("2.02.01", "Empr"), "equity": acct("2.03"), "cur_liab": acct("2.01"), "cur_assets": acct("1.01")}).reset_index()
w12 = Fd[(Fd["mat"] > Fd["ref"]) & (Fd["mat"] <= Fd["ref"] + pd.Timedelta(days=366))].groupby(["cnpj", "year"])["bal"].sum().rename("fre_wall12")
w24 = Fd[(Fd["mat"] > Fd["ref"]) & (Fd["mat"] <= Fd["ref"] + pd.Timedelta(days=731))].groupby(["cnpj", "year"])["bal"].sum().rename("fre_wall24")
tot = Fd[Fd["mat"] > Fd["ref"]].groupby(["cnpj", "year"])["bal"].sum().rename("fre_out")
n_inst = Fd[Fd["mat"] > Fd["ref"]].groupby(["cnpj", "year"]).size().rename("fre_n")
P = P.merge(w12.reset_index(), how="left").merge(w24.reset_index(), how="left").merge(tot.reset_index(), how="left").merge(n_inst.reset_index(), how="left")
for c in ["fre_wall12", "fre_wall24", "fre_out", "fre_n"]: P[c] = P[c].fillna(0)
P = P[P["assets"] > 0]
sf = lambda a, b: (a / b).replace([np.inf, -np.inf], np.nan)
P["lev"] = sf(P["cur_debt"].fillna(0) + P["lt_debt"].fillna(0), P["assets"]); P["cd_cash"] = sf(P["cur_debt"], P["cash"].where(P["cash"] > 0))
P["wall_cash"] = sf(P["fre_wall12"], P["cash"].where(P["cash"] > 0)); P["wall_assets"] = sf(P["fre_wall12"], P["assets"]); P["cd_assets"] = sf(P["cur_debt"], P["assets"])
P["cur_ratio"] = sf(P["cur_assets"], P["cur_liab"]); P["eq_a"] = sf(P["equity"], P["assets"])
# labels: recovery filing within the next 12 months (cutoff 1 Jun Y+1 as accounts are public)
rj = pd.read_csv(f"{sys.argv[2]}/br_rj_companies.csv"); rj["cnpj"] = rj["CNPJ_Companhia"].str.replace(r"\D", "", regex=True); rj["d"] = pd.to_datetime(rj["Data_Entrega"], errors="coerce")
rjd = rj.groupby("cnpj")["d"].min()
P["rj_d"] = P["cnpj"].map(rjd)
P["prior"] = P["rj_d"] < pd.to_datetime((P["year"] + 1).astype(str) + "-06-01")
P["y"] = ((P["rj_d"] >= pd.to_datetime((P["year"] + 1).astype(str) + "-06-01")) & (P["rj_d"] < pd.to_datetime((P["year"] + 2).astype(str) + "-06-01"))).astype(int)
Q = P[~P["prior"] & (P["year"] <= 2024)]
rep["panel"] = {"company_years": len(Q), "events": int(Q["y"].sum()), "base_rate": round(Q["y"].mean(), 4), "companies": int(Q["cnpj"].nunique()), "with_fre_wall": int((Q["fre_wall12"] > 0).sum())}
aucs = {}
for c in ["lev", "cd_cash", "cd_assets", "wall_cash", "wall_assets", "fre_wall12", "cur_ratio", "eq_a"]:
    s = Q[c]; m = s.notna()
    if Q.loc[m, "y"].sum() >= 15 and m.sum() > 300:
        a = roc_auc_score(Q.loc[m, "y"], s[m] if c not in ("cur_ratio", "eq_a") else -s[m]); aucs[c] = {"auc": round(a, 3), "n": int(m.sum()), "events": int(Q.loc[m, "y"].sum())}
rep["auc_single"] = aucs
# rate when a wall exists relative to cash
Q2 = Q[Q["cash"] > 0].copy(); Q2["wall_flag"] = Q2["wall_cash"] > 1
rep["rate_wall_gt_cash"] = Q2.groupby("wall_flag")["y"].agg(["count", "sum", "mean"]).round(4).to_dict("index")
Q2["cd_flag"] = Q2["cd_cash"] > 1
rep["rate_curdebt_gt_cash"] = Q2.groupby("cd_flag")["y"].agg(["count", "sum", "mean"]).round(4).to_dict("index")
both = Q2[(Q2["fre_out"] > 0)]
rep["rate_among_with_fre_debt"] = {"n": len(both), "events": int(both["y"].sum()), "rate_wall_gt_cash": round(both[both["wall_flag"]]["y"].mean(), 4) if both["wall_flag"].any() else None, "rate_other": round(both[~both["wall_flag"]]["y"].mean(), 4)}
# logistic: does FRE wall add to DFP current debt?
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict, GroupKFold
X0 = Q[["lev", "cd_cash", "cd_assets", "cur_ratio", "eq_a"]].replace([np.inf, -np.inf], np.nan); X0 = X0.clip(X0.quantile(.02), X0.quantile(.98), axis=1).fillna(X0.median())
X1 = X0.copy(); X1["wall_assets"] = Q["wall_assets"].fillna(0).clip(0, 1); X1["wall_cash"] = Q["wall_cash"].fillna(0).clip(0, 20)
for nm, X in [("dfp_only", X0), ("dfp_plus_fre_wall", X1)]:
    pr = cross_val_predict(LogisticRegression(max_iter=2000, class_weight="balanced", C=0.3), (X - X.mean()) / X.std(), Q["y"], cv=GroupKFold(5), groups=Q["cnpj"], method="predict_proba")[:, 1]
    rep.setdefault("cv_auc", {})[nm] = round(roc_auc_score(Q["y"], pr), 3)
# current maturity table (latest FRE year available per company)
lat = Fd[(Fd["mat"] > pd.Timestamp("2026-11-01"))].copy()
lat = lat.sort_values("year").groupby(["cnpj", "Valor_Mobiliario", "Data_Emissao", "Data_Vencimento"], dropna=False).tail(1)
cash_latest = P.sort_values("year").groupby("cnpj").tail(1).set_index("cnpj")[["year", "cash", "assets", "cur_debt", "lt_debt"]].rename(columns={"year": "dfp_year"})
nm = F.sort_values("year").groupby("cnpj")["CNPJ_Companhia"].last()
lat = lat.merge(cash_latest, left_on="cnpj", right_index=True, how="left")
lat["maturity_months"] = ((lat["mat"] - pd.Timestamp("2026-11-07")).dt.days / 30.4).round(1)
cols = ["CNPJ_Companhia", "Valor_Mobiliario", "Data_Emissao", "Data_Vencimento", "maturity_months", "bal", "year", "cash", "assets", "dfp_year"]
lat[cols].rename(columns={"bal": "outstanding_brl", "year": "fre_year", "cash": "latest_cash_brl", "assets": "latest_assets_brl"}).sort_values("maturity_months").to_csv(f"{OUT}/br_debt_maturities_current.csv", index=False)
rep["current_table"] = {"instruments": len(lat), "companies": int(lat["cnpj"].nunique()), "maturing_within_12m_companies": int(lat[lat["maturity_months"] <= 12]["cnpj"].nunique()), "fre_years_loaded": sorted(int(y) for y in F["year"].unique())}
json.dump(rep, open(f"{OUT}/br_fre_wall.json", "w"), indent=1, default=str, ensure_ascii=False)
print(json.dumps({k: rep[k] for k in ["fre", "panel", "auc_single", "rate_wall_gt_cash", "rate_curdebt_gt_cash", "cv_auc"]}, indent=1, default=str)[:3000])
