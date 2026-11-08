"""Task 0.3. Run:  python smoke/smoke_apis.py
One minimal raw request per data source. Saves each response to data/smoke/ so you can see the real shapes."""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from genegap import config

H = {"User-Agent": config.USER_AGENT}
OUT = config.SMOKE_DIR
OUT.mkdir(parents=True, exist_ok=True)


def gnomad():
    url = "https://gnomad.broadinstitute.org/api"
    q1 = {"query": 'query { variant_search(query: "rs4244285", dataset: gnomad_r4) { variant_id } }'}
    r1 = requests.post(url, json=q1, headers=H, timeout=30).json()
    vid = r1["data"]["variant_search"][0]["variant_id"]
    q2 = {"query": 'query { variant(variantId: "%s", dataset: gnomad_r4) { variant_id rsids '
                   'exome { ac an populations { id ac an } } genome { ac an populations { id ac an } } } }' % vid}
    r2 = requests.post(url, json=q2, headers=H, timeout=30).json()
    assert "errors" not in r2, r2.get("errors")
    return r2, f"variant {vid}"


def ctgov():
    r = requests.get("https://clinicaltrials.gov/api/v2/studies", headers=H, timeout=30,
                     params={"query.intr": "clopidogrel", "filter.overallStatus": "COMPLETED",
                             "aggFilters": "results:with", "pageSize": 3}).json()
    return r, f"{len(r['studies'])} studies, first {r['studies'][0]['protocolSection']['identificationModule']['nctId']}"


def clinvar():
    p = {"db": "clinvar", "term": "rs4244285", "retmode": "json", "retmax": 3, "tool": "genegap"}
    if config.NCBI_API_KEY:
        p["api_key"] = config.NCBI_API_KEY
    r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=p, headers=H, timeout=30).json()
    return r, f"ids {r['esearchresult']['idlist']}"


def opentargets():
    q = {"query": 'query { search(queryString: "clopidogrel", entityNames: ["drug"], page: {index: 0, size: 2}) '
                  '{ hits { id name entity } } }'}
    r = requests.post("https://api.platform.opentargets.org/api/v4/graphql", json=q, headers=H, timeout=30).json()
    return r, f"hits {r['data']['search']['hits']}"


def chembl():
    r = requests.get("https://www.ebi.ac.uk/chembl/api/data/molecule/search.json",
                     params={"q": "clopidogrel", "limit": 1}, headers=H, timeout=30).json()
    return r, r["molecules"][0]["molecule_chembl_id"]


def uniprot():
    r = requests.get("https://rest.uniprot.org/uniprotkb/search", headers=H, timeout=30,
                     params={"query": "gene_exact:CYP2C19 AND organism_id:9606", "format": "json", "size": 1}).json()
    return r, r["results"][0]["primaryAccession"]


def europepmc():
    r = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", headers=H, timeout=30,
                     params={"query": "CYP2C19 clopidogrel", "format": "json", "resultType": "lite", "pageSize": 2}).json()
    return r, f"{r['hitCount']} hits"


CHECKS = [gnomad, ctgov, clinvar, opentargets, chembl, uniprot, europepmc]

if __name__ == "__main__":
    bad = 0
    for fn in CHECKS:
        try:
            data, note = fn()
            (OUT / f"{fn.__name__}.json").write_text(json.dumps(data, indent=2)[:200_000])
            print(f"PASS  {fn.__name__:<12} {note}")
        except Exception as e:
            bad += 1
            print(f"FAIL  {fn.__name__:<12} {type(e).__name__}: {str(e)[:160]}")
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed. Raw responses are in {OUT}")
    sys.exit(1 if bad else 0)
