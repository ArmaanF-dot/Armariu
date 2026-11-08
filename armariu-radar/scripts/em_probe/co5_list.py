"""Colombia 'likely to borrow more' list from latest accounts (model validated in co_borrow_test)."""
import json, re, sys, time, urllib.request, urllib.parse, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as H
warnings.filterwarnings("ignore")
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
log = lambda *a: print(*a, flush=True)
def get(u):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=300) as r: return r.read().decode("utf8", "replace")
        except Exception as e: log("retry", repr(e)[:80]); time.sleep(5 * (i + 1))
    return "[]"
def soda(q): return json.loads(get("https://www.datos.gov.co/resource/6hqw-m3dm.json?" + urllib.parse.urlencode(q)))
F = pd.read_csv(f"{OUT}/co_features.csv.gz", usecols=["nit", "year", "A", "L", "FC", "R", "E", "lev", "cur", "cash_cl", "roa", "opm", "int_burden", "cl_share", "loss", "asset_turn", "log_a", "rev_growth", "asset_growth", "usd_assets_m"])
F["nit"] = F["nit"].astype("int64").astype(str); F = F[F.year >= 2018].drop_duplicates(["nit", "year"])
FE = ["rev_growth", "asset_growth", "lev", "cur", "cash_cl", "roa", "opm", "int_burden", "cl_share", "loss", "asset_turn", "log_a"]
F[FE] = F[FE].replace([np.inf, -np.inf], np.nan)
N = F[["nit", "year", "A", "L", "FC"]].copy(); N["year"] -= 1; N = N.rename(columns={"A": "A1", "L": "L1", "FC": "FC1"})
D = F.merge(N, on=["nit", "year"]); D = D[(D.A > 0) & (D.A1 > 0) & (D.L > 0)]
D["y_debt"] = (((D.L1 - D.L) / D.A > 0.10) & (D.L1 > D.L * 1.25)).astype(int); D["fc_up"] = ((D.FC > 0) & (D.FC1 > D.FC * 1.3)).astype(int)
m1 = H(max_depth=3, learning_rate=0.05, max_iter=150).fit(D[FE], D.y_debt); m2 = H(max_depth=3, learning_rate=0.05, max_iter=150).fit(D[FE], D.fc_up)
L = F[F.year == F.year.max()].copy(); log("latest year", F.year.max(), len(L))
L["p_debt"] = m1.predict_proba(L[FE])[:, 1]; L["p_fc"] = m2.predict_proba(L[FE])[:, 1]
L["score"] = (L.p_debt.rank(pct=True) + L.p_fc.rank(pct=True)) / 2
ok = (L.usd_assets_m >= 5) & (L.cur >= 1.0) & (L.lev < 1.0) & (L.roa >= -0.02) & (L.E > 0)
log("pass screen", int(ok.sum()))
top = L[ok].sort_values("score", ascending=False).head(150).copy()
lst = ",".join("'" + n + "'" for n in top.nit)
rows = soda({"$select": "nit,concepto,valor", "$where": f"fecha_corte='{int(F.year.max())}-12-31T00:00:00.000' AND nit in ({lst}) AND (concepto like 'Raz%n social%' OR concepto like 'Clasificaci%n Industrial Internacional Uniforme Versi%n 4 A.C%' OR concepto like 'Ciudad de la direcci%n del domicilio')", "$limit": 50000})
c = pd.DataFrame(rows); c["k"] = c.concepto.map(lambda s: "name" if re.match(r"Raz", s) else "ciiu" if "A.C" in s else "city")
c = c.pivot_table(index="nit", columns="k", values="valor", aggfunc="first").reset_index(); c["nit"] = c.nit.astype(str)
top = top.merge(c, on="nit", how="left")
def why(r):
    p = []
    if r.rev_growth > 0.15: p.append(f"revenue grew {r.rev_growth*100:.0f}%")
    if r.asset_growth > 0.15: p.append(f"assets grew {r.asset_growth*100:.0f}%")
    if r.int_burden > 0.02: p.append("already pays interest on debt")
    p.append(f"leverage {r.lev:.2f}"); p.append(f"current ratio {r.cur:.1f}")
    return "; ".join(p)
top["reasons"] = top.apply(why, axis=1)
top["usd_assets_m"] = top.usd_assets_m.round(1)
top["revenue_usd_m"] = (top.R / 4000 / 1e3).round(1)
out = top[["nit", "name", "ciiu", "city", "usd_assets_m", "revenue_usd_m", "lev", "cur", "roa", "rev_growth", "p_debt", "p_fc", "score", "reasons"]].round(3)
out.to_csv(f"{OUT}/co_likely_borrowers.csv", index=False)
readme = pd.DataFrame({"Read me": [
 "Colombia: companies whose reported debt has tended to grow (list built 8 Nov 2026 from Supersociedades NIIF accounts for 2025; CC BY-SA 4.0).",
 "Ranking = average of two models: chance liabilities rise >25% next year, chance finance costs rise >30% next year. Trained on 2018-24 accounts.",
 "Held-out test 2020-24 (trained only on earlier years): top 10% hit 21-49% vs 14-26% base for liabilities; 40-58% vs 16-34% for finance costs (about 1.5-2.5x).",
 "Screen applied: assets over USD 5m, current ratio 1.0+, leverage under 1, positive equity, return on assets above -2%. This is a simple screen, not the full distress score.",
 "Limits: outcomes are proxies for borrowing; model mostly finds companies that already carry debt; no loan-level confirmation; not a refinancing prediction.",
 "Amounts assume Colombian pesos at 4,000 per USD and thousands units; check before quoting."]})
with pd.ExcelWriter(f"{OUT}/colombia-likely-borrowers.xlsx") as xw:
    readme.to_excel(xw, sheet_name="Read me", index=False); out.to_excel(xw, sheet_name="Likely borrowers", index=False)
log(out.head(15).to_string())
