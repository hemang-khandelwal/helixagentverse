"""gnomAD GraphQL API client for retrieving population-specific allele frequencies."""
from __future__ import annotations

from typing import Optional

from ..schemas import AncestryFrequency, Variant
from .base import ApiClient, ApiError

# GraphQL query to resolve an NCBI rsID (e.g. rs4244285) to a genomic coordinate ID (10-94781859-G-A)
Q_SEARCH = """query($q: String!, $ds: DatasetId!) {
  variant_search(query: $q, dataset: $ds) { variant_id } }"""

# GraphQL query to retrieve allele counts (AC) and allele numbers (AN) across ancestry populations
Q_VARIANT = """query($id: String!, $ds: DatasetId!) {
  variant(variantId: $id, dataset: $ds) {
    variant_id rsids
    exome  { ac an populations { id ac an } }
    genome { ac an populations { id ac an } } } }"""


ANCESTRY_GROUPS = frozenset({"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"})


def merge_populations(variant_json: dict) -> dict[str, list[int]]:
    """
    Sum exome and genome counts per genetic ancestry group (e.g. 'afr', 'eas', 'nfe').

    gnomAD splits data by exome (coding regions) and genome (whole genome); we sum AC and AN
    across both. Only the 10 top-level gnomAD v4 ancestry groups are kept: the API also returns
    sex-stratified subsets such as 'afr_XX' or 'nfe_XY', which describe the same individuals and
    would double-count them. Note that 'ami' appears in the genome cohort but not the exome one,
    which is expected.
    """
    totals: dict[str, list[int]] = {}
    for cohort in ("exome", "genome"):
        block = variant_json.get(cohort)
        if not block:
            continue
        for p in block.get("populations") or []:
            # Keep only the top-level ancestry groups; skip sex-split and other sub-cohorts
            if p["id"] not in ANCESTRY_GROUPS:
                continue
            t = totals.setdefault(p["id"], [0, 0])
            t[0] += p["ac"]  # Total allele count (number of observed mutant copies)
            t[1] += p["an"]  # Total allele number (number of sampled chromosomes)
    return totals


class GnomadClient(ApiClient):
    """
    Client for Broad Institute's gnomAD GraphQL API.
    Provides global reference frequencies across diverse continental ancestries.
    """

    name = "gnomAD"
    url = "https://gnomad.broadinstitute.org/api"
    dataset = "gnomad_r4"      # Explicitly pin dataset release version for scientific reproducibility

    def _gql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL query and return the response data dictionary."""
        data, _ = self.request("POST", self.url, json_body={"query": query, "variables": variables})
        if data.get("errors"):
            raise ApiError(f"gnomAD GraphQL error: {data['errors']}")
        return data["data"]

    def variant_ids_for_rsid(self, rsid: str) -> list[str]:
        """Convert a dbSNP identifier (e.g. 'rs4244285') into gnomAD genomic coordinate IDs."""
        d = self._gql(Q_SEARCH, {"q": rsid, "ds": self.dataset})
        return [v["variant_id"] for v in d["variant_search"]]

    def variant(self, variant_id: str, gene: Optional[str] = None, rsid: Optional[str] = None) -> Variant:
        """
        Fetch full population frequency data for a variant coordinate (e.g. '10-94781859-G-A').
        Builds validated AncestryFrequency objects and creates a Fact record for each population.
        """
        d = self._gql(Q_VARIANT, {"id": variant_id, "ds": self.dataset})["variant"]
        if d is None:
            raise ApiError(f"gnomAD: variant {variant_id} not found in {self.dataset}")

        link = f"https://gnomad.broadinstitute.org/variant/{variant_id}?dataset={self.dataset}"
        freqs, fact_ids = [], []

        # Iterate through each ancestry group and record its AC and AN as a verifiable Fact
        for group, (ac, an) in sorted(merge_populations(d).items()):
            f = self.fact(
                field=f"ac_an:{group}",
                value={"ac": ac, "an": an, "dataset": self.dataset},
                record_id=variant_id,
                url=link,
            )
            freqs.append(AncestryFrequency(group=group, ac=ac, an=an, fact_id=f.id))
            fact_ids.append(f.id)

        rs = rsid or (d.get("rsids") or [None])[0]
        return Variant(
            variant_id=variant_id,
            rsid=rs,
            gene=gene,
            dataset=self.dataset,
            frequencies=freqs,
            fact_ids=fact_ids,
        )

    def variants_for_rsid(self, rsid: str, gene: Optional[str] = None) -> list[Variant]:
        """
        Retrieve all variant alleles for an rsID.
        Note: an rsID can occasionally have multiple alleles (e.g. multiallelic SNVs);
        downstream filtering selects the pharmacogenomically relevant change.

        Raises if any allele cannot be fetched. Callers that would rather keep the alleles
        that did work should use variants_for_rsid_detailed().
        """
        return [self.variant(v, gene=gene, rsid=rsid) for v in self.variant_ids_for_rsid(rsid)]

    def variants_for_rsid_detailed(
        self, rsid: str, gene: Optional[str] = None
    ) -> tuple[list[Variant], list[str]]:
        """
        Same as variants_for_rsid, but one allele failing does not lose the others.

        Returns (variants, problems). A multiallelic rsID where only some alleles resolve is
        still worth analysing, provided the report says which ones were skipped, so every
        skipped allele comes back as a sentence in `problems`. Raises only if the rsID itself
        cannot be resolved.
        """
        variants: list[Variant] = []
        problems: list[str] = []
        for variant_id in self.variant_ids_for_rsid(rsid):
            try:
                variants.append(self.variant(variant_id, gene=gene, rsid=rsid))
            except ApiError as e:
                problems.append(f"allele {variant_id} could not be fetched ({e})")
        return variants, problems
