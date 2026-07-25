"""测试 EmployerAutocompleteSearch 接口"""
import json, time
import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

def search(term: str):
    url = "https://api.glassdoor.com/mobile-graph"
    params = {"locale": "zh_CN_#Hans"}
    headers = {
        "x-gd-id": GD_ID,
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": "EmployerAutocompleteSearch",
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0",
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
        "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
    }
    body = {
        "operationName": "EmployerAutocompleteSearch",
        "variables": {"term": term},
        "query": "query EmployerAutocompleteSearch($term: String!) { employerAutocomplete(term: $term, caller: \"MOBILE\") { id logoURL name } }",
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
    }
    resp = requests.post(url, params=params, headers=headers, json=body,
                         impersonate="chrome110", timeout=30)
    print(f"Search '{term}': status={resp.status_code}, len={len(resp.content)}")
    if resp.status_code == 200:
        data = resp.json()
        results = data.get("data", {}).get("employerAutocomplete", [])
        print(f"  Results: {len(results)}")
        for r in results[:5]:
            print(f"    {r.get('id')} – {r.get('name')}")
    else:
        print(f"  Error: {resp.text[:200]}")
    return resp

for term in ["Google", "Apple", "Amazon", "Microsoft", "Netflix", "Facebook", "A"]:
    search(term)
    time.sleep(2)
