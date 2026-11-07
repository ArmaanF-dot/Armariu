"""Third probe: firm-level insolvency labels for Colombia and overlap with open financials."""
import io, json, os, re, sys, urllib.request, urllib.parse, time
import pandas as pd
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u, t=300):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as r: return r.read()
        except Exception as e: print("retry", u, e, flush=True); time.sleep(5)
    raise RuntimeError(u)
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)))
rep = {}
# distinct companies per year in Supersociedades financials
try:
    rep["fin_nits_by_year"] = soda("6hqw-m3dm", {"$select": "date_extract_y(fecha_corte) as y, count(distinct nit) as n", "$where": "date_extract_m(fecha_corte)=12", "$group": "y", "$order": "y"})
except Exception as e: rep["fin_nits_err"] = repr(e)[:300]
try:
    rep["caratula_concepts"] = soda("6hqw-m3dm", {"$select": "concepto, count(*)", "$group": "concepto", "$order": "count(*) desc", "$limit": 80})
except Exception as e: rep["concept_err"] = repr(e)[:300]
# RUES status values
try:
    rep["rues_estado"] = soda("c82u-588k", {"$select": "estado_matricula, count(*)", "$group": "estado_matricula", "$limit": 50})
except Exception as e: rep["rues_err"] = repr(e)[:300]
# Supersociedades insolvency base files
links = set()
for page in ["https://www.supersociedades.gov.co/web/procedimientos-de-insolvencia/informes",
             "https://supersociedades.gov.co/web/procedimientos-de-insolvencia/informes"]:
    try:
        h = get(page, 60).decode("utf-8", "ignore")
        open(f"{OUT}/superso_informes.html", "w").write(h)
        for m in re.findall(r'href="([^"]+)"', h):
            if re.search(r"xls|BASE|Base|base", m): links.add(urllib.parse.urljoin(page, m))
        break
    except Exception as e: rep["page_err"] = repr(e)[:300]
rep["xlsx_links"] = sorted(links)
print(rep["xlsx_links"], flush=True)
got = None
for l in sorted(links, reverse=True):
    if "2026" in l or "2025" in l:
        try:
            b = get(l); got = l
            x = pd.read_excel(io.BytesIO(b), sheet_name=None)
            rep["base_file"] = l
            rep["base_sheets"] = {k: {"rows": len(v), "cols": [str(c) for c in v.columns][:40], "head": v.head(3).astype(str).to_dict("records")} for k, v in x.items()}
            for k, v in x.items():
                v.to_csv(f"{OUT}/co_base_insolv_{re.sub('[^A-Za-z0-9]', '_', k)}.csv", index=False)
            break
        except Exception as e: rep.setdefault("base_errs", []).append([l, repr(e)[:200]])
json.dump(rep, open(f"{OUT}/test3.json", "w"), indent=1, default=str, ensure_ascii=False)
print("done", got)
