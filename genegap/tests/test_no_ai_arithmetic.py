"""Task 4.3: the no-AI-arithmetic rule, enforced structurally rather than spotted later.

Four angles, all offline and deterministic:
  1. The guard itself admits the computable provenance methods and refuses LLM extraction.
  2. The real fixture-driven M1 run cites no LLM-extracted fact behind any number.
  3. No module under agents/ or clients/ imports the arithmetic layer at all.
  4. Every member of Method is classified, so adding one breaks the build.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest

from genegap.fixtures import offline_clients
from genegap.gap import compute_gap
from genegap.guards import (
    COMPUTABLE_METHODS,
    NON_COMPUTABLE_METHODS,
    RULE,
    AIArithmeticError,
    computable,
    declared_methods,
    is_computable,
)
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import load_target_population
from genegap.schemas import Fact, GroupGap, Method
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import variants_for_gene

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "genegap"

# The layers that fetch and cite. Arithmetic lives in stats/ and gap.py only.
NON_COMPUTING_DIRS = ("agents", "clients")

# The numeric fields of a GroupGap. Any value here must trace to a computable fact.
NUMERIC_FIELDS = (
    "n_trial", "ac", "an", "allele_freq", "trial_share", "target_share",
    "carrier_freq", "ci_low", "ci_high", "expected_carriers", "enrolment_ratio",
    "carrier_ratio", "prob_fewer_than_k", "required_n",
)


def _fact(method: str, value: float = 0.42) -> Fact:
    return Fact(
        field="ac_an:eas",
        value=value,
        source="gnomAD",
        record_id="10-94781859-G-A",
        method=method,
    )


# ---------------------------------------------------------------- 1. the guard


@pytest.mark.parametrize("method", sorted(COMPUTABLE_METHODS))
def test_computable_admits_every_computable_method(method):
    """api, curated and computed values are operands and come back unchanged."""
    fact = _fact(method, value=1234)
    assert computable(fact) == 1234
    assert is_computable(fact) is True


def test_computable_refuses_an_llm_extracted_value():
    """An LLM may extract a number; that number may never be an operand."""
    fact = _fact("llm_extracted")
    assert is_computable(fact) is False
    with pytest.raises(AIArithmeticError) as excinfo:
        computable(fact)

    message = str(excinfo.value)
    # The message has to name the fact well enough to find it in the provenance log.
    assert fact.id in message
    assert "ac_an:eas" in message
    assert "gnomAD" in message
    assert "llm_extracted" in message


def test_the_guard_refuses_a_method_it_does_not_classify():
    """A pathway nobody has ruled on is refused, not quietly allowed."""
    fact = _fact("api")
    object.__setattr__(fact, "method", "vibes")  # bypass validation deliberately
    with pytest.raises(ValueError, match="does not classify"):
        computable(fact)


def test_the_rule_is_stated_in_plain_english_for_the_report():
    assert isinstance(RULE, str) and len(RULE.split()) > 40
    assert "language model" in RULE


# -------------------------------------------- 2. the real run cites no LLM fact


@pytest.fixture
def m1_run(tmp_path):
    """The M1 clopidogrel / CYP2C19 chain over the recorded payloads (same path as test_m1_end_to_end)."""
    store = Store(str(tmp_path / "guards.sqlite"))
    gnomad, clinvar, ctgov, _seeded = offline_clients(store)

    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == "rs4244285")

    runs = {
        version: compute_gap(variant, map_counts(counts, mapping), target,
                             inheritance=table.inheritance)
        for version, mapping in load_all_mappings().items()
    }
    return store, variant, runs


def test_no_number_in_the_m1_gap_table_rests_on_an_llm_extracted_fact(m1_run):
    """Every Fact cited behind the numbers of a real GapResult is computable.

    This passes today and fails the moment model output is wired into the compute path.
    """
    store, _variant, runs = m1_run
    assert runs, "the fixture produced no gap runs"

    checked = 0
    for version, run in runs.items():
        result = run.result
        numeric = [
            g for g in result.groups
            if any(getattr(g, f) not in (None, 0, 0.0) for f in NUMERIC_FIELDS)
        ]
        assert numeric, f"mapping {version} produced no numeric rows to check"
        assert result.fact_ids, f"mapping {version} reports numbers but cites no facts"

        for fid in result.fact_ids:
            fact = store.get_fact(fid)
            assert fact is not None, f"cited fact {fid} is not in the provenance store"
            assert fact.method not in NON_COMPUTABLE_METHODS, (
                f"mapping {version} cites fact {fid} (field={fact.field}, source={fact.source}) "
                f"with method={fact.method}; a reported number rests on LLM extraction"
            )
            # The guard must agree, not just the bare method string.
            computable(fact)
            checked += 1

    assert checked > 0


def test_every_group_with_allele_counts_cites_a_computable_source_fact(m1_run):
    """The ac/an behind each carrier frequency traces to a real, non-LLM gnomAD fact."""
    store, variant, runs = m1_run
    freq_facts = {f.group: f.fact_id for f in variant.frequencies}

    for version, run in runs.items():
        for g in run.result.groups:
            if g.ac is None and g.an is None:
                continue
            fid = freq_facts.get(g.group)
            assert fid, f"group {g.group} has counts under mapping {version} but no source fact"
            assert fid in run.result.fact_ids
            fact = store.get_fact(fid)
            assert fact is not None
            computable(fact)


# ------------------------------------------------- 3. static layer separation


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> list[str]:
    """Absolute dotted names this module imports, resolving relative imports."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package = _module_name(path).rsplit(".", 1)[0] if "." in _module_name(path) else _module_name(path)
    if path.name != "__init__.py":
        package = _module_name(path).rsplit(".", 1)[0]

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                climb = node.level - 1
                base = base[: len(base) - climb] if climb else base
                prefix = ".".join(base)
                target = f"{prefix}.{node.module}" if node.module else prefix
            else:
                target = node.module or ""
            found.append(target)
            found += [f"{target}.{alias.name}" for alias in node.names]
    return found


