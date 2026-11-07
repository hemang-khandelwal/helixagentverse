"""B owns this module. Implement B1-B4 here; no model calls in the verifier."""

from __future__ import annotations

from datetime import date

from contracts import (
    Candidate, CheckReport, CheckResult, EvidenceGraph, FrequencyRecord,
    PgxAssociationRecord, SeverityRecord, Verdict,
)


def check_c3(candidate: Candidate, graph: EvidenceGraph) -> CheckResult:
    raise NotImplementedError("B1: allele specificity; implement first")


def verify(
    candidate: Candidate, graph: EvidenceGraph, *, max_date: date | None,
    frequencies: list[FrequencyRecord],
    associations: list[PgxAssociationRecord],
    severity_table: list[SeverityRecord],
) -> CheckReport:
    raise NotImplementedError("B1-B3: run the applicable checks")


def adjudicate(report: CheckReport, *, abstained: bool = False) -> Verdict:
    raise NotImplementedError("B4: apply adjudicating versus annotating policy")
