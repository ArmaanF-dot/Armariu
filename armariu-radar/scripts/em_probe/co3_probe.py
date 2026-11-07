import json, sys, time, urllib.request, urllib.parse
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=300) as r: return r.read().decode("utf8", "replace")
        except Exception as e: print("retry", u[:100], repr(e)[:100]); time.sleep(4)
    return ""
def soda(ds, q): return json.loads(get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q)) or "[]")
for ds in ["uymx-8p3j", "c82u-588k"]:
    print("=====", ds)
    m = get(f"https://www.datos.gov.co/api/views/{ds}.json")
    if m:
        j = json.loads(m); print(j.get("name"), "| rowsUpdatedAt", j.get("rowsUpdatedAt"), "| viewLastModified", j.get("viewLastModified"))
        print([ (c["fieldName"], c["dataTypeName"]) for c in j["columns"]])
    print("count", soda(ds, {"$select": "count(*)"}))
    print("sample", json.dumps(soda(ds, {"$limit": 3}), ensure_ascii=False)[:2500])
# payment plan: field population
cols = [c["fieldName"] for c in json.loads(get("https://www.datos.gov.co/api/views/uymx-8p3j.json"))["columns"]]
sel = ",".join(f"count({c}) as n_{i}" for i, c in enumerate(cols))
r = soda("uymx-8p3j", {"$select": "count(*) as n," + sel}); print("population", r)
print(dict(zip([f"n_{i}" for i in range(len(cols))], cols)))
for c in cols:
    if "fecha" in c.lower():
        print(c, soda("uymx-8p3j", {"$select": f"min({c}) as mn, max({c}) as mx"}))
# catalog search for other ideas
for q in ["boletin deudores morosos", "deudores morosos del estado", "informacion relevante emisores", "hechos relevantes", "emisores valores", "estados financieros", "obligaciones tributarias", "sancion", "multas supersociedades", "cartera"]:
    t = get("https://api.us.socrata.com/api/catalog/v1?" + urllib.parse.urlencode({"domains": "www.datos.gov.co", "q": q, "limit": 8, "only": "datasets"}))
    try: print("CAT", q, [(x["resource"]["id"], x["resource"]["name"][:70], x["resource"].get("updatedAt","")[:10]) for x in json.loads(t)["results"]])
    except Exception as e: print("CAT fail", q, repr(e)[:80], t[:100])
# BDME site
for u in ["https://www.contaduria.gov.co/bdme", "https://www.contaduria.gov.co/boletin-de-deudores-morosos-del-estado-bdme", "https://www.superfinanciera.gov.co/jsp/10100890"]:
    t = get(u); print("SITE", u, len(t), t[:300].replace("\n", " "))
