import io, json, re, sys, time, urllib.request, zipfile
import pandas as pd
OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
B = "https://www.debentures.com.br/exploreosnd/consultaadados/emissoesdedebentures/"
rep = {}
def fetch(k, u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=300) as r:
            b = r.read(); rep[k] = {"url": u, "status": r.status, "ctype": r.headers.get("content-type"), "disp": r.headers.get("content-disposition"), "bytes": len(b)}
            open(f"{OUT}/deb_{k}.bin", "wb").write(b[:3_000_000]); print(k, rep[k], flush=True); return b
    except Exception as e: rep[k] = {"url": u, "err": repr(e)[:200]}; print(k, rep[k], flush=True)
page = fetch("caract_page", B + "caracteristicas_f.asp")
if page:
    t = page.decode("latin-1")
    rep["links"] = sorted(set(re.findall(r'href="([^"]*(?:_e|_r|download|xls|csv)[^"]*)"', t)))[:30]
    clean = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S); clean = re.sub(r"<[^>]+>", " ", clean); clean = re.sub(r"\s+", " ", clean)
    i = clean.find("download"); rep["download_text"] = clean[max(0, i - 300): i + 900]
for k, q in [("all_registered", "caracteristicas_e.asp?tip_deb=&op_exc=False"), ("all_excluded", "caracteristicas_e.asp?tip_deb=&op_exc=True"), ("all_both", "caracteristicas_e.asp?tip_deb=&op_exc=Nada")]:
    b = fetch(k, B + q)
    if b:
        head = b[:600]
        rep[k]["head"] = head.decode("latin-1", "ignore")[:600]
for k, q in [("vencimentos", "vencimentos_proximos.asp?tip_deb=publicas&op_exc="), ("agenda", "../eventosfinanceiros/agenda_r.asp")]:
    fetch(k, B + q)
json.dump(rep, open(f"{OUT}/deb_probe.json", "w"), indent=1, ensure_ascii=False, default=str)
print("done")
