import json, sys, time, urllib.request, urllib.parse
UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
def get(u, t=120):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as r: return r.status, r.read().decode("utf8", "replace")
        except Exception as e: err = repr(e)[:120]; time.sleep(3)
    return 0, err
def soda(ds, q):
    s, t = get(f"https://www.datos.gov.co/resource/{ds}.json?" + urllib.parse.urlencode(q), 300)
    try: return json.loads(t)
    except Exception: return t[:200]
for ds in ["it5q-hg94", "nb3d-v3n7", "xse2-2ted", "73dx-n59j", "si2v-pbq5", "4n4q-k399"]:
    print("=====", ds)
    s, m = get(f"https://www.datos.gov.co/api/views/{ds}.json")
    if s != 200: print("meta fail", m); continue
    j = json.loads(m); print(j.get("name"), "| rowsUpdatedAt", j.get("rowsUpdatedAt"), "| license", (j.get("license") or {}).get("name"))
    cols = [c["fieldName"] for c in j["columns"]]; print(cols)
    print("count", soda(ds, {"$select": "count(*)"}))
    print("sample", json.dumps(soda(ds, {"$limit": 2}), ensure_ascii=False)[:1800])
    for c in j["columns"]:
        if c["dataTypeName"] in ("calendar_date", "date") or "fecha" in c["fieldName"]:
            print("range", c["fieldName"], soda(ds, {"$select": f"min({c['fieldName']}) as mn, max({c['fieldName']}) as mx, count({c['fieldName']}) as n"}))
for u in ["https://www.dian.gov.co/dian/cifras/Paginas/Bases-de-Datos-Comercio-Exterior.aspx", "https://www.dian.gov.co/dian/cifras/Paginas/EstadisticasComercioExterior.aspx"]:
    s, t = get(u); print("DIAN", u, s, len(t), t[:300].replace("\n", " "))
