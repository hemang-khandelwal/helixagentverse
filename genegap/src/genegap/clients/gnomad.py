"""gnomAD GraphQL client. Query shapes are from memory: run smoke/smoke_apis.py to confirm them."""
from __future__ import annotations

from ..schemas import AncestryFrequency, Variant
from .base import ApiClient, ApiError

Q_SEARCH = """query($q: String!, $ds: DatasetId!) {
  variant_search(query: $q, dataset: $ds) { variant_id } }"""

Q_VARIANT = """query($id: String!, $ds: DatasetId!) {
  variant(variantId: $id, dataset: $ds) {
    variant_id rsids
    exome  { ac an populations { id ac an } }
    genome { ac an populations { id ac an } } } }"""


ANCESTRY_GROUPS = frozenset({"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"})


def merge_populations(variant_json: dict) -> dict[str, list[int]]:
    """Sum exome + genome counts per ancestry group. Only the 10 top-level gnomAD v4 groups are kept."""
    # Note: 'ami' appears in genome but not exome, which is expected.
    totals: dict[str, list[int]] = {}
    for cohort in ("exome", "genome"):
        block = variant_json.get(cohort)
        if not block:
            continue
        for p in block.get("populations") or []:
            if p["id"] not in ANCESTRY_GROUPS:
                continue
            t = totals.setdefault(p["id"], [0, 0])
            t[0] += p["ac"]
            t[1] += p["an"]
    return totals


class GnomadClient(ApiClient):
    name = "gnomAD"
    url = "https://gnomad.broadinstitute.org/api"
    dataset = "gnomad_r4"      # pin the version; record it in every Fact

    def _gql(self, query: str, variables: dict) -> dict:
        data, _ = self.request("POST", self.url, json_body={"query": query, "variables": variables})
        if data.get("errors"):
            raise ApiError(f"gnomAD GraphQL error: {data['errors']}")
        return data["data"]

    def variant_ids_for_rsid(self, rsid: str) -> list[str]:
        d = self._gql(Q_SEARCH, {"q": rsid, "ds": self.dataset})
        return [v["variant_id"] for v in d["variant_search"]]

    def variant(self, variant_id: str, gene: str | None = None, rsid: str | None = None) -> Variant:
        d = self._gql(Q_VARIANT, {"id": variant_id, "ds": self.dataset})["variant"]
        if d is None:
            raise ApiError(f"gnomAD: variant {variant_id} not found in {self.dataset}")
        link = f"https://gnomad.broadinstitute.org/variant/{variant_id}?dataset={self.dataset}"
        freqs, fact_ids = [], []
        for group, (ac, an) in sorted(merge_populations(d).items()):
            f = self.fact(f"ac_an:{group}", {"ac": ac, "an": an, "dataset": self.dataset}, variant_id, link)
            freqs.append(AncestryFrequency(group=group, ac=ac, an=an, fact_id=f.id))
            fact_ids.append(f.id)
        rs = rsid or (d.get("rsids") or [None])[0]
        return Variant(variant_id=variant_id, rsid=rs, gene=gene, dataset=self.dataset,
                       frequencies=freqs, fact_ids=fact_ids)

    def variants_for_rsid(self, rsid: str, gene: str | None = None) -> list[Variant]:
        """An rsID can map to several alleles; the caller (or the medical student's rules) picks."""
        return [self.variant(v, gene=gene, rsid=rsid) for v in self.variant_ids_for_rsid(rsid)]
