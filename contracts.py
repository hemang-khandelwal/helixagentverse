"""Project Helix — frozen data contracts.

FROZEN once T0 lands this file. Import from it; never edit it locally.
If a field you need is missing, say so in the group chat and stop — a silent
edit here is the most expensive mistake available today.

Design rules this file encodes:

1.  The LLM may EXTRACT a number from prose but never COMPUTE with one.
    Extracted values carry their source so the UI can show them next to the
    sentence they came from.
2.  Only G1, G2 and G9 adjudicate. Every other check annotates: it says which
    claim it weakens and by how much, and cannot reject on its own.
3.  There is no confidence scalar. "4 of 4 applicable checks" is not a
    calibrated probability. Report named passed / warned / failed / unresolved
    lists instead.
4.  Temporal filtering is on FIRST PUBLIC AVAILABILITY, not journal issue
    year. Advance-online publication routinely predates the issue by a year.
5.  In the historical arms the pharmacogenomic tool may return population
    FREQUENCY ONLY. Association records carry today's knowledge and are the
    answer. The type system enforces this: those arms receive FrequencyRecord,
    which has no association fields to leak.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Literal

# --------------------------------------------------------------------------
# Vocabularies. Closed sets, so scoring is unambiguous.
# --------------------------------------------------------------------------

MechanismClass = Literal[
    "immune_hla",
    "immune_non_hla",
    "metabolic_pk",
    "on_target_mechanism",
    "off_target_pharmacology",
    "idiosyncratic_unknown",
]

CheckStatus = Literal["PASS", "WARN", "FAIL", "UNRESOLVED"]

Outcome = Literal[
    "SURVIVES",                   # carries its WARN and UNRESOLVED list
    "REJECTED",
    "REQUIRES_HUMAN_RESOLUTION",
    "ABSTAINED",                  # a correct answer for some cases
]

JudgeCall = Literal["CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE"]

Arm = Literal[
    "A_baseline",   # model alone: no tools, no retrieval, no date filter
    "A_plus",       # model + the same date-filtered evidence, no checks, no loop
    "B_full",       # full pipeline
    "C_live",       # judge free-text input, no ground truth claimed
]

Blinding = Literal["light", "heavy"]

CaseKind = Literal[
    "positive_association",   # a variant-level association was later established
    "alternative_mechanism",  # a real mechanism, but not the one under test
    "insufficient_evidence",  # correct answer is to decline to name a variant
    "evidence_against",       # correct answer is to reject a proposed mechanism
]

EvidenceSource = Literal["cache_replay", "live"]

# Which checks may reject. Everything else annotates only.
ADJUDICATING_CHECKS: frozenset[str] = frozenset({"G1", "G2", "G9"})
ANNOTATING_CHECKS: frozenset[str] = frozenset({"G3", "G4", "G5", "G6", "G7", "G8"})
ALL_CHECKS: frozenset[str] = ADJUDICATING_CHECKS | ANNOTATING_CHECKS


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Citation:
    """A retrieved paper.

    `first_available` is the date the work first became publicly readable —
    advance-online, preprint, or issue date, whichever is earliest. G1 filters
    on this, NOT on `issue_year`. A 2008-issue paper posted online in May 2007
    is inside an inclusive 2007 window and voids the case it leaks into.
    """
    pmid: str
    title: str
    first_available: _dt.date
    issue_year: int | None = None
    source: str = "europepmc"

    def within(self, max_year: int) -> bool:
        return self.first_available <= _dt.date(max_year, 12, 31)


@dataclass(frozen=True)
class FrequencyRecord:
    """Population frequency only. Safe in every arm.

    Deliberately has no association, evidence-level or clinical-annotation
    field: allele frequencies are stable facts, association records are the
    answer we are hiding. If you find yourself wanting to add one, you want
    AssociationRecord, which the historical arms must never receive.
    """
    variant: str
    gene: str
    # ancestry -> (low, point, high) carrier frequency estimates
    carrier_frequency: dict[str, tuple[float, float, float]]
    source: str
    source_version: str

    def carrier_point(self, ancestry: str) -> float | None:
        v = self.carrier_frequency.get(ancestry)
        return None if v is None else v[1]

    def carrier_high(self, ancestry: str) -> float | None:
        """Upper plausible bound. G3 uses this, to be generous to the hypothesis."""
        v = self.carrier_frequency.get(ancestry)
        return None if v is None else v[2]


@dataclass(frozen=True)
class AssociationRecord:
    """Variant-phenotype association as a database asserts it TODAY.

    MODE C ONLY. Passing one of these into A_plus or B_full is a leak, not a
    feature: tools.py must refuse it and tests must assert the refusal.
    """
    variant: str
    phenotype: str
    evidence_level: str
    source: str
    annotation_date: _dt.date | None


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Phenotype:
    """A blinded clinical toxicity description.

    Structured fields are EXTRACTED from the narrative, never computed. None
    means the narrative does not state it — leave it None rather than guessing,
    because an UNRESOLVED check is honest and a guessed value is not.

    Note what is absent: there is no `multi_ancestry_data_available` flag.
    Whether multi-ancestry incidence data exists is derived at check time from
    the in-window citation set. A hand-filled flag would smuggle in today's
    knowledge and quietly contaminate G6.
    """
    case_id: str                                   # "P1", "N3"
    blinding: Blinding
    narrative: str
    max_year: int                                  # inclusive cutoff

    ae_rate: float | None = None                   # fraction, e.g. 0.065
    ae_rate_range: tuple[float, float] | None = None   # (low, high); often wide
    dose_dependent: bool | None = None
    dose_gradient_population_wide: bool | None = None  # smooth across everyone
    onset_days_median: float | None = None
    onset_days_range: tuple[float, float] | None = None
    rechallenge_accelerated: bool | None = None
    # ancestry -> (relative incidence, number of events observed in that group)
    ancestry_incidence: dict[str, tuple[float, int]] | None = None
    affected_tissue: str | None = None

    # Provenance of each extracted field, for the UI and for the audit.
    extraction_sources: dict[str, str] = field(default_factory=dict)

    def ae_rate_low(self) -> float | None:
        """Lower plausible bound. G3 uses this, to be generous to the hypothesis."""
        if self.ae_rate_range is not None:
            return self.ae_rate_range[0]
        return self.ae_rate


@dataclass(frozen=True)
class Hypothesis:
    mechanism_class: MechanismClass
    gene_family: str | None                # "HLA class II", "UGT"
    specific_candidate: str | None         # "HLA-B*57:01"; None is legitimate
    justification: str
    supporting_pmids: list[str]
    falsifying_evidence_sought: str

    # Does the proposed mechanism act through parent-drug or metabolite
    # EXPOSURE LEVEL? Declared by the generator, compared by G4 against
    # observed dose-dependency. A reactive-metabolite hypothesis is
    # metabolic_pk with exposure_mediated=False, and G4 then returns
    # UNRESOLVED instead of penalising a valid idea.
    exposure_mediated: bool | None = None

    # Declining is a first-class correct answer, not a failure.
    abstains: bool = False


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    """One check's finding.

    `observed` is rendered verbatim in the UI, so write it for a reader:
        "carrier freq 0.062 (hi), AE rate 0.065 (lo) -> explains at most 95%"
        "2 ancestry groups, 4 and 7 events: too few to detect a gradient"
    `weakens` says which claim this bears on, for the annotating checks. An
    annotating check must never be read as a rejection.
    """
    check_id: str                          # "G1" ... "G9"
    status: CheckStatus
    observed: str
    detail: str
    weakens: str | None = None
    # Set when UNRESOLVED: names the fact that would settle it. This is what
    # drives tool selection in the orchestrator.
    missing_field: str | None = None

    @property
    def adjudicating(self) -> bool:
        return self.check_id in ADJUDICATING_CHECKS


@dataclass(frozen=True)
class CheckReport:
    results: list[CheckResult]

    def _by(self, status: CheckStatus) -> list[str]:
        return [r.check_id for r in self.results if r.status == status]

    @property
    def passed(self) -> list[str]:
        return self._by("PASS")

    @property
    def warned(self) -> list[str]:
        return self._by("WARN")

    @property
    def failed(self) -> list[str]:
        return self._by("FAIL")

    @property
    def unresolved(self) -> list[str]:
        return self._by("UNRESOLVED")

    @property
    def adjudicating_failures(self) -> list[str]:
        """The only failures that may reject a hypothesis."""
        return [r.check_id for r in self.results
                if r.status == "FAIL" and r.adjudicating]

    def gaps(self) -> list[tuple[str, str]]:
        """(check_id, missing_field) for the orchestrator's tool selection."""
        return [(r.check_id, r.missing_field) for r in self.results
                if r.status == "UNRESOLVED" and r.missing_field]

    # Deliberately absent: a confidence scalar. Do not add one.


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str                            # names the deciding check or judge call
    warned: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# The agentic loop, recorded so it can be audited
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolCall:
    round_index: int
    triggered_by_check: str | None         # the UNRESOLVED check that prompted it
    missing_field: str | None
    tool: str                              # "fetch_literature", "fetch_frequency", ...
    arguments: dict[str, str | int]
    n_results: int
    n_excluded_by_date: int
    skipped_reason: str | None = None      # set when the model chose not to spend


