"""Small clients for target, protein and literature lookups (Open Targets, ChEMBL, UniProt, Europe PMC)."""
from __future__ import annotations

from ..schemas import Fact
from .base import ApiClient, ApiError


class OpenTargetsClient(ApiClient):
    name = "OpenTargets"
    url = "https://api.platform.opentargets.org/api/v4/graphql"

    def _gql(self, q: str, v: dict) -> dict:
        data, _ = self.request("POST", self.url, json_body={"query": q, "variables": v})
        if data.get("errors"):
            raise ApiError(f"OpenTargets GraphQL error: {data['errors']}")
        return data["data"]

    def find_drug(self, name: str) -> list[dict]:
        q = """query($q:String!){ search(queryString:$q, entityNames:["drug"], page:{index:0,size:3}){
                 hits{ id name entity } } }"""
        return self._gql(q, {"q": name})["search"]["hits"]

    def drug_targets(self, chembl_id: str) -> list[Fact]:
        q = """query($id:String!){ drug(chemblId:$id){ name
                 mechanismsOfAction{ rows{ mechanismOfAction targets{ id approvedSymbol } } } } }"""
        d = self._gql(q, {"id": chembl_id})["drug"] or {}
        out = []
        for row in (d.get("mechanismsOfAction") or {}).get("rows", []):
            for t in row.get("targets") or []:
                out.append(self.fact("mechanism_target",
                                     {"symbol": t["approvedSymbol"], "ensembl": t["id"], "moa": row["mechanismOfAction"]},
                                     chembl_id, f"https://platform.opentargets.org/drug/{chembl_id}"))
        return out


class ChemblClient(ApiClient):
    name = "ChEMBL"
    base = "https://www.ebi.ac.uk/chembl/api/data"

    def molecule_id(self, drug_name: str) -> Fact | None:
        d, url = self.request("GET", f"{self.base}/molecule/search.json", params={"q": drug_name, "limit": 1})
        mols = d.get("molecules", [])
        if not mols:
            return None
        cid = mols[0]["molecule_chembl_id"]
        return self.fact("molecule_chembl_id", cid, drug_name.lower(), url)


class UniProtClient(ApiClient):
    name = "UniProt"
    base = "https://rest.uniprot.org/uniprotkb"

    def accession_for_gene(self, symbol: str) -> Fact | None:
        d, url = self.request("GET", f"{self.base}/search",
                              params={"query": f"gene_exact:{symbol} AND organism_id:9606", "format": "json", "size": 1})
        res = d.get("results", [])
        if not res:
            return None
        return self.fact("accession", res[0]["primaryAccession"], symbol, url)


class EuropePmcClient(ApiClient):
    name = "EuropePMC"
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def search(self, query: str, page_size: int = 5) -> list[Fact]:
        d, url = self.request("GET", f"{self.base}/search",
                              params={"query": query, "format": "json", "resultType": "lite", "pageSize": page_size})
        out = []
        for r in d.get("resultList", {}).get("result", []):
            rid = f"{r.get('source')}:{r.get('id')}"
            out.append(self.fact("paper", {"title": r.get("title"), "year": r.get("pubYear"), "doi": r.get("doi")},
                                 rid, f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}"))
        return out
