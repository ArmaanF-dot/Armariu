import io, json, sys, zipfile, urllib.request, time
import pandas as pd
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u):
    for i in range(3):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600).read()
        except Exception as e: print("retry", u, e, flush=True); time.sleep(5)
rep = {}
z = zipfile.ZipFile(io.BytesIO(get("https://dados.cvm.gov.br/dados/OFERTA/DISTRIB/DADOS/oferta_distribuicao.zip")))
rep["files"] = z.namelist()
for n in z.namelist():
    if not n.endswith(".csv"): continue
    d = pd.read_csv(z.open(n), sep=";", encoding="latin-1", dtype=str, low_memory=False)
    d.head(300).to_csv(f"{OUT}/br_offer_{n[:30]}.csv", index=False)
    info = {"rows": len(d), "cols": list(d.columns)}
    for c in d.columns:
        if d[c].nunique() < 40: info.setdefault("small_vc", {})[c] = d[c].value_counts().head(12).to_dict()
    for c in d.columns:
        if "Data" in c or "data" in c: info.setdefault("date_ranges", {})[c] = [str(d[c].dropna().min()), str(d[c].dropna().max())]
    info["sample"] = d.head(3).to_dict("records")
    rep[n] = info; print(n, len(d), flush=True)
json.dump(rep, open(f"{OUT}/br_probe.json", "w"), indent=1, ensure_ascii=False, default=str)
# DFP / ITR account codes for debt
z2 = zipfile.ZipFile(io.BytesIO(get("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2023.zip")))
b = pd.read_csv(z2.open([n for n in z2.namelist() if "BPP_con" in n][0]), sep=";", encoding="latin-1", dtype=str)
codes = b.groupby(["CD_CONTA", "DS_CONTA"]).size().reset_index(name="n").sort_values("n", ascending=False)
codes.head(120).to_csv(f"{OUT}/br_bpp_codes.csv", index=False)
a = pd.read_csv(z2.open([n for n in z2.namelist() if "BPA_con" in n][0]), sep=";", encoding="latin-1", dtype=str)
a.groupby(["CD_CONTA", "DS_CONTA"]).size().reset_index(name="n").sort_values("n", ascending=False).head(60).to_csv(f"{OUT}/br_bpa_codes.csv", index=False)
print("done")
