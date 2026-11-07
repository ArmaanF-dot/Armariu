"""Brazil refinancing radar: score live debenture issuers for a new issue in the next 12 months."""
import json, re, sys, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
OUT = sys.argv[1]
df = pd.read_csv(f"{OUT}/br_debentures_registry.csv.gz", dtype=str)
for c in ["issue", "mat", "exit"]: df[c] = pd.to_datetime(df[c], errors="coerce")
for c in ["amt0", "qty", "vn0"]: df[c] = pd.to_numeric(df[c], errors="coerce")
df["exempt476"] = df["exempt476"].astype(str).str.lower().eq("true")
df["kind"] = df["motivo"].fillna("").str.replace(r"^\d\d/\d\d/\d{4} - ", "", regex=True).str.upper().str.strip()
df = df.dropna(subset=["issue", "mat"]); df = df[df["issue"].between("1990-01-01", "2026-12-31") & df["mat"].between("1990-01-01", "2080-01-01")].copy()
ex = (df["status"] == "Excluído") & df["exit"].notna() & df["kind"].str.contains("RESGATE|VENCIMENTO|CANCEL|CONVERS|AMORTIZ")
df["exited"] = df["exit"].where(ex); df["mat_reliable"] = (df["status"] == "Registrado") | (df["kind"] == "VENCIMENTO"); df["amt"] = df["amt0"].fillna(0)
df["securitiser"] = df["Empresa"].str.contains("SECURITIZ|CREDITOS|RECEBIVEIS|FIDC|FUNDO", case=False, na=False)
issues = {k: np.sort(v.values) for k, v in df.groupby("cnpj")["issue"]}
def feats(t):
    t1 = t + pd.DateOffset(years=1); t2 = t + pd.DateOffset(years=2); t6 = t + pd.DateOffset(months=6)
    past = df[df["issue"] < t]; act = past[past["issue"] >= t - pd.DateOffset(years=5)]
    out = past[(past["mat"] >= t) & ~(past["exited"] < t) & past["mat_reliable"]]
    g = act.groupby("cnpj"); B = pd.DataFrame({"n5": g.size()})
    B["last_issue"] = g["issue"].max(); B["amt5"] = g["amt"].sum(); B["tenor"] = (act["mat"] - act["issue"]).dt.days.groupby(act["cnpj"]).mean() / 30.4
    B["is476"] = act["exempt476"].groupby(act["cnpj"]).mean()
    og = out.groupby("cnpj"); B["outstanding"] = og["amt"].sum(); B["n_out"] = og.size(); B["first_mat"] = (og["mat"].min() - t).dt.days / 30.4
    B["w12"] = out[out["mat"] < t1].groupby("cnpj")["amt"].sum(); B["w6"] = out[out["mat"] < t6].groupby("cnpj")["amt"].sum(); B["w24"] = out[out["mat"] < t2].groupby("cnpj")["amt"].sum()
    B = B.fillna({"outstanding": 0, "n_out": 0, "w12": 0, "w6": 0, "w24": 0})
    B["m_last"] = (t - B["last_issue"]).dt.days / 30.4; B["share12"] = B["w12"] / B["outstanding"].replace(0, np.nan)
    B["lo"] = np.log1p(B["outstanding"]); B["lw12"] = np.log1p(B["w12"]); B["l5"] = np.log1p(B["amt5"])
    # typical gap between issues
    gaps = {c: np.median(np.diff(a).astype("timedelta64[D]").astype(float)) / 30.4 for c, a in ((c, issues[c][issues[c] < np.datetime64(t)]) for c in B.index) if len(a) >= 3}
    B["gap_months"] = pd.Series(gaps)
    return B[B["n5"] >= 1]
FE = ["n5", "m_last", "l5", "tenor", "is476", "lo", "lw12", "share12", "first_mat", "n_out", "w6", "w24"]
def label(B, t):
    t1 = t + pd.DateOffset(years=1)
    return np.array([int(((issues.get(c, []) > np.datetime64(t)) & (issues.get(c, []) <= np.datetime64(t1))).any()) for c in B.index])
panel = []
for yr in range(2013, 2026):
    t = pd.Timestamp(yr, 1, 1); B = feats(t); B = B[B["outstanding"] > 0].copy(); B["year"] = yr; B["y"] = label(B, t); panel.append(B.reset_index().rename(columns={"index": "cnpj"}))
P = pd.concat(panel, ignore_index=True)
def fit(tr): return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=1.0, random_state=0).fit(tr[FE], tr["y"])
rep = {"panel": len(P), "events": int(P["y"].sum())}
# walk-forward precision at top 25/50/100
wf = {}; sc = []
for ty in range(2019, 2026):
    tr = P[P["year"] < ty]; te = P[P["year"] == ty].copy(); te["p"] = fit(tr).predict_proba(te[FE])[:, 1]; sc.append(te)
    r = {"n": len(te), "base": round(te["y"].mean(), 3), "auc": round(roc_auc_score(te["y"], te["p"]), 3)}
    for k in (25, 50, 100):
        top = te.sort_values("p", ascending=False).head(k); r[f"top{k}_hit"] = round(top["y"].mean(), 3)
    top_ns = te[~te["cnpj"].isin(df.loc[df["securitiser"], "cnpj"])].sort_values("p", ascending=False).head(50); r["top50_ex_securitisers_hit"] = round(top_ns["y"].mean(), 3)
    wf[ty] = r
