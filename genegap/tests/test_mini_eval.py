"""Task 6.1: the mini evaluation, tested fully offline.

Nothing here touches the network. The scorer reads the committed cache in
eval_out/baseline_responses.json, so the demo can show this result with the API
unreachable -- which is the point of caching every raw response.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from genegap import config as genegap_config
from genegap.eval import mini_eval
from genegap.eval.mini_eval import (
    CATEGORIES,
    BASELINE_CACHE,
    SUMMARY_PATH,
    EvalItem,
    GroundTruth,
    classify,
    coverage_sentence,
    ground_truth,
    headline,
    items,
    parse_answer,
    render_summary,
    run_arm_a,
    run_arm_b,
    score,
)

MODULE_PATH = Path(mini_eval.__file__)

# Modules that reach the network. None may be imported at module level on the
# scoring path, and none may appear inside a scoring function.
NETWORK_MODULES = (
    "google.genai", "google", "httpx", "requests", "urllib", "urllib3",
    "http", "socket", "aiohttp",
)
SCORING_FUNCTIONS = (
    "score", "classify", "absolute_error_pp", "headline", "coverage_sentence",
    "render_summary", "parse_answer", "_load_cache", "_save_cache",
)


# ------------------------------------------------------------------ the item set


def test_the_item_set_is_defined_in_code_and_within_the_cap():
    found = items()
    assert 1 <= len(found) <= 25
    assert len(found) == 10, "the 10 gnomAD v4 ancestry groups"
    assert all(isinstance(i, EvalItem) for i in found)
    assert len({i.item_id for i in found}) == len(found)
    assert all(i.variant_id == "10-94781859-G-A" for i in found)
    for i in found:
        assert i.group in i.question or i.group_name in i.question
        assert "carrier frequency" in i.question


# ----------------------------------------------------------- the cache is usable


@pytest.fixture(scope="module")
def cache():
    assert BASELINE_CACHE.exists(), (
        f"{BASELINE_CACHE} is missing; it is generated and committed so the scorer "
        f"can run with no network"
    )
    return json.loads(BASELINE_CACHE.read_text())


def test_the_committed_cache_holds_every_item_with_its_raw_response(cache):
    responses = cache["responses"]
    for item in items():
        assert item.item_id in responses, f"{item.item_id} missing from the cache"
        record = responses[item.item_id]
        for key in ("prompt", "response_text", "parsed_carrier_frequency",
                    "status", "attempts", "model"):
            assert key in record, f"{item.item_id} has no {key!r}"
        assert record["status"] in ("OK", "ERROR")
        if record["status"] == "OK":
            assert record["response_text"], "a successful call must keep its full text"


# ----------------------------------------------------------------- the classifier


def _gt(cf, insufficient=False):
    return GroundTruth("eas", cf, None, 1, 1000, insufficient, ["F-abc"])


def _ok(value, refused=False):
    return {"status": "OK", "parsed_carrier_frequency": value, "refused": refused}


@pytest.mark.parametrize("value,expected", [
    (0.515, "EXACT"),       # within 2 pp
    (0.500, "EXACT"),       # 1.5 pp
    (0.480, "CLOSE"),       # 3.5 pp
    (0.470, "CLOSE"),       # 4.5 pp
    (0.400, "WRONG"),       # 11.5 pp
    (0.010, "WRONG"),
])
def test_classifier_bands(value, expected):
    assert classify(_ok(value), _gt(0.515)) == expected


def test_classifier_marks_a_refusal():
    assert classify({"status": "OK", "parsed_carrier_frequency": None,
                     "refused": True}, _gt(0.515)) == "REFUSED"


def test_classifier_marks_an_out_of_range_value_as_fabricated():
    assert classify(_ok(1.4), _gt(0.515)) == "FABRICATED"
    assert classify(_ok(-0.2), _gt(0.515)) == "FABRICATED"


def test_classifier_marks_a_confident_number_with_no_real_value_as_fabricated():
    """ami is insufficient_data: our pipeline has no carrier frequency to give."""
    assert classify(_ok(0.3), _gt(None, insufficient=True)) == "FABRICATED"
    assert classify(_ok(0.3), None) == "FABRICATED"


def test_classifier_marks_a_failed_call_as_error():
    assert classify({"status": "ERROR", "parsed_carrier_frequency": None}, _gt(0.5)) == "ERROR"


@pytest.mark.parametrize("text,expected_value,expected_refused", [
    ('{"carrier_frequency": 0.52, "refused": false, "note": "x"}', 0.52, False),
    ('{"carrier_frequency": null, "refused": true, "note": "x"}', None, True),
    ('```json\n{"carrier_frequency": 0.3, "refused": false}\n```', 0.3, False),
    ("The carrier frequency is about 29%.", 0.29, False),
    ("I cannot know this value.", None, True),
    ("", None, False),
])
def test_parse_answer_is_tolerant(text, expected_value, expected_refused):
    value, refused, _note = parse_answer(text)
    assert value == pytest.approx(expected_value) if expected_value is not None else value is None
    assert refused is expected_refused


# ------------------------------------------------------- the scorer is offline


def test_the_scorer_reproduces_the_same_summary_from_cache():
    """Two scoring runs over the same cache must agree in every number."""
    arm_b = run_arm_b()
    truths = ground_truth()
    first = score(cache_path=BASELINE_CACHE, arm_b=arm_b, truths=truths)
    second = score(cache_path=BASELINE_CACHE, arm_b=arm_b, truths=truths)

    a, b = first.to_dict(), second.to_dict()
    a.pop("generated_at"), b.pop("generated_at")   # only the timestamp may differ
    assert a == b

    assert first.n_items == 10
    assert sum(first.counts.values()) == 10
    assert set(first.counts) == set(CATEGORIES)
    assert first.headline == headline(first.counts, 10, first.arm_b_total)


def test_the_committed_summary_matches_what_the_scorer_produces_now():
    """The committed markdown must not drift from the committed cache."""
    assert SUMMARY_PATH.exists(), f"{SUMMARY_PATH} is generated and committed"
    arm_b = run_arm_b()
    summary = score(cache_path=BASELINE_CACHE, arm_b=arm_b, truths=ground_truth())
    rendered = render_summary(summary, arm_b)
    committed = SUMMARY_PATH.read_text()

    # The timestamp line is expected to differ; nothing else may.
    def strip_ts(text):
        return "\n".join(l for l in text.splitlines() if not l.startswith("_Generated"))

    assert strip_ts(rendered) == strip_ts(committed)


def test_the_headline_counts_wrong_and_fabricated_only():
    counts = {"EXACT": 1, "CLOSE": 2, "WRONG": 3, "REFUSED": 1, "FABRICATED": 2, "ERROR": 1}
    assert "5 of 10" in headline(counts, 10, 10)


def test_the_coverage_sentence_reports_answered_versus_declined():
    counts = {"EXACT": 1, "CLOSE": 2, "WRONG": 0, "REFUSED": 7, "FABRICATED": 0, "ERROR": 0}
    text = coverage_sentence(counts, 10, 10)
    assert "3 of 10" in text and "declined or failed on 7" in text


def test_scoring_never_touches_the_network(monkeypatch):
    """Scoring with the SDK and every HTTP library poisoned must still work."""
    import builtins
    real_import = builtins.__import__

    def poisoned(name, *args, **kwargs):
        if name.split(".")[0] in ("google", "httpx", "requests", "urllib3", "socket"):
            raise AssertionError(f"scoring path tried to import {name!r}")
        return real_import(name, *args, **kwargs)

    arm_b = run_arm_b()
    truths = ground_truth()
    monkeypatch.setattr(builtins, "__import__", poisoned)
    summary = score(cache_path=BASELINE_CACHE, arm_b=arm_b, truths=truths)
    rendered = render_summary(summary, arm_b)
    assert summary.n_items == 10
    assert "Headline" in rendered


# ------------------------------------------- static: no network import on the path


def _tree():
    return ast.parse(MODULE_PATH.read_text(), filename=str(MODULE_PATH))


def test_no_network_module_is_imported_at_module_level():
    """The SDK must be imported lazily, inside Arm A only."""
    tree = _tree()
    offenders = []
    for node in tree.body:            # module level only
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name.split(".")[0] in NETWORK_MODULES]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in NETWORK_MODULES:
                offenders.append(node.module)
    assert not offenders, f"network modules imported at module level: {offenders}"


def test_no_scoring_function_imports_a_network_module():
    tree = _tree()
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in SCORING_FUNCTIONS:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Import):
                offenders += [f"{node.name}: {a.name}" for a in inner.names
                              if a.name.split(".")[0] in NETWORK_MODULES]
            elif isinstance(inner, ast.ImportFrom):
                root = (inner.module or "").split(".")[0]
                if root in NETWORK_MODULES:
                    offenders.append(f"{node.name}: {inner.module}")
    assert not offenders, f"scoring functions import network modules: {offenders}"


def test_arm_a_is_the_only_function_that_reaches_the_sdk():
    tree = _tree()
    sdk_users = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith("google"):
                sdk_users.add(node.name)
            elif isinstance(inner, ast.Import):
                if any(a.name.startswith("google") for a in inner.names):
                    sdk_users.add(node.name)
    assert sdk_users <= {"run_arm_a", "_ask_once"}, sdk_users


# ------------------------------------------------- Arm A resilience, with no network


class _Boom:
    """A client whose calls always fail with a retryable error."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("503 UNAVAILABLE: the model is overloaded")


