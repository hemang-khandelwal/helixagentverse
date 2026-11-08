"""Clients for target, protein, and literature lookups (Open Targets, ChEMBL, UniProt, Europe PMC)."""
from __future__ import annotations

from typing import Any, Optional

from ..schemas import Fact
from .base import ApiClient, ApiError


class OpenTargetsClient(ApiClient):
    """
    Client for Open Targets Platform GraphQL API.
    Identifies target genes, metabolizing enzymes, and mechanisms of action for drugs.
    """

    name = "OpenTargets"
    url = "https://api.platform.opentargets.org/api/v4/graphql"

    def _gql(self, q: str, v: dict[str, Any]) -> dict[str, Any]:
        """Execute GraphQL query against Open Targets and return the data payload."""
        data, _ = self.request("POST", self.url, json_body={"query": q, "variables": v})
        if data.get("errors"):
            raise ApiError(f"OpenTargets GraphQL error: {data['errors']}")
        return data["data"]

    def find_drug(self, name: str) -> list[dict[str, Any]]:
        """Search Open Targets for a drug name and return matching drug entities with their ChEMBL IDs."""
        q = """query($q:String!){ search(queryString:$q, entityNames:["drug"], page:{index:0,size:3}){
                 hits{ id name entity } } }"""
        return self._gql(q, {"q": name})["search"]["hits"]

    def drug_targets(self, chembl_id: str) -> list[Fact]:
        """
        Query verified mechanisms of action and approved target gene symbols for a drug.
        Creates a Fact record for each target gene.
        """
        q = """query($id:String!){ drug(chemblId:$id){ name
                 mechanismsOfAction{ rows{ mechanismOfAction targets{ id approvedSymbol } } } } }"""
        d = self._gql(q, {"id": chembl_id})["drug"] or {}
        out = []

        # Iterate through mechanisms of action and extract approved gene symbols (e.g. CYP2C19, P2RY12)
        for row in (d.get("mechanismsOfAction") or {}).get("rows", []):
            for t in row.get("targets") or []:
                out.append(
                    self.fact(
                        field="mechanism_target",
                        value={
                            "symbol": t["approvedSymbol"],
                            "ensembl": t["id"],
                            "moa": row["mechanismOfAction"],
                        },
                        record_id=chembl_id,
                        url=f"https://platform.opentargets.org/drug/{chembl_id}",
                    )
                )
        return out


class ChemblClient(ApiClient):
    """
    Client for EMBL-EBI ChEMBL REST API.
    Used to resolve chemical drug names to canonical ChEMBL molecule identifiers.
    """

    name = "ChEMBL"
    base = "https://www.ebi.ac.uk/chembl/api/data"

    def molecule_id(self, drug_name: str) -> Optional[Fact]:
        """Look up the ChEMBL molecule ID for a given drug name (e.g. 'clopidogrel' -> 'CHEMBL483')."""
        d, url = self.request(
            "GET",
            f"{self.base}/molecule/search.json",
            params={"q": drug_name, "limit": 1},
        )
        mols = d.get("molecules", [])
        if not mols:
            return None
        cid = mols[0]["molecule_chembl_id"]
        return self.fact("molecule_chembl_id", cid, drug_name.lower(), url)


class UniProtClient(ApiClient):
    """
    Client for UniProtKB REST API.
    Maps human gene symbols to primary protein sequence accessions and structural annotations.
    """

    name = "UniProt"
    base = "https://rest.uniprot.org/uniprotkb"

    def accession_for_gene(self, symbol: str) -> Optional[Fact]:
        """
        Look up the primary Swiss-Prot accession for a human gene symbol
        (organism_id:9606 restricts results to Homo sapiens).
        """
        d, url = self.request(
            "GET",
            f"{self.base}/search",
            params={"query": f"gene_exact:{symbol} AND organism_id:9606", "format": "json", "size": 1},
        )
        res = d.get("results", [])
        if not res:
            return None
        return self.fact("accession", res[0]["primaryAccession"], symbol, url)


class EuropePmcClient(ApiClient):
    """
    Client for EMBL-EBI Europe PMC REST API.
    Searches peer-reviewed literature to find published clinical evidence supporting flags.
    """

    name = "EuropePMC"
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def search(self, query: str, page_size: int = 5) -> list[Fact]:
        """
        Search scientific publications matching a query (e.g. 'CYP2C19 clopidogrel resistance').
        Creates a Fact record for each paper containing title, publication year, and DOI.
        """
        d, url = self.request(
            "GET",
            f"{self.base}/search",
            params={"query": query, "format": "json", "resultType": "lite", "pageSize": page_size},
        )
        out = []
        for r in d.get("resultList", {}).get("result", []):
            rid = f"{r.get('source')}:{r.get('id')}"
            out.append(
                self.fact(
                    field="paper",
                    value={"title": r.get("title"), "year": r.get("pubYear"), "doi": r.get("doi")},
                    record_id=rid,
                    url=f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}",
                )
            )
        return out
