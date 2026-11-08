"""Data schemas and provenance contracts for GeneGap."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

# Provenance pathways: how a specific datapoint entered the system
# - "api": retrieved directly from an external database (e.g. gnomAD, ClinicalTrials.gov)
# - "llm_extracted": extracted by an LLM from unstructured text (e.g. a study description)
# - "curated": entered by a researcher from medical guidelines (e.g. CPIC recommendations)
# - "computed": derived by deterministic mathematical functions (e.g. Hardy-Weinberg formula)
Method = Literal["api", "llm_extracted", "curated", "computed"]


def utcnow() -> datetime:
    """Helper returning current timestamp in UTC for consistent time-stamping across time zones."""
    return datetime.now(timezone.utc)

#Every number in the project is meant to be stored as a Fact. That is how the critic agent later checks that nothing was made up
class Fact(BaseModel):
    """
    The atomic unit of provenance. Every number, frequency, and count reported
    by the system is backed by a Fact so downstream reviewers and critic agents
    can verify exactly where each piece of information came from.
    """

    field: str                      # Name of the metric (e.g. "ac_an:afr", "overall_status")
    value: Any                      # The actual recorded value (number, text, or dictionary)
    source: str                     # Source repository name (e.g. "gnomAD", "ClinVar", "ClinicalTrials.gov")
    record_id: str                  # Identifier in that source (e.g. "10-94781859-G-A", "NCT01234567")
    url: str = ""                   # Direct link to the source web page for auditability
    method: Method = "api"          # Ingestion pathway ('api', 'llm_extracted', 'curated', 'computed')
    retrieved_at: datetime = Field(default_factory=utcnow)
    quote: Optional[str] = None     # Verbatim text snippet (<15 words) supporting LLM extractions
    note: Optional[str] = None      # Technical context or known assay limitations

    @computed_field  # type: ignore[misc]
    @property
    def id(self) -> str:
        """
        Deterministic SHA-1 hash of the fact's key attributes.
        If the same fact is fetched multiple times, it receives the exact same ID,
        preventing duplicated records in storage.
        """
        raw = json.dumps([self.source, self.record_id, self.field, self.value], sort_keys=True, default=str)
        return "F-" + hashlib.sha1(raw.encode()).hexdigest()[:12]


class AncestryFrequency(BaseModel):
    """
    Allele frequency for one genetic ancestry group from gnomAD.
    
    Terminology:
    - Allele Count (AC): Number of times the variant allele was observed.
    - Allele Number (AN): Total chromosomes tested (usually ~2x individuals for diploid genes).
    - Allele Frequency (AF): Fraction of chromosomes with the variant (p = AC / AN).
    """

    group: str                      # gnomAD ancestry code (e.g. "afr", "amr", "eas", "nfe", "sas")
    ac: int = Field(ge=0)           # Observed copies of the variant allele (must be >= 0)
    an: int = Field(ge=0)           # Total copies of the gene examined (must be >= 0)
    fact_id: str                    # ID of the Fact record storing the raw query response

    @model_validator(mode="after")
    def _ac_not_above_an(self) -> AncestryFrequency:
        # Biological invariant: you cannot observe more mutant alleles than chromosomes sampled
        if self.ac > self.an:
            raise ValueError(f"Allele count ({self.ac}) cannot exceed allele number ({self.an})")
        return self

    @property
    def af(self) -> Optional[float]:
        # Return point frequency p = AC / AN, or None if no chromosomes were sampled
        return self.ac / self.an if self.an > 0 else None


class Variant(BaseModel):
    """
    A genetic variant with clinical classifications and population frequencies.
    """

    variant_id: str                 # Genomic position: chrom-pos-ref-alt (e.g. "10-94781859-G-A")
    rsid: Optional[str] = None      # NCBI reference SNP ID (e.g. "rs4244285" for CYP2C19*2)
    gene: Optional[str] = None      # Approved HGNC gene symbol (e.g. "CYP2C19")
    dataset: str = ""               # Pinned dataset release (e.g. "gnomad_r4") for reproducibility
    classification: Optional[str] = None   # ClinVar classification (e.g. "Pathogenic", "Drug response")
    star_allele: Optional[str] = None       # e.g. "*2" (from the medical student's file)
    function: Optional[str] = None          # e.g. "no function", "increased function"
    copies_needed: Optional[str] = None
    curation_notes: Optional[str] = None    # the medical student's caveats, passed through to the report
    frequencies: list[AncestryFrequency] = []  # Frequencies broken down by ancestry group
    fact_ids: list[str] = []        # Provenance links for this variant


class BaselineDemographics(BaseModel):
    """
    Demographic characteristics extracted from a trial's participant baseline table.
    """

    nct_id: str                     # ClinicalTrials.gov study ID (e.g. "NCT00098800")
    scheme: Literal["race", "ethnicity", "sex", "age"]  # Type of demographic breakdown
    counts: dict[str, int]          # Reported category name -> participant count
    total_participants: Optional[int] = None
    fact_ids: list[str] = []

    @property
    def missing_fraction(self) -> Optional[float]:
        """
        Proportion of trial participants whose demographic category was unknown or not reported.
        Trials frequently have missing demographic data, so we track this explicitly.
        """
        total = sum(self.counts.values())
        if not total:
            return None
        missing = sum(
            count for label, count in self.counts.items()
            if any(term in label.lower() for term in ("unknown", "not reported", "unspecified"))
        )
        return missing / total


class Trial(BaseModel):
    """
    A clinical study record retrieved from ClinicalTrials.gov.
    """

    nct_id: str                     # National Clinical Trial registry ID
    title: str = ""                 # Brief study title
    status: str = ""                # Trial status (e.g. "COMPLETED", "TERMINATED")
    phases: list[str] = []          # Study phases (e.g. ["PHASE3"])
    conditions: list[str] = []      # Disease conditions being studied (e.g. ["Acute Coronary Syndrome"])
    interventions: list[str] = []   # Drug or treatment names tested (e.g. ["Clopidogrel"])
    has_results: bool = False       # True if the trial has posted tabular results on the registry
    baseline: list[BaselineDemographics] = []  # Extracted participant demographic tables
    fact_ids: list[str] = []        # Provenance IDs supporting trial information


class GroupGap(BaseModel):
    """
    Coverage and representation statistics for one population group in a clinical trial.
    
    Terminology:
    - Carrier frequency: Proportion of individuals carrying at least one variant copy.
      For autosomal dominant: 1 - (1 - p)^2. For autosomal recessive: p^2.
    - Credible interval (ci_low, ci_high): 95% interval from Beta Monte Carlo sampling,
      reflecting uncertainty due to small gnomAD sample size (AN).
    - Expected carriers: n_trial * carrier_freq.
    - Representation ratio: Group's trial carrier share divided by its target population share.
      Values below 1.0 indicate under-representation; values near 0 indicate serious gaps.
    - Required n: Minimum trial participants needed in this group to expect >= k carriers.
    """

    group: str                      # Ancestry group code (e.g. "afr", "eas", "nfe")
    n_trial: float                  # Participants mapped to this group (fractional under weighted mappings)
    ac: Optional[int] = None                   # gnomAD allele count backing the frequency
    an: Optional[int] = None                   # gnomAD allele number; small AN means a shaky estimate
    allele_freq: Optional[float] = None        # p = AC / AN
    trial_share: Optional[float] = None        # This group's share of the mapped trial participants
    target_share: Optional[float] = None       # Share of this group in the real-world disease population
    carrier_freq: Optional[float] = None       # Estimated carrier frequency in this group
    ci_low: Optional[float] = None             # Lower bound of carrier frequency credible interval
    ci_high: Optional[float] = None            # Upper bound of carrier frequency credible interval
    expected_carriers: Optional[float] = None  # Expected number of variant carriers in the trial
    enrolment_ratio: Optional[float] = None    # Trial group share / target population group share
    carrier_ratio: Optional[float] = None      # Trial carrier share / target carrier share
    prob_fewer_than_k: Optional[float] = None  # P(trial holds fewer than k carriers of this variant)
    k_threshold: Optional[int] = None          # The k used for prob_fewer_than_k
    required_n: Optional[int] = None           # Recommended trial sample size to meet carrier threshold
    insufficient_data: bool = False            # True if gnomAD allele number was too low to calculate reliably


class GapResult(BaseModel):
    """
    Complete genetic gap analysis across all evaluated ancestry groups for one variant.
    
    Because clinical trials report self-identified race/ethnicity while genomic databases
    report genetic ancestry, mapping between them involves assumptions. GeneGap evaluates
    multiple mapping versions (e.g. 'A' = conservative, 'B' = admixture-aware);
    a finding is robust only if it holds across mappings.
    """

    variant_id: str
    gene: Optional[str] = None
    inheritance: Literal["dominant", "recessive", "x_linked"] = "dominant"
    mapping_version: str = "A"      # Which trial-to-ancestry mapping file was used
    groups: list[GroupGap] = []     # Gap statistics per population group
    fact_ids: list[str] = []        # Associated provenance facts


class Flag(BaseModel):
    """
    An evidence finding or clinical trial coverage warning.
    
    Evidence Tiers:
    - Tier 1: Established CPIC Level A/B guideline or regulatory drug-label warning
    - Tier 2: Supported by multiple independent peer-reviewed studies or meta-analysis
    - Tier 3: Preliminary evidence (single small cohort or conflicting literature)
    - Tier 4: Mechanistic or in-vitro / computational hypothesis only
    - Tier 5: No supporting clinical literature found (frequency alone is NOT actionable)
    """

    variant_id: str
    claim: str                      # Concise description of the gap finding
    evidence_tier: Optional[int] = Field(default=None, ge=1, le=5)  # 1 (strongest) to 5 (weakest)
    citations: list[str] = []       # DOIs or PubMed IDs providing clinical evidence
    fact_ids: list[str] = []        # Supporting provenance facts
    robust_across_mappings: Optional[bool] = None  # True if confirmed under all mapping assumptions
    status: Literal["draft", "critic_passed", "human_approved", "rejected"] = "draft"