def test_a_persistently_failing_item_is_recorded_as_error_and_the_run_continues(
    tmp_path, monkeypatch
):
    """A late failure must never lose items already completed."""
    cache_path = tmp_path / "responses.json"
    boom = _Boom()
    calls = {"n": 0}

    def flaky(client, model, prompt):
        calls["n"] += 1
        if "(eas)" in prompt:        # not bare "eas": it hides inside "at least"
            return boom()
        return '{"carrier_frequency": 0.3, "refused": false, "note": "ok"}'

    monkeypatch.setattr(mini_eval, "_ask_once", flaky)
    monkeypatch.setattr(mini_eval, "items", lambda: [
        EvalItem("rs1:afr", "CYP2C19", "rs1", "v", "afr", "African"),
        EvalItem("rs1:eas", "CYP2C19", "rs1", "v", "eas", "East Asian"),
        EvalItem("rs1:sas", "CYP2C19", "rs1", "v", "sas", "South Asian"),
    ])
    monkeypatch.setattr(genegap_config, "GEMINI_API_KEY", "test-key", raising=False)

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: object())

    cache = run_arm_a(
        eval_items=mini_eval.items(), cache_path=cache_path,
        model="test-model", max_attempts=3, sleep_fn=lambda s: None,
    )

    responses = cache["responses"]
    assert responses["rs1:afr"]["status"] == "OK"
    assert responses["rs1:sas"]["status"] == "OK", "a later item still ran after a failure"
    assert responses["rs1:eas"]["status"] == "ERROR"
    assert responses["rs1:eas"]["attempts"] == 3, "retried up to the limit"
    assert "503" in responses["rs1:eas"]["error"]

    # Everything was persisted to disk as it went, not only at the end.
    on_disk = json.loads(cache_path.read_text())["responses"]
    assert set(on_disk) == {"rs1:afr", "rs1:eas", "rs1:sas"}