# --------------------------------------------------------------------------
# Output — the one object the UI and the evaluation harness both read
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    case_id: str
    arm: Arm
    blinding: Blinding | None
    max_year: int
    evidence_source: EvidenceSource        # the UI must label replay vs live

    hypotheses: list[Hypothesis] = field(default_factory=list)
    check_reports: list[CheckReport] = field(default_factory=list)   # parallel
    verdicts: list[Verdict] = field(default_factory=list)            # parallel
    judge_calls: list[JudgeCall] = field(default_factory=list)       # parallel

    citations: list[Citation] = field(default_factory=list)
    n_papers_excluded: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    retrieval_rounds: int = 0
    elapsed_seconds: float = 0.0
    timed_out: bool = False

    human_decision: Literal["APPROVE", "REJECT"] | None = None
    human_reason: str | None = None

    def surviving(self) -> list[Hypothesis]:
        return [h for h, v in zip(self.hypotheses, self.verdicts)
                if v.outcome == "SURVIVES"]

    def abstained(self) -> bool:
        return any(v.outcome == "ABSTAINED" for v in self.verdicts)

    def temporal_violations(self) -> list[Citation]:
        """Must always be empty in a historical arm. tests/test_temporal.py."""
        if self.arm == "C_live":
            return []
        return [c for c in self.citations if not c.within(self.max_year)]


