"""ClinVar via NCBI E-utilities. Field names for the classification vary by record vintage, so we try both."""
from __future__ import annotations

from ..config import CONTACT_EMAIL, NCBI_API_KEY
from ..schemas import Fact
from .base import ApiClient


class ClinVarClient(ApiClient):
    name = "ClinVar"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    min_interval = 0.12 if NCBI_API_KEY else 0.4      # NCBI: 10 req/s with a key, 3 without

    def _p(self, **kw) -> dict:
        kw.update(retmode="json", tool="genegap")
        if NCBI_API_KEY:
            kw["api_key"] = NCBI_API_KEY
        if CONTACT_EMAIL:
            kw["email"] = CONTACT_EMAIL
        return kw

    def lookup_rsid(self, rsid: str, retmax: int = 5) -> list[Fact]:
        """One Fact per ClinVar record found for the rsID: value = {title, classification}."""
        s, _ = self.request("GET", f"{self.base}/esearch.fcgi", params=self._p(db="clinvar", term=rsid, retmax=retmax))
        ids = s.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        d, url = self.request("GET", f"{self.base}/esummary.fcgi", params=self._p(db="clinvar", id=",".join(ids)))
        out = []
        for uid in d.get("result", {}).get("uids", []):
            r = d["result"][uid]
            cls = (r.get("germline_classification") or {}).get("description") \
                or (r.get("clinical_significance") or {}).get("description")
            out.append(self.fact("classification", {"title": r.get("title"), "classification": cls},
                                 f"VCV/uid:{uid}", f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/"))
        return out
