"""Probe emerging-market open company-data sources: reachability and a small sample."""
import json, sys, time, urllib.request, ssl, os
OUT = sys.argv[1] if len(sys.argv) > 1 else "em_probe_out"
os.makedirs(OUT, exist_ok=True)
URLS = {
 # Brazil
 "br_cvm_root": "https://dados.cvm.gov.br/dados/",
 "br_cvm_ipe": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/",
 "br_cvm_dfp": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/",
 "br_cvm_cad": "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv",
 "br_cvm_oferta": "https://dados.cvm.gov.br/dados/OFERTA/DISTRIB/DADOS/",
 "br_cvm_ofertas_rcvm160": "https://dados.cvm.gov.br/dados/OFERTA/",
 "br_rfb_cnpj": "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/",
 "br_rfb_cnpj_old": "https://dadosabertos.rfb.gov.br/CNPJ/",
 "br_pncp": "https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20250102&pagina=1",
 # Colombia
 "co_secop2": "https://www.datos.gov.co/resource/jbjy-vk9h.json?$limit=2",
 "co_cat_insolv": "https://www.datos.gov.co/api/catalog/v1?q=insolvencia&limit=20",
 "co_cat_superso": "https://www.datos.gov.co/api/catalog/v1?q=supersociedades%20estados%20financieros&limit=20",
 "co_cat_garantias": "https://www.datos.gov.co/api/catalog/v1?q=garantias%20mobiliarias&limit=10",
 "co_cat_rues": "https://www.datos.gov.co/api/catalog/v1?q=registro%20mercantil%20camara%20de%20comercio&limit=10",
 "co_garantias_site": "https://www.garantiasmobiliarias.com.co/",
 # Mexico
 "mx_rug": "https://www.rug.gob.mx/",
 "mx_compranet_dg": "https://upcp-compranet.buengobierno.gob.mx/",
 "mx_datos": "https://datos.gob.mx/",
 # Chile
 "cl_boletin_concursal": "https://www.boletinconcursal.cl/",
 "cl_registro_empresas": "https://www.registrodeempresasysociedades.cl/",
 "cl_mercadopublico": "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json?estado=activas&ticket=F8537A18-6766-4DEF-9E59-426B4FEE2844",
 "cl_cmf": "https://www.cmfchile.cl/",
 # Peru
 "pe_indecopi_concursal": "https://www.indecopi.gob.pe/",
 "pe_sunat_padron": "https://www.sunat.gob.pe/descargaPRR/padron_reducido_ruc.zip",
 # India
 "in_mca": "https://www.mca.gov.in/",
 "in_ibbi_cirp": "https://ibbi.gov.in/en/public-announcement",
 "in_datagov": "https://www.data.gov.in/",
 "in_nse_ann": "https://www.nseindia.com/",
 # Turkey
 "tr_kap": "https://www.kap.org.tr/tr/",
 "tr_kap_api_members": "https://www.kap.org.tr/tr/api/member/filter/IGS",
 "tr_ticaret_sicil": "https://www.ticaretsicil.gov.tr/",
 "tr_ilan": "https://www.ilan.gov.tr/",
 "tr_ekap": "https://ekap.kik.gov.tr/",
 # Global
 "gleif_tr": "https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=TR&page[size]=1",
 "gleif_br": "https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=BR&page[size]=1",
 "gleif_in": "https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=IN&page[size]=1",
 "gleif_co": "https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=CO&page[size]=1",
 "gleif_mx": "https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=MX&page[size]=1",
 "opencorporates": "https://api.opencorporates.com/v0.4/companies/search?q=altinyildiz",
}
res = {}
for k, u in URLS.items():
    t = time.time()
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (research probe)", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read(400_000)
            res[k] = {"url": u, "status": r.status, "bytes_read": len(body), "ctype": r.headers.get("content-type"), "secs": round(time.time()-t, 1)}
            with open(os.path.join(OUT, k + ".txt"), "wb") as f:
                f.write(body[:200_000])
    except urllib.error.HTTPError as e:
        res[k] = {"url": u, "status": e.code, "err": str(e)[:200]}
    except Exception as e:
        res[k] = {"url": u, "status": None, "err": repr(e)[:200]}
    print(k, res[k].get("status"), res[k].get("bytes_read", res[k].get("err")), flush=True)
json.dump(res, open(os.path.join(OUT, "probe.json"), "w"), indent=1)