def test_a_cached_item_is_not_re_asked(tmp_path, monkeypatch):
    cache_path = tmp_path / "responses.json"
    cache_path.write_text(json.dumps({
        "model": "test-model",
        "responses": {"rs1:afr": {"item_id": "rs1:afr", "status": "OK",
                                  "response_text": "{}", "parsed_carrier_frequency": 0.1,
                                  "refused": False, "attempts": 1, "prompt": "p",
                                  "model": "test-model"}},
    }))
    asked = []

    monkeypatch.setattr(mini_eval, "_ask_once",
                        lambda c, m, p: asked.append(p) or '{"carrier_frequency": 0.2}')
    monkeypatch.setattr(genegap_config, "GEMINI_API_KEY", "test-key", raising=False)
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: object())

    run_arm_a(
        eval_items=[EvalItem("rs1:afr", "G", "rs1", "v", "afr", "African")],
        cache_path=cache_path, model="test-model", sleep_fn=lambda s: None,
    )
    assert asked == [], "an item already in the cache must not be re-asked"


# ------------------------------------------------------------------------ Arm B


def test_arm_b_runs_offline_and_its_verification_layers_are_clean():
    arm_b = run_arm_b()
    assert arm_b["tier"] in ("TIER A", "TIER B")
    assert len(arm_b["items"]) == 10
    assert all(i["language_passed"] for i in arm_b["items"]), [
        (i["group"], i["language_failed_rules"]) for i in arm_b["items"]
        if not i["language_passed"]
    ]
    assert arm_b["n_cited_facts"] > 0
    for i in arm_b["items"]:
        assert i["cited_fact_ids"], f"{i['group']} states a finding with no cited fact"


def test_arm_b_ground_truth_covers_every_item():
    truths = ground_truth()
    for item in items():
        assert item.group in truths
    assert truths["ami"].insufficient_data is True
    assert truths["ami"].carrier_freq is None
    assert truths["eas"].carrier_freq is not None
