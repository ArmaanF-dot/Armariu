import io, json, sys, time, zipfile, urllib.request, re
import pandas as pd
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
rep = {}
def tryget(k, u, n=300000):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
            b = r.read(n); rep[k] = {"url": u, "status": r.status, "ctype": r.headers.get("content-type"), "bytes": len(b), "secs": round(time.time() - t, 1)}
            open(f"{OUT}/brm_{k}.bin", "wb").write(b[:200000]); return b
    except Exception as e:
        rep[k] = {"url": u, "err": repr(e)[:200]}; print(k, rep[k], flush=True)
for k, u in {"debentures_com_br": "https://www.debentures.com.br/", "debentures_exploreosnd": "https://www.debentures.com.br/exploreosnd/consultaadados/emissoesdedebentures/caracteristicas_f.asp",
             "anbima_data": "https://data.anbima.com.br/debentures", "anbima_api": "https://api.anbima.com.br/", "b3_debentures": "https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-fixa/debentures.htm",
             "gov_incentivadas": "https://www.gov.br/economia/pt-br/centrais-de-conteudo/publicacoes/boletins/boletim-de-debentures-incentivadas",
             "cvm_dfp_index": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/", "cvm_itr_index": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/",
             "cvm_fre_index": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/", "cvm_fca": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/", "cvm_inventario": "https://dados.cvm.gov.br/misc/InventarioBasesCVM.pdf",
             "brapi_deb": "https://brapi.dev/api/v2/debentures"}.items():
    tryget(k, u)
    print(k, rep.get(k, {}).get("status"), rep.get(k, {}).get("bytes"), flush=True)
# CVM FRE has debt maturity tables? list zip contents of the latest FRE and DFP
def listzip(u, key):
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600).read()
        z = zipfile.ZipFile(io.BytesIO(b)); rep[key] = {"files": z.namelist()}
        return z
    except Exception as e: rep[key] = {"err": repr(e)[:200]}
z = listzip("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2024.zip", "dfp2024")
z2 = listzip("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_2024.zip", "fre2024")
if z2:
    for n in z2.namelist():
        if re.search(r"(divid|obrig|endivid|hist|emprest|debent|valor_mob|titulo)", n, re.I) and n.endswith(".csv"):
            try:
                d = pd.read_csv(z2.open(n), sep=";", encoding="latin-1", dtype=str, nrows=2000)
                rep["fre2024"][n] = {"cols": list(d.columns)[:30], "sample": d.head(2).to_dict("records")}
            except Exception as e: rep["fre2024"][n] = repr(e)[:100]
# DFP: any note-level debt detail?
if z:
    for n in z.namelist():
        if re.search(r"(nota|DVA|DMPL)", n, re.I) and n.endswith(".csv"):
            rep["dfp2024"].setdefault("note_like", []).append(n)
# Debt notes by maturity bucket: BPP 2.01.04 vs 2.02.01 give current vs non-current only
if z:
    b = pd.read_csv(z.open([n for n in z.namelist() if "BPP_con" in n and n.endswith(".csv")][0]), sep=";", encoding="latin-1", dtype=str)
    c = b[b["CD_CONTA"].isin(["2.01.04", "2.01.04.01", "2.01.04.02", "2.02.01", "2.02.01.01", "2.02.01.02"])]
    rep["dfp2024"]["debt_lines"] = c.groupby(["CD_CONTA", "DS_CONTA"]).size().to_dict().__repr__()
    rep["dfp2024"]["companies_with_debt_split"] = int(c[c["CD_CONTA"].isin(["2.01.04", "2.02.01"])]["CNPJ_CIA"].nunique())
    rep["dfp2024"]["companies_total"] = int(b["CNPJ_CIA"].nunique())
json.dump(rep, open(f"{OUT}/br_mat_probe.json", "w"), indent=1, ensure_ascii=False, default=str)
print("done")
