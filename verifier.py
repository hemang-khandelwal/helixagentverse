"""B owns this module. Implement B1-B4 here; no model calls in the verifier."""

# B writes the checker here. It uses explicit Python rules to inspect the AI's
# suggestions and explain the outcome.



from __future__ import annotations

from datetime import date

from contracts import (
    Candidate, CheckReport, CheckResult, EvidenceGraph, FrequencyRecord,
    PgxAssociationRecord, SeverityRecord, Verdict,
)


def check_c3(candidate: Candidate, graph: EvidenceGraph) -> CheckResult:
    # Check whether the evidence supports the specific allele being named. Evidence
    # about a gene alone must not be presented as evidence for an exact allele. Still
    # to build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("B1: allele specificity; implement first")


def verify(
    candidate: Candidate, graph: EvidenceGraph, *, max_date: date | None,
    frequencies: list[FrequencyRecord],
    associations: list[PgxAssociationRecord],
    severity_table: list[SeverityRecord],
) -> CheckReport:
    # Run the checks for this finding using its evidence, the date limit, population
    # frequencies, association records, and the medics' severity table. Return a
    # CheckReport. Still to build: the error below deliberately stops this unfinished
    # function.
    raise NotImplementedError("B1-B3: run the applicable checks")


def adjudicate(report: CheckReport, *, abstained: bool = False) -> Verdict:
    # Turn the check report into a decision. Only checks in ADJUDICATING_CHECKS can
    # reject a finding. Missing checks must not silently count as passes. Still to
    # build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("B4: apply adjudicating versus annotating policy")
