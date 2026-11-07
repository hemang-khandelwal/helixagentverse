"""B owns this module. Ground-truth records stay here, outside agent inputs."""

# B measures performance here. Compare the programme's findings with answers reviewed
# by the medics. Keep the answer sheet hidden from the AI.



from contracts import EvaluationCase, GroundTruthRecord, RunResult, Score
from tools import HelixTools


def score(
    case: EvaluationCase, result: RunResult,
    ground_truth: list[GroundTruthRecord],
) -> Score:
    # Compare one investigation with the medics' answer sheet. Report separate
    # measures of correct findings and unsupported claims. Still to build: the error
    # below deliberately stops this unfinished function.
    raise NotImplementedError("B8: separate metrics with clinical-review support")


def run_all(
    cases: list[EvaluationCase], tools: HelixTools,
    ground_truth: list[GroundTruthRecord],
) -> list[Score]:
    # Run the test cases and collect their scores. Apply each case's own date limit.
    # Run the full system before replaying its evidence into A_plus. Still to build:
    # the error below deliberately stops this unfinished function.
    raise NotImplementedError("B7/B8: bind each cutoff; B_full then A_plus; baseline")
