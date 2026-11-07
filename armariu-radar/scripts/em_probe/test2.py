"""Second probe: is there enough labelled history to backtest in Colombia and Brazil?"""
import io, json, os, sys, urllib.request, urllib.parse, zipfile, collections, time
import pandas as pd
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u, timeout=300):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            print("retry", u, e, flush=True); time.sleep(5)
    raise RuntimeError(u)
rep = {}
def soda(ds, q):
    u = f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)
    return json.loads(get(u))
# ---------- Colombia ----------
co = {}
for ds in ["msu7-rjqd", "pfdp-zks5", "prwj-nzxa", "6hqw-m3dm", "c82u-588k", "jbjy-vk9h"]:
    try:
        meta = json.loads(get(f"https://www.datos.gov.co/api/views/{ds}.json"))
        cols = [c["fieldName"] for c in meta.get("columns", [])]
        cnt = soda(ds, {"$select": "count(*)"})
        sample = soda(ds, {"$limit": 3})
        co[ds] = {"name": meta.get("name"), "rows": cnt, "cols": cols, "sample": sample,
                  "license": (meta.get("license") or {}).get("name"), "attribution": meta.get("attribution"),
                  "rowsUpdatedAt": meta.get("rowsUpdatedAt")}
        print("CO", ds, meta.get("name"), cnt, flush=True)
    except Exception as e:
        co[ds] = {"err": repr(e)[:300]}
# Insolvency: pull whole table (small) to profile years and types
try:
    rows = soda("msu7-rjqd", {"$limit": 200000})
    df = pd.DataFrame(rows); df.to_csv(f"{OUT}/co_insolvencia.csv", index=False)
    co["insolv_profile"] = {"n": len(df), "cols": list(df.columns),
        "value_counts": {c: df[c].astype(str).value_counts().head(15).to_dict() for c in df.columns if df[c].nunique() < 60}}
    datecols = [c for c in df.columns if "fecha" in c.lower()]
    for c in datecols:
        y = pd.to_datetime(df[c], errors="coerce").dt.year.value_counts().sort_index()
        co["insolv_profile"]["years_" + c] = {int(k): int(v) for k, v in y.items()}
except Exception as e:
    co["insolv_profile"] = {"err": repr(e)[:300]}
# Financials: count by period and companies
for ds in ["pfdp-zks5", "6hqw-m3dm"]:
    try:
        cols = co[ds]["cols"]
        per = [c for c in cols if "periodo" in c or "fecha_corte" in c or c.startswith("a_o") or "ano" in c]
        nit = [c for c in cols if "nit" in c]
        g = {}
        for p in per[:2]:
            g[p] = soda(ds, {"$select": f"{p}, count(*)", "$group": p, "$limit": 100})
        if nit:
            g["distinct_nit"] = soda(ds, {"$select": f"count(distinct {nit[0]})"})
        co[ds]["profile"] = g
    except Exception as e:
        co[ds]["profile_err"] = repr(e)[:300]
rep["colombia"] = co
json.dump(rep, open(f"{OUT}/test2.json", "w"), indent=1, default=str)
# ---------- Brazil CVM ----------
br = {"ipe": {}}
rj_rows = []
for y in range(2016, 2027):
    try:
        z = zipfile.ZipFile(io.BytesIO(get(f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{y}.zip")))
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        d = pd.read_csv(z.open(name), sep=";", encoding="latin-1", dtype=str)
        cats = d["Categoria"].value_counts()
        rj = d[d["Categoria"].str.contains("Recupera|Fal[eê]ncia|Liquida", case=False, na=False) |
               d.get("Assunto", pd.Series("", index=d.index)).fillna("").str.contains("recupera..o judicial|recupera..o extrajudicial", case=False, regex=True)]
        rj_rows.append(rj)
        br["ipe"][y] = {"docs": len(d), "companies": int(d["CNPJ_Companhia"].nunique()),
                        "rj_docs": len(rj), "rj_companies": int(rj["CNPJ_Companhia"].nunique()),
                        "top_cats": cats.head(25).to_dict()}
        if y == 2024: br["ipe_cols"] = list(d.columns)
        print("BR IPE", y, len(d), len(rj), flush=True)
    except Exception as e:
        br["ipe"][y] = {"err": repr(e)[:300]}
if rj_rows:
    allrj = pd.concat(rj_rows); allrj.to_csv(f"{OUT}/br_rj_docs.csv", index=False)
    first = allrj.groupby(["CNPJ_Companhia", "Nome_Companhia"])["Data_Entrega"].min().reset_index()
    first["year"] = first["Data_Entrega"].str[:4]
    br["rj_companies_total"] = len(first)
    br["rj_first_by_year"] = first["year"].value_counts().sort_index().to_dict()
    first.to_csv(f"{OUT}/br_rj_companies.csv", index=False)
try:
    cad = pd.read_csv(io.BytesIO(get("https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv")), sep=";", encoding="latin-1", dtype=str)
    br["cad"] = {"n": len(cad), "sit": cad["SIT"].value_counts().to_dict(), "sit_emissor": cad.get("SIT_EMISSOR", pd.Series()).value_counts().to_dict()}
except Exception as e:
    br["cad"] = {"err": repr(e)[:300]}
try:
    z = zipfile.ZipFile(io.BytesIO(get("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2023.zip")))
    br["dfp_2023_files"] = z.namelist()
    bp = [n for n in z.namelist() if "BPP_con" in n][0]
    d = pd.read_csv(z.open(bp), sep=";", encoding="latin-1", dtype=str)
    br["dfp_2023_bpp_con"] = {"rows": len(d), "companies": int(d["CNPJ_CIA"].nunique())}
except Exception as e:
    br["dfp_err"] = repr(e)[:300]
rep["brazil"] = br
json.dump(rep, open(f"{OUT}/test2.json", "w"), indent=1, default=str)
print("done")
