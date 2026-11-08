"""
API smoke test (Task 0.3).
Run: python smoke/smoke_apis.py

Issues one minimal request per data source and saves each response to data/smoke/
so you can inspect the exact payload shapes returned by each endpoint.
"""
import json
import sys
from pathlib import Path

import requests

# Add the 'src' directory to sys.path so 'genegap' can be imported
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from genegap import config

# Standard headers including polite User-Agent
H = {"User-Agent": config.USER_AGENT}
OUT = config.SMOKE_DIR
OUT.mkdir(parents=True, exist_ok=True)


def gnomad():
    """
    gnomAD GraphQL API test.
    Queries the known loss-of-function variant CYP2C19*2 (rs4244285) in dataset gnomad_r4
    and fetches allele counts (AC) and numbers (AN) for exomes and genomes.
    """
    url = "https://gnomad.broadinstitute.org/api"
    # Step 1: Look up genomic coordinates for the rsID
    q1 = {"query": 'query { variant_search(query: "rs4244285", dataset: gnomad_r4) { variant_id } }'}
    r1 = requests.post(url, json=q1, headers=H, timeout=30).json()
    vid = r1["data"]["variant_search"][0]["variant_id"]

    # Step 2: Fetch population-level allele frequencies for that coordinate
    q2 = {
        "query": 'query { variant(variantId: "%s", dataset: gnomad_r4) { variant_id rsids '
        "exome { ac an populations { id ac an } } "
        "genome { ac an populations { id ac an } } } }" % vid
    }
    r2 = requests.post(url, json=q2, headers=H, timeout=30).json()
    assert "errors" not in r2, r2.get("errors")
    return r2, f"variant {vid}"


def ctgov():
    """
    ClinicalTrials.gov API v2 test.
    Searches for completed studies investigating clopidogrel that have posted tabular results.
    """
    r = requests.get(
        "https://clinicaltrials.gov/api/v2/studies",
        headers=H,
        timeout=30,
        params={
            "query.intr": "clopidogrel",
            "filter.overallStatus": "COMPLETED",
            "aggFilters": "results:with",
            "pageSize": 3,
        },
    ).json()
    return r, f"{len(r['studies'])} studies, first {r['studies'][0]['protocolSection']['identificationModule']['nctId']}"


def clinvar():
    """
    ClinVar NCBI Entrez test.
    Searches ClinVar for clinical significance records associated with rs4244285.
    """
    p = {"db": "clinvar", "term": "rs4244285", "retmode": "json", "retmax": 3, "tool": "genegap"}
    if config.NCBI_API_KEY:
        p["api_key"] = config.NCBI_API_KEY
    if config.CONTACT_EMAIL:
        p["email"] = config.CONTACT_EMAIL
    r = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=p,
        headers=H,
        timeout=30,
    ).json()
    return r, f"ids {r['esearchresult']['idlist']}"


def opentargets():
    """
    Open Targets GraphQL API test.
    Searches for the drug 'clopidogrel' to verify drug-to-target entity resolution.
    """
    q = {
        "query": 'query { search(queryString: "clopidogrel", entityNames: ["drug"], page: {index: 0, size: 2}) '
        "{ hits { id name entity } } }"
    }
    r = requests.post("https://api.platform.opentargets.org/api/v4/graphql", json=q, headers=H, timeout=30).json()
    return r, f"hits {r['data']['search']['hits']}"


def chembl():
    """
    ChEMBL REST API test.
    Searches for 'clopidogrel' to retrieve its canonical ChEMBL molecule ID.
    """
    r = requests.get(
        "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json",
        params={"q": "clopidogrel", "limit": 1},
        headers=H,
        timeout=30,
    ).json()
    return r, r["molecules"][0]["molecule_chembl_id"]


def uniprot():
    """
    UniProt REST API test.
    Looks up human gene CYP2C19 (organism_id 9606) to retrieve its Swiss-Prot accession.
    """
    r = requests.get(
        "https://rest.uniprot.org/uniprotkb/search",
        headers=H,
        timeout=30,
        params={"query": "gene_exact:CYP2C19 AND organism_id:9606", "format": "json", "size": 1},
    ).json()
    return r, r["results"][0]["primaryAccession"]


def europepmc():
    """
    Europe PMC REST API test.
    Searches scientific publications mentioning both CYP2C19 and clopidogrel.
    """
    r = requests.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        headers=H,
        timeout=30,
        params={"query": "CYP2C19 clopidogrel", "format": "json", "resultType": "lite", "pageSize": 2},
    ).json()
    return r, f"{r['hitCount']} hits"


CHECKS = [gnomad, ctgov, clinvar, opentargets, chembl, uniprot, europepmc]

if __name__ == "__main__":
    bad = 0
    for fn in CHECKS:
        try:
            data, note = fn()
            # Save raw JSON response to data/smoke/<api>.json for developer inspection
            (OUT / f"{fn.__name__}.json").write_text(json.dumps(data, indent=2)[:200_000])
            print(f"PASS  {fn.__name__:<12} {note}")
        except Exception as e:
            bad += 1
            print(f"FAIL  {fn.__name__:<12} {type(e).__name__}: {str(e)[:160]}")
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed. Raw responses are in {OUT}")
    sys.exit(1 if bad else 0)
