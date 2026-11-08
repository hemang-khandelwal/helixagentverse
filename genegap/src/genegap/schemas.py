"""Task 1.1: data schemas. Every number in the system travels inside or alongside a Fact."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

Method = Literal["api", "llm_extracted", "curated", "computed"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Fact(BaseModel):
    """One sourced value. The critic checks every reported figure against these."""

    field: str                      # what the value is, e.g. "ac_an:afr"
    value: Any
    source: str                     # e.g. "gnomAD"
    record_id: str                  # e.g. "10-94781859-G-A" or "NCT01234567"
    url: str = ""                   # a human-checkable link
    method: Method = "api"
    retrieved_at: datetime = Field(default_factory=utcnow)
    quote: Optional[str] = None     # short supporting quote (under 15 words) for llm_extracted facts
    note: Optional[str] = None

    @computed_field  # deterministic id, so the same fact is never stored twice
    @property
    def id(self) -> str:
        raw = json.dumps([self.source, self.record_id, self.field, self.value], sort_keys=True, default=str)
        return "F-" + hashlib.sha1(raw.encode()).hexdigest()[:12]


class AncestryFrequency(BaseModel):
    group: str                      # gnomAD genetic ancestry group code, e.g. "afr"
    ac: int = Field(ge=0)           # allele count
    an: int = Field(ge=0)           # allele number (copies examined)
    fact_id: str

    @model_validator(mode="after")
    def _ac_not_above_an(self):
        if self.ac > self.an:
            raise ValueError(f"ac ({self.ac}) cannot exceed an ({self.an})")
        return self

    @property
    def af(self) -> Optional[float]:
        return self.ac / self.an if self.an else None


class Variant(BaseModel):
    variant_id: str                 # chrom-pos-ref-alt
    rsid: Optional[str] = None
    gene: Optional[str] = None
    dataset: str = ""
    classification: Optional[str] = None   # ClinVar, if looked up
    frequencies: list[AncestryFrequency] = []
    fact_ids: list[str] = []


class BaselineDemographics(BaseModel):
    nct_id: str
    scheme: Literal["race", "ethnicity", "sex", "age"]
    counts: dict[str, int]          # category label as reported -> participants
    total_participants: Optional[int] = None
    fact_ids: list[str] = []

    @property
    def missing_fraction(self) -> Optional[float]:
        total = sum(self.counts.values())
        if not total:
            return None
        miss = sum(v for k, v in self.counts.items() if "unknown" in k.lower() or "not reported" in k.lower())
        return miss / total


class Trial(BaseModel):
    nct_id: str
    title: str = ""
    status: str = ""
    phases: list[str] = []
    conditions: list[str] = []
    interventions: list[str] = []
    has_results: bool = False
    baseline: list[BaselineDemographics] = []
    fact_ids: list[str] = []


class GroupGap(BaseModel):
    group: str
    n_trial: int
    target_share: Optional[float] = None
    carrier_freq: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    expected_carriers: Optional[float] = None
    enrolment_ratio: Optional[float] = None   # trial share / target share
    carrier_ratio: Optional[float] = None     # trial carrier share / target carrier share
    required_n: Optional[int] = None
    insufficient_data: bool = False


class GapResult(BaseModel):
    variant_id: str
    gene: Optional[str] = None
    inheritance: Literal["dominant", "recessive", "x_linked"] = "dominant"
    mapping_version: str = "A"
    groups: list[GroupGap] = []
    fact_ids: list[str] = []


class Flag(BaseModel):
    variant_id: str
    claim: str
    evidence_tier: Optional[int] = Field(default=None, ge=1, le=5)
    citations: list[str] = []
    fact_ids: list[str] = []
    robust_across_mappings: Optional[bool] = None
    status: Literal["draft", "critic_passed", "human_approved", "rejected"] = "draft"
