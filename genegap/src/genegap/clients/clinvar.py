"""ClinVar client via NCBI Entrez E-utilities for variant pathogenicity and drug response."""
from __future__ import annotations

from typing import Any

from ..config import CONTACT_EMAIL, NCBI_API_KEY
from ..schemas import Fact
from .base import ApiClient


class ClinVarClient(ApiClient):
    """
    Client for NCBI ClinVar using Entrez E-utilities.
    Retrieves clinical significance classifications (e.g. 'Drug response', 'Pathogenic', 'VUS').
    """

    name = "ClinVar"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # NCBI allows up to 10 requests/sec with an API key, or 3 requests/sec without one
    min_interval = 0.12 if NCBI_API_KEY else 0.40

    def _p(self, **kw: Any) -> dict[str, Any]:
        """Attach standard NCBI Entrez identification parameters (tool name, email, API key)."""
        kw.update(retmode="json", tool="genegap")
        if NCBI_API_KEY:
            kw["api_key"] = NCBI_API_KEY
        if CONTACT_EMAIL:
            kw["email"] = CONTACT_EMAIL
        return kw

    def lookup_rsid(self, rsid: str, retmax: int = 5) -> list[Fact]:
        """
        Look up ClinVar records for an rsID.
        
        Step 1: Use esearch to find ClinVar variation IDs matching the rsID.
        Step 2: Use esummary to retrieve the title and clinical significance description.
        Step 3: Save and return a Fact for each matching ClinVar assertion.
        """
        # Search for ClinVar internal IDs (UIDs) matching this dbSNP rsID
        search_res, _ = self.request(
            "GET",
            f"{self.base}/esearch.fcgi",
            params=self._p(db="clinvar", term=rsid, retmax=retmax),
        )
        ids = search_res.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        # Retrieve summary documents for the discovered IDs
        summary_res, url = self.request(
            "GET",
            f"{self.base}/esummary.fcgi",
            params=self._p(db="clinvar", id=",".join(ids)),
        )

        out = []
        result_map = summary_res.get("result", {})

        for uid in result_map.get("uids", []):
            record = result_map[uid]
            # Handle vintage schema differences: modern records use 'germline_classification',
            # while older records use 'clinical_significance'
            classification = (
                (record.get("germline_classification") or {}).get("description")
                or (record.get("clinical_significance") or {}).get("description")
                or "Unknown"
            )

            out.append(
                self.fact(
                    field="classification",
                    value={"title": record.get("title", ""), "classification": classification},
                    record_id=f"VCV/uid:{uid}",
                    url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
                )
            )

        return out
