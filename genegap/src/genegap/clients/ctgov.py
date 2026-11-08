"""ClinicalTrials.gov API v2 client for trial search and study protocol retrieval."""
from __future__ import annotations

from typing import Optional

from ..schemas import Trial
from .base import ApiClient

# Selective field projection for search: fetches only necessary metadata to minimize bandwidth
FIELDS = "NCTId|BriefTitle|OverallStatus|Phase|Condition|InterventionName|HasResults"


class CTGovClient(ApiClient):
    """
    Client for the official ClinicalTrials.gov REST API v2.
    Searches completed clinical studies and extracts structured study records.
    """

    name = "ClinicalTrials.gov"
    base = "https://clinicaltrials.gov/api/v2"

    def _trial(self, study: dict) -> Trial:
        """Parse a study dictionary from ClinicalTrials.gov API v2 into a typed Trial model."""
        protocol = study.get("protocolSection", {})
        ident = protocol.get("identificationModule", {})
        nct = ident.get("nctId", "")
        status = protocol.get("statusModule", {}).get("overallStatus", "")

        # Record a provenance Fact linking the trial status to its official public URL
        f = self.fact(
            field="overall_status",
            value=status,
            record_id=nct,
            url=f"https://clinicaltrials.gov/study/{nct}",
        )

        return Trial(
            nct_id=nct,
            title=ident.get("briefTitle", ""),
            status=status,
            phases=protocol.get("designModule", {}).get("phases", []),
            conditions=protocol.get("conditionsModule", {}).get("conditions", []),
            interventions=[
                i.get("name", "")
                for i in protocol.get("armsInterventionsModule", {}).get("interventions", [])
            ],
            has_results=bool(study.get("hasResults")),
            fact_ids=[f.id],
        )

    def search(
        self,
        intervention: Optional[str] = None,
        condition: Optional[str] = None,
        status: str = "COMPLETED",
        has_results: bool = True,
        page_size: int = 50,
        max_pages: int = 1,
    ) -> list[Trial]:
        """
        Search for completed clinical trials studying a specific drug intervention or condition.
        
        Filtering for status='COMPLETED' and has_results=True ensures the system only analyzes
        trials that actually enrolled participants and posted outcome data.
        """
        params = {
            "query.intr": intervention,
            "query.cond": condition,
            "filter.overallStatus": status,
            "aggFilters": "results:with" if has_results else None,
            "pageSize": page_size,
            "fields": FIELDS,
        }
        # Strip out None values so requests doesn't serialize empty URL parameters
        params = {k: v for k, v in params.items() if v is not None}
        trials: list[Trial] = []

        # Page through results using ClinicalTrials.gov pageToken
        for _ in range(max_pages):
            data, _url = self.request("GET", f"{self.base}/studies", params=params)
            trials += [self._trial(s) for s in data.get("studies", [])]
            token = data.get("nextPageToken")
            if not token:
                break
            params = {**params, "pageToken": token}

        return trials

    def study_raw(self, nct_id: str) -> dict:
        """
        Retrieve the unabridged study record including resultsSection and baseline tables.
        Used downstream by the demographic parser to extract participant race and ethnicity counts.
        """
        data, _ = self.request("GET", f"{self.base}/studies/{nct_id}")
        return data
