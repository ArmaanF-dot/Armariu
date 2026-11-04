import json, requests
urls = [
 "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?publishedFrom=2024-03-01T00:00:00&publishedTo=2024-03-02T00:00:00&stages=award&limit=3",
 "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?publishedFrom=2024-03-01&publishedTo=2024-03-02&stages=award&size=3",
 "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?stages=award&limit=3&updatedFrom=2025-06-01T00:00:00&updatedTo=2025-06-02T00:00:00",
 "https://www.contractsfinder.service.gov.uk/",
 "https://www.find-tender.service.gov.uk/",
]
for u in urls:
    print("=====", u)
    try:
        r = requests.get(u, timeout=60, headers={"Accept": "application/json"})
        print(r.status_code, r.headers.get("content-type"), len(r.content))
        t = r.text
        try:
            j = r.json(); print(json.dumps(j, indent=1)[:9000])
        except Exception:
            print(t[:3000])
    except Exception as e:
        print("ERR", e)
import os
print("CH key present:", bool(os.environ.get("CH_REST_KEY")))