rep["walk_forward"] = wf
S = pd.concat(sc); S["dec"] = pd.qcut(S["p"].rank(method="first"), 10, labels=False) + 1
rep["calibration_by_decile"] = S.groupby("dec").agg(avg_p=("p", "mean"), hit=("y", "mean"), n=("y", "size")).round(3).reset_index().to_dict("records")
# score today
t0 = pd.Timestamp("2026-11-07"); model = fit(P)
B = feats(t0); B = B[B["outstanding"] > 0].copy(); B["p"] = model.predict_proba(B[FE])[:, 1]
B["pct_rank"] = B["p"].rank(pct=True)
cal = S.groupby(pd.qcut(S["p"], 10, duplicates="drop"))["y"].mean().values
B["hist_hit_rate_decile"] = pd.qcut(B["p"].rank(method="first"), 10, labels=False).map(lambda i: round(float(cal[min(i, len(cal) - 1)]), 2))
live = df[(df["status"] == "Registrado")]
info = live.sort_values("issue").groupby("cnpj").agg(issuer=("Empresa", "last"), lead=("Coordenador Lider", "last"), trustee=("Agente Fiduciario", "last"), indexers=("indice", lambda s: ", ".join(pd.Series(s.dropna()).value_counts().index[:2])),
                                                      security=("Garantia/Especie", lambda s: ", ".join(pd.Series(s.dropna()).value_counts().index[:2])), securitiser=("securitiser", "max"))
B = B.join(info, how="left")
def why(r):
    out = []
    if r["first_mat"] <= 3: out.append(f"first maturity in {r['first_mat']:.0f} months")
    elif r["first_mat"] <= 12: out.append(f"first maturity in {r['first_mat']:.0f} months")
    if r["w12"] > 0: out.append(f"BRL {r['w12']/1e6:,.0f}m of issue size matures within 12 months")
    if pd.notna(r["gap_months"]) and r["gap_months"] <= 12: out.append(f"issues about every {r['gap_months']:.0f} months")
    if r["m_last"] >= 12: out.append(f"last issue {r['m_last']:.0f} months ago")
    if r["n5"] >= 5: out.append(f"{int(r['n5'])} series in 5 years")
    return "; ".join(out)
B["reasons"] = B.apply(why, axis=1)
R = B.reset_index().rename(columns={"index": "cnpj"})
R["last_issue"] = R["last_issue"].dt.date; R["months_since_last_issue"] = R["m_last"].round(0); R["maturing_12m_issue_size_brln"] = (R["w12"] / 1e6).round(0); R["live_issue_size_brln"] = (R["outstanding"] / 1e6).round(0)
cols = ["issuer", "cnpj", "p", "hist_hit_rate_decile", "reasons", "first_mat", "maturing_12m_issue_size_brln", "live_issue_size_brln", "n_out", "n5", "months_since_last_issue", "gap_months", "last_issue", "lead", "trustee", "indexers", "security", "securitiser"]
R = R.sort_values("p", ascending=False)
R["p"] = R["p"].round(2); R["first_mat"] = R["first_mat"].round(1); R["gap_months"] = R["gap_months"].round(0)
R[cols].to_csv(f"{OUT}/br_refi_radar_all.csv", index=False)
oper = R[~R["securitiser"].fillna(False)]
with pd.ExcelWriter(f"{OUT}/brazil-refinancing-radar.xlsx") as xw:
    pd.DataFrame({"Read me": [
        "Live Brazilian debenture issuers scored on 7 Nov 2026 for a NEW debenture issue within 12 months. Source: debentures.com.br registry (free download, terms of automated use not checked).",
        "p = model probability. hist_hit_rate_decile = share of issuers in the same score band that actually re-issued within 12 months in 2019-2025 walk-forward tests. Overall about 22-24% re-issue in 12 months; top 50 hit rate is in the backtest sheet.",
        "A new issue is not proof of a refinancing, and not all issuers want or need a lender: many are repeat issuers that raise in the bond market.",
        "Amounts are ISSUE SIZE in BRL millions, not outstanding balances (the registry's balance fields did not reconcile with CVM filings).",
        "Maturity dates are trusted only for debentures still live or matured as scheduled; early redemptions overwrite the field. Maturity timing is therefore understated for issuers that redeem early.",
        "Securitisers (CRI/CRA vehicles) are listed separately because their issuing is pipeline-driven, not a company funding need. Research list, not advice. Contact the issuer's lead bank or the issuer, not through this sheet."]}).to_excel(xw, sheet_name="Read me", index=False)
    oper[cols].head(100).to_excel(xw, sheet_name="Top 100 operating issuers", index=False)
    oper[oper["w12"] > 0][cols].head(100).to_excel(xw, sheet_name="Maturing in 12m (top 100)", index=False)
    R[R["securitiser"].fillna(False)][cols].head(30).to_excel(xw, sheet_name="Securitisers (top 30)", index=False)
    pd.DataFrame(wf).T.reset_index().rename(columns={"index": "test_year"}).to_excel(xw, sheet_name="Backtest by year", index=False)
rep["scored_today"] = {"issuers": len(R), "operating": len(oper), "with_12m_maturity": int((R["w12"] > 0).sum())}
json.dump(rep, open(f"{OUT}/br_radar.json", "w"), indent=1, default=str, ensure_ascii=False)
print(json.dumps(rep, indent=1, default=str)[:3500]); print(oper[["issuer", "p", "reasons", "lead"]].head(12).to_string())
