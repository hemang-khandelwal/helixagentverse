"""ClinicalTrials.gov API v2 client. Demographic parsing comes later (task 2.3)."""
from __future__ import annotations

from typing import Optional

from ..schemas import Trial
from .base import ApiClient

FIELDS = "NCTId|BriefTitle|OverallStatus|Phase|Condition|InterventionName|HasResults"


class CTGovClient(ApiClient):
    name = "ClinicalTrials.gov"
    base = "https://clinicaltrials.gov/api/v2"

    def _trial(self, study: dict) -> Trial:
        p = study.get("protocolSection", {})
        nct = p.get("identificationModule", {}).get("nctId", "")
        status = p.get("statusModule", {}).get("overallStatus", "")
        f = self.fact("overall_status", status, nct, f"https://clinicaltrials.gov/study/{nct}")
        return Trial(
            nct_id=nct,
            title=p.get("identificationModule", {}).get("briefTitle", ""),
            status=status,
            phases=p.get("designModule", {}).get("phases", []),
            conditions=p.get("conditionsModule", {}).get("conditions", []),
            interventions=[i.get("name", "") for i in p.get("armsInterventionsModule", {}).get("interventions", [])],
            has_results=bool(study.get("hasResults")),
            fact_ids=[f.id],
        )

    def search(self, intervention: Optional[str] = None, condition: Optional[str] = None,
               status: str = "COMPLETED", has_results: bool = True,
               page_size: int = 50, max_pages: int = 1) -> list[Trial]:
        params = {"query.intr": intervention, "query.cond": condition, "filter.overallStatus": status,
                  "aggFilters": "results:with" if has_results else None,
                  "pageSize": page_size, "fields": FIELDS}
        params = {k: v for k, v in params.items() if v is not None}
        trials: list[Trial] = []
        for _ in range(max_pages):
            data, _url = self.request("GET", f"{self.base}/studies", params=params)
            trials += [self._trial(s) for s in data.get("studies", [])]
            token = data.get("nextPageToken")
            if not token:
                break
            params = {**params, "pageToken": token}
        return trials

    def study_raw(self, nct_id: str) -> dict:
        """Full record, including resultsSection. Task 2.3 parses the baseline tables from this."""
        data, _ = self.request("GET", f"{self.base}/studies/{nct_id}")
        return data
