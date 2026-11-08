"""Task 6.1: the mini evaluation.

Two arms: the model answering from memory with no tools (Arm A), and our offline
pipeline over committed fixtures (Arm B, which is ground truth). The scorer is pure
and reads only the cached responses, so the demo can show the result with the API
unreachable.
"""
from .mini_eval import (
    coverage_sentence,
    CATEGORIES,
    EvalItem,
    GroundTruth,
    Summary,
    classify,
    ground_truth,
    headline,
    items,
    render_summary,
    run_arm_a,
    run_arm_b,
    score,
)

__all__ = [
    "CATEGORIES", "EvalItem", "GroundTruth", "Summary", "classify", "coverage_sentence", "ground_truth",
    "headline", "items", "render_summary", "run_arm_a", "run_arm_b", "score",
]
