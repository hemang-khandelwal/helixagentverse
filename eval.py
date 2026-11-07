"""B owns this module. Ground-truth records stay here, outside agent inputs."""

from contracts import EvaluationCase, GroundTruthRecord, RunResult, Score
from tools import HelixTools


def score(
    case: EvaluationCase, result: RunResult,
    ground_truth: list[GroundTruthRecord],
) -> Score:
    raise NotImplementedError("B8: separate metrics with clinical-review support")


def run_all(
    cases: list[EvaluationCase], tools: HelixTools,
    ground_truth: list[GroundTruthRecord],
) -> list[Score]:
    raise NotImplementedError("B7/B8: bind each cutoff; B_full then A_plus; baseline")
