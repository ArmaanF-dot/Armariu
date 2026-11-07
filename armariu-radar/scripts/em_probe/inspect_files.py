import io, re, sys, json, urllib.request, urllib.parse, time
import pandas as pd
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
h = open(f"{OUT}/superso_informes.html").read()
links = sorted(set(m.replace("&amp;", "&") for m in re.findall(r'href="([^"]+)"', h) if ".xlsx" in m and re.search(r"insolv", m, re.I)))
rep = []
for l in links:
    u = urllib.parse.urljoin("https://www.supersociedades.gov.co/", l)
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=300).read()
        xs = pd.read_excel(io.BytesIO(b), sheet_name=None, dtype=str)
        for k, v in xs.items():
            rep.append({"file": l.split("/")[-2][:70], "sheet": k, "rows": len(v), "cols": [str(c) for c in v.columns][:30]})
    except Exception as e:
        rep.append({"file": l.split("/")[-2][:70], "err": repr(e)[:150]})
    print(rep[-1], flush=True)
json.dump(rep, open(f"{OUT}/inspect_files.json", "w"), indent=1, ensure_ascii=False)