# --------------------------------------------------------------------------
# Ground truth and scoring. Only eval.py may import these.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AcceptableAnswer:
    """One admissible answer. Cases have SETS of these, not single answers.

    N2's description is compatible with more than one genuine pharmacogenetic
    association, so a single "correct" allele would mark right answers wrong.
    Written by a pharmacology reviewer BEFORE any model output is seen.
    """
    mechanism_class: MechanismClass
    gene_family: str | None
    specific_candidate: str | None         # None = naming no allele is correct
    rationale: str


@dataclass(frozen=True)
class GroundTruth:
    case_id: str
    kind: CaseKind
    acceptable: list[AcceptableAnswer]
    abstention_is_correct: bool
    # Audit trail for the cutoff, so a judge can check it.
    discovery_pmid: str | None
    discovery_first_available: _dt.date | None
    cutoff_rationale: str


@dataclass(frozen=True)
class Score:
    """Three separate numbers. Never combine them into one.

    `unjustified_allele_naming` is a HARM metric, not an error metric: it
    counts allele names asserted without in-window variant-level evidence.
    Reducing it at equal mechanism accuracy is the project's primary claim.
    """
    case_id: str
    arm: Arm
    blinding: Blinding | None
    mechanism_class_correct: bool
    gene_family_correct: bool
    unjustified_allele_naming: int
    abstained: bool
    abstention_was_correct: bool | None
    notes: str = ""