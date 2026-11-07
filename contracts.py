"""A1: shared contracts for the prospective Helix screen.

Import these types instead of inventing module-specific dictionaries. Coordinate
changes with the whole team. Dates are datetime.date in Python and ISO YYYY-MM-DD
in files. Candidate objects are proposals: the verifier, not their constructor,
decides whether their evidence is sufficient.
"""

# @dataclass builds a form-like record from the fields below it. frozen=True prevents
# replacing fields after creation, but lists inside can still change.
# default_factory=list gives every record its own empty list. self means the current
# record.

# Python reading guide: str means text; int means a whole number; float means a
# decimal; bool means True or False. list[str] means a list of text values. dict
# stores values under named keys. X | None allows a value to be unknown. Literal
# lists the expected choices; Python does not enforce those choices unless we add
# checks. -> describes what a function returns.

# Shared forms for the whole team. Each class describes one kind of information we
# pass between files. Agree with teammates before changing these forms.



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

ADJUDICATING_CHECKS = frozenset({"C1", "C2", "C3", "C4"})
ANNOTATING_CHECKS = frozenset({"C5", "C6", "C7", "C8"})
ALL_CHECKS = ADJUDICATING_CHECKS | ANNOTATING_CHECKS


@dataclass(frozen=True)
class Citation:
    # A source we can check, such as a research paper. Keep its link, the first date
    # people could read it, and the exact sentence supporting our claim.
    source_id: str
    title: str
    url: str
    first_available: date
    supporting_text: str

    def within(self, max_date: date) -> bool:
        # Check whether this source was available on or before max_date. A source
        # published exactly on the limit is allowed.
        """Inclusive cutoff, using first public availability, not issue year."""
        return self.first_available <= max_date


@dataclass(frozen=True)
class CompoundRecord:
    # Basic information about one drug. SMILES is a text description of its chemical
    # structure. target_genes lists genes linked to the drug's targets.
    compound_id: str
    name: str
    smiles: str
    target_genes: list[str]
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class PgxAssociationRecord:
    # One reported link between a drug, a gene or allele, and an effect. phenotype
    # means the effect being described. None means information is unknown.
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
    # How common a variant is in one population. Store fractions: 0.02 means 2%. low
    # and high describe the estimate's range. Allele frequency counts gene copies;
    # carrier frequency counts people carrying the variant. Keep them separate.
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
        # Python runs this automatically just after making the record. Reject the
        # invalid values listed below. These checks do not establish whether a
        # medical claim is true.
        values = (self.frequency_low, self.frequency_point, self.frequency_high)
        if self.frequency_kind not in {"allele", "carrier"}:
            raise ValueError("frequency_kind must be allele or carrier")
        if not all(isfinite(value) for value in values):
            raise ValueError("Frequency values must be finite")
        if not 0 <= values[0] <= values[1] <= values[2] <= 1:
            raise ValueError("Frequencies must satisfy 0 <= low <= point <= high <= 1")


@dataclass(frozen=True)
class BiomarkerRecord:
    # One reference-table entry used to choose test cases. This helps build our
    # answer sheet; it must not reveal that answer to the AI during a test.
    drug_name: str
    gene: str
    phenotype: str
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class EvaluationCase:
    # One drug we will test the programme on. max_date is the latest evidence date
    # allowed. cutoff_rationale explains why that date was chosen.
    case_id: str
    drug_name: str
    kind: Literal["known_association", "no_known_association"]
    max_date: date
    cutoff_source_id: str
    cutoff_source_url: str
    cutoff_rationale: str


@dataclass(frozen=True)
class GroundTruthRecord:
    # One answer reviewed by the medics. Think of this as the answer sheet for
    # marking a test. Do not let the AI see it while answering.
    """Evaluation-only answer. Never pass this object to the agent or tools."""

    case_id: str
    gene: str
    phenotype: str
    allele: str | None
    source_ids: list[str]


@dataclass(frozen=True)
class SeverityRecord:
    # The medics' description of how serious an effect is, with their reason and
    # source. uncertain is allowed when the evidence is unclear.
    phenotype: str
    severity_class: Literal["programme_limiting", "tolerability", "uncertain"]
    rationale: str
    source_id: str
    source_url: str
    first_available: date


@dataclass(frozen=True)
class LiteratureRecord:
    # A paper's source details plus its abstract: the short summary published with
    # the paper.
    citation: Citation
    abstract: str


@dataclass(frozen=True)
class AnalogRecord:
    # A related drug we might investigate. tanimoto compares chemical structures on a
    # 0-to-1 scale. Similar structure alone does not prove the same medical risk.
    compound: CompoundRecord
    tanimoto: float
    shared_targets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphNode:
    # One item in our evidence map: for example, a drug or gene. node_id is its
    # unique label inside the programme. analog_depth counts steps through related
    # drugs.
    node_id: str
    kind: NodeKind
    label: str
    analog_depth: int = 0


@dataclass(frozen=True)
class EvidenceEdge:
    # One connection between two items in the evidence map. For example: a drug is
    # processed by an enzyme. citation_ids lists the sources supporting that
    # connection.
    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: str
    citation_ids: list[str]


@dataclass
class EvidenceGraph:
    # Our evidence map. nodes stores the items, edges stores their connections, and
    # citations stores the sources. Each dictionary lets us find an item by its ID.
    drug_node_id: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[str, EvidenceEdge] = field(default_factory=dict)
    citations: dict[str, Citation] = field(default_factory=dict)


@dataclass(frozen=True)
class FrontierItem:
    # An item the AI could investigate next. heuristic_score is a rough priority for
    # searching; it is not a probability that a medical claim is true.
    node_id: str
    heuristic_score: float
    reason: str


@dataclass(frozen=True)
class Candidate:
    # One possible finding, waiting to be checked. A means evidence for this drug; B
    # means evidence from a related drug; C means a gene-level idea without a named
    # allele. provenance_edge_ids points to the evidence-map connections supporting
    # it.
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
    # The result of one check. PASS means its requirement was met; WARN means
    # caution; FAIL means its rule failed; UNRESOLVED means information is missing.
    # reason explains the result.
    check_id: CheckId
    status: CheckStatus
    reason: str

    def __post_init__(self) -> None:
        # Python runs this automatically just after making the record. Reject the
        # invalid values listed below. These checks do not establish whether a
        # medical claim is true.
        if self.check_id not in ALL_CHECKS:
            raise ValueError("Unknown check_id: " + self.check_id)
        if self.status not in {"PASS", "WARN", "FAIL", "UNRESOLVED"}:
            raise ValueError("Unknown check status: " + self.status)

    @property
    def adjudicating(self) -> bool:
        # True means this check is allowed to reject a finding. False means it only
        # adds information or warnings.
        return self.check_id in ADJUDICATING_CHECKS


@dataclass(frozen=True)
class CheckReport:
    # All checks for one possible finding. Each check ID can appear once. An empty
    # list means no checks are recorded, not that the finding passed.
    results: list[CheckResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Python runs this automatically just after making the record. Reject the
        # invalid values listed below. These checks do not establish whether a
        # medical claim is true.
        ids = [result.check_id for result in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("A report must contain at most one result per check")

    def ids_with_status(self, status: CheckStatus) -> list[str]:
        # Return the IDs of checks with the requested result. For example, asking for
        # FAIL collects the failed check IDs.
        return [result.check_id for result in self.results if result.status == status]


@dataclass(frozen=True)
class Verdict:
    # The decision after checking: show the finding, reject it, or record that the
    # system chose not to make a claim. Keep a readable reason.
    outcome: Literal["EMITTED", "REJECTED", "ABSTAINED"]
    reason: str


@dataclass
class CandidateResult:
    # One possible finding together with its checks and decision. It also holds the
    # human reviewer's response. Unknown information stays None.
    candidate: Candidate
    checks: CheckReport
    verdict: Verdict
    expected_carriers_per_1000: dict[str, float] = field(default_factory=dict)
    severity_class: str | None = None
    actionable: bool | None = None
    human_decision: Literal["ACCEPT", "REJECT"] | None = None
    human_reason: str | None = None


@dataclass(frozen=True)
class RunBudget:
    # Limits for one investigation: how many search steps, how far to follow related
    # drugs, and how many seconds to spend. The workflow code must actually enforce
    # these limits.
    max_expansions: int = 12
    max_analog_hops: int = 2
    max_seconds: float = 120.0

    def __post_init__(self) -> None:
        # Python runs this automatically just after making the record. Reject the
        # invalid values listed below. These checks do not establish whether a
        # medical claim is true.
        if self.max_expansions < 1 or self.max_analog_hops < 0:
            raise ValueError("Expansion budget must be positive; analog hops nonnegative")
        if not isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("Time budget must be finite and positive")


@dataclass(frozen=True)
class RunRequest:
    # What we ask the programme to investigate: a drug, an optional chemical
    # structure, a date limit, and a time/search budget. arm selects which test
    # version to run.
    drug_name: str
    max_date: date | None
    arm: Arm = "C_live"
    smiles: str | None = None
    budget: RunBudget = field(default_factory=RunBudget)

    def __post_init__(self) -> None:
        # Python runs this automatically just after making the record. Reject the
        # invalid values listed below. These checks do not establish whether a
        # medical claim is true.
        if not self.drug_name.strip():
            raise ValueError("A drug name is required")
        if self.arm not in {"A_baseline", "A_plus", "B_full", "C_live"}:
            raise ValueError("Unknown evaluation arm")
        if self.arm in {"A_plus", "B_full"} and self.max_date is None:
            raise ValueError("Historical retrieval arms require an explicit max_date")


@dataclass(frozen=True)
class ExpansionDecision:
    # The AI's next move: investigate an item using a tool, or stop. arguments
    # contains the values to pass to that tool; reason explains the choice.
    action: Literal["EXPAND", "STOP"]
    reason: str
    node_id: str | None = None
    tool: ToolName | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteJudgment:
    # The AI's reading of how the body processes the drug. Store its reason and
    # sources. None means it could not identify a route.
    route: str | None
    reason: str
    source_ids: list[str]


@dataclass(frozen=True)
class TransferJudgment:
    # Whether evidence about a related drug might apply here. shared_feature names
    # the biological reason for that comparison. None means we cannot decide yet.
    transferable: bool | None
    shared_feature: str | None
    reason: str
    source_ids: list[str]


@dataclass(frozen=True)
class ToolCall:
    # A record of one tool use: what was requested, why, how many results came back,
    # and how many were excluded because they were too new.
    node_id: str | None
    tool: str
    arguments: dict[str, Any]
    reason: str
    n_results: int
    n_excluded_by_date: int = 0


@dataclass
class RunResult:
    # Everything produced by one investigation. Both the screen and the marking code
    # read this same record. Keep rejected findings too, so people can inspect what
    # was ruled out.
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
    # Separate measurements for one test case. Recall measures how many expected
    # gene/effect pairs were found. Counts of supported and unsupported allele claims
    # help assess evidence quality. No single number here is a patient's risk.
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
    # Build the list of expected filenames and field names from the record
    # definitions above. This keeps the written file guide aligned with the Python
    # records.
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
    # Prepare records for saving as JSON, a common text-file format. Convert dates to
    # YYYY-MM-DD text, records to named values, and lists item by item.
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