def _layer_modules(layer: str) -> list[Path]:
    directory = PACKAGE / layer
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py"))


def test_the_layer_directories_are_where_we_think_they_are():
    assert PACKAGE.is_dir(), f"{PACKAGE} not found; the static test would pass vacuously"
    assert (PACKAGE / "stats").is_dir()


@pytest.mark.parametrize("layer", NON_COMPUTING_DIRS)
def test_fetching_layers_do_not_import_the_arithmetic_layer(layer):
    """agents/ and clients/ fetch and cite; they do not compute.

    Skips (does not fail) while a layer is still empty, and asserts the rule for
    whatever modules do exist.
    """
    modules = [p for p in _layer_modules(layer) if p.name != "__init__.py" or p.read_text().strip()]
    if not modules:
        pytest.skip(f"src/genegap/{layer}/ has no modules yet")

    violations = []
    for path in modules:
        for imported in _imported_modules(path):
            if imported == "genegap.stats" or imported.startswith("genegap.stats."):
                violations.append(f"{path.relative_to(SRC)} imports {imported}")

    assert not violations, (
        "Arithmetic lives in stats/ and gap.py only. These modules import it directly:\n  "
        + "\n  ".join(violations)
    )


def test_the_static_check_would_actually_catch_a_violation(tmp_path):
    """Guard against the import walker silently matching nothing."""
    offender = PACKAGE / "agents" / "_probe_violation.py"
    offender.write_text("from ..stats import carrier_frequency\n")
    try:
        imported = _imported_modules(offender)
        assert any(m.startswith("genegap.stats") for m in imported), imported
    finally:
        offender.unlink()


# ------------------------------------------------ 4. the enum is fully covered


def test_every_method_is_classified_as_computable_or_not():
    """Adding a provenance pathway to Method must force a decision, not default to allowed."""
    declared = set(get_args(Method))
    classified = set(COMPUTABLE_METHODS) | set(NON_COMPUTABLE_METHODS)

    unclassified = declared - classified
    assert not unclassified, (
        f"Method value(s) {sorted(unclassified)} are not classified in genegap.guards. "
        f"Decide whether each may be used as an operand and add it to COMPUTABLE_METHODS "
        f"or NON_COMPUTABLE_METHODS."
    )

    stale = classified - declared
    assert not stale, f"genegap.guards classifies {sorted(stale)}, which Method no longer declares"

    assert declared_methods() == frozenset(declared)
    assert not (COMPUTABLE_METHODS & NON_COMPUTABLE_METHODS)


def test_every_declared_method_round_trips_through_the_guard():
    """No Method value falls through computable() into the 'unknown pathway' branch."""
    for method in get_args(Method):
        fact = _fact(method)
        if method in NON_COMPUTABLE_METHODS:
            with pytest.raises(AIArithmeticError):
                computable(fact)
        else:
            assert computable(fact) == fact.value


def test_groupgap_numeric_fields_list_is_complete():
    """If a numeric field is added to GroupGap, the end-to-end check must cover it."""
    skip = {"group", "k_threshold", "insufficient_data"}
    actual = {name for name in GroupGap.model_fields if name not in skip}
    missing = actual - set(NUMERIC_FIELDS)
    assert not missing, (
        f"GroupGap gained numeric field(s) {sorted(missing)}; add them to NUMERIC_FIELDS "
        f"so the no-AI-arithmetic end-to-end check covers them."
    )
