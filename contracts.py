"""A1: shared contracts for the prospective Helix screen.

Import these types instead of inventing module-specific dictionaries. Coordinate
changes with the whole team. Dates are datetime.date in Python and ISO YYYY-MM-DD
in files. Candidate objects are proposals: the verifier, not their constructor,
decides whether their evidence is sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from math import isfinite
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

CheckId = Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
CheckStatus = Literal["PASS", "WARN", "FAIL", "UNRESOLVED"]
Tier = Literal["A", "B", "C"]
Arm = Literal["A_baseline", "A_plus", "B_full", "C_live"]
NodeKind = Literal["Drug", "Route", "Gene", "Variant", "Phenotype", "Analog", "Population"]
ToolName = Literal[
    "fetch_compound", "similar_compounds", "fetch_pgx",
    "fetch_frequency", "fetch_literature",
]

# The latest task-split PDF specifies four adjudicating checks (B1/B2).
# C4 is topicality. C9/GTEx is outside this task split.
ADJUDICATING_CHECKS = frozenset({"C1", "C2", "C3", "C4"})
ANNOTATING_CHECKS = frozenset({"C5", "C6", "C7", "C8"})
ALL_CHECKS = ADJUDICATING_CHECKS | ANNOTATING_CHECKS


@dataclass(frozen=True)
class Citation:
    source_id: str
    title: str
    url: str
    first_available: date
    supporting_text: str

    def within(self, max_date: date) -> bool:
        """Inclusive cutoff, using first public availability, not issue year."""
        return self.first_available <= max_date


# DATA FILE RECORDS: B supplies the files; A implements their loaders.
@dataclass(frozen=True)
class CompoundRecord:
    compound_id: str
    name: str
    smiles: str
    target_genes: list[str]
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class PgxAssociationRecord:
    record_id: str
    drug_name: str
    gene: str
    allele: str | None
    phenotype: str
    evidence_level: str
    guideline: str | None
    assay_available: bool | None
    source_id: str
    source_url: str
    first_available: date
    supporting_text: str


@dataclass(frozen=True)
class FrequencyRecord:
    variant: str
    gene: str
    ancestry: str
    frequency_kind: Literal["allele", "carrier"]
    frequency_low: float
    frequency_point: float
    frequency_high: float
    source_id: str
    source_url: str
    source_version: str
    first_available: date

    def __post_init__(self) -> None:
        values = (self.frequency_low, self.frequency_point, self.frequency_high)
        if self.frequency_kind not in {"allele", "carrier"}:
            raise ValueError("frequency_kind must be allele or carrier")
        if not all(isfinite(value) for value in values):
            raise ValueError("Frequency values must be finite")
        if not 0 <= values[0] <= values[1] <= values[2] <= 1:
            raise ValueError("Frequencies must satisfy 0 <= low <= point <= high <= 1")


@dataclass(frozen=True)
class BiomarkerRecord:
    drug_name: str
    gene: str
    phenotype: str
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    drug_name: str
    kind: Literal["known_association", "no_known_association"]
    max_date: date
    cutoff_source_id: str
    cutoff_source_url: str
    cutoff_rationale: str


@dataclass(frozen=True)
class GroundTruthRecord:
    """Evaluation-only answer. Never pass this object to the agent or tools."""

    case_id: str
    gene: str
    phenotype: str
    allele: str | None
    source_ids: list[str]


@dataclass(frozen=True)
class SeverityRecord:
    phenotype: str
    severity_class: Literal["programme_limiting", "tolerability", "uncertain"]
    rationale: str
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class LiteratureRecord:
    citation: Citation
    abstract: str


@dataclass(frozen=True)
class AnalogRecord:
    compound: CompoundRecord
    tanimoto: float
    shared_targets: list[str] = field(default_factory=list)


# GRAPH: edges refer to nodes and citations by their IDs.
@dataclass(frozen=True)
class GraphNode:
    node_id: str
    kind: NodeKind
    label: str
    analog_depth: int = 0


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: str
    citation_ids: list[str]


@dataclass
class EvidenceGraph:
    drug_node_id: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[str, EvidenceEdge] = field(default_factory=dict)
    citations: dict[str, Citation] = field(default_factory=dict)


@dataclass(frozen=True)
class FrontierItem:
    node_id: str
    heuristic_score: float
    reason: str


# CANDIDATES AND CHECKS: no confidence scalar or implied probability of harm.
@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    gene: str
    allele: str | None
    phenotype: str
    tier: Tier
    provenance_edge_ids: list[str]
    route: str | None = None
    analog_drug: str | None = None
    shared_feature: str | None = None
    tanimoto: float | None = None
    shared_target: bool | None = None
    shared_route: bool | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: CheckId
    status: CheckStatus
    reason: str

    def __post_init__(self) -> None:
        if self.check_id not in ALL_CHECKS:
            raise ValueError("Unknown check_id: " + self.check_id)
        if self.status not in {"PASS", "WARN", "FAIL", "UNRESOLVED"}:
            raise ValueError("Unknown check status: " + self.status)

    @property
    def adjudicating(self) -> bool:
        return self.check_id in ADJUDICATING_CHECKS


@dataclass(frozen=True)
class CheckReport:
    results: list[CheckResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        ids = [result.check_id for result in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("A report must contain at most one result per check")

    def ids_with_status(self, status: CheckStatus) -> list[str]:
        return [result.check_id for result in self.results if result.status == status]


@dataclass(frozen=True)
class Verdict:
    outcome: Literal["EMITTED", "REJECTED", "ABSTAINED"]
    reason: str


@dataclass
class CandidateResult:
    candidate: Candidate
    checks: CheckReport
    verdict: Verdict
    expected_carriers_per_1000: dict[str, float] = field(default_factory=dict)
    severity_class: str | None = None
    actionable: bool | None = None
    human_decision: Literal["ACCEPT", "REJECT"] | None = None
    human_reason: str | None = None


# LOOP: Python enforces these budgets, not the model's prompt.
@dataclass(frozen=True)
class RunBudget:
    max_expansions: int = 12
    max_analog_hops: int = 2
    max_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_expansions < 1 or self.max_analog_hops < 0:
            raise ValueError("Expansion budget must be positive; analog hops nonnegative")
        if not isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("Time budget must be finite and positive")


@dataclass(frozen=True)
class RunRequest:
    drug_name: str
    max_date: date | None
    arm: Arm = "C_live"
    smiles: str | None = None
    budget: RunBudget = field(default_factory=RunBudget)

    def __post_init__(self) -> None:
        if not self.drug_name.strip():
            raise ValueError("A drug name is required")
        if self.arm not in {"A_baseline", "A_plus", "B_full", "C_live"}:
            raise ValueError("Unknown evaluation arm")
        if self.arm in {"A_plus", "B_full"} and self.max_date is None:
            raise ValueError("Historical retrieval arms require an explicit max_date")


@dataclass(frozen=True)
class ExpansionDecision:
    action: Literal["EXPAND", "STOP"]
    reason: str
    node_id: str | None = None
    tool: ToolName | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteJudgment:
    route: str | None
    reason: str
    source_ids: list[str]


@dataclass(frozen=True)
class TransferJudgment:
    transferable: bool | None
    shared_feature: str | None
    reason: str
    source_ids: list[str]


@dataclass(frozen=True)
class ToolCall:
    node_id: str | None
    tool: str
    arguments: dict[str, Any]
    reason: str
    n_results: int
    n_excluded_by_date: int = 0


@dataclass
class RunResult:
    request: RunRequest
    graph: EvidenceGraph
    status: Literal["COMPLETE", "BUDGET_EXHAUSTED", "ABSTAINED", "ERROR"]
    evidence_mode: Literal["cache_replay", "live", "mixed"]
    candidates: list[CandidateResult] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class Score:
    case_id: str
    arm: Arm
    recall_at_5: float | None
    recall_at_10: float | None
    allele_candidates_reviewed: int
    supported_allele_candidates: int
    unjustified_allele_naming: int
    emitted_candidates: int
    abstained: bool
    notes: str = ""


# Machine-readable file schemas are generated from these exact dataclasses.
# No patient-level records or ground-truth answers belong in agent inputs.
DATA_FILE_RECORDS = {
    "compounds.json": ("json", CompoundRecord),
    "pgx.json": ("json", PgxAssociationRecord),
    "frequencies.csv": ("csv", FrequencyRecord),
    "biomarkers.csv": ("csv", BiomarkerRecord),
    "evaluation_cases.json": ("json", EvaluationCase),
    "ground_truth.json": ("json", GroundTruthRecord),
    "severity_table.json": ("json", SeverityRecord),
}


def data_file_schemas() -> dict[str, Any]:
    """Describe file fields without implementing A5's data loaders."""
    return {
        "schema_version": SCHEMA_VERSION,
        "files": {
            name: {
                "format": file_format,
                "record": record_type.__name__,
                "fields": [item.name for item in fields(record_type)],
            }
            for name, (file_format, record_type) in DATA_FILE_RECORDS.items()
        },
    }


def to_jsonable(value: Any) -> Any:
    """Encode shared records for fixtures/UI; loaders parse dates explicitly."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
