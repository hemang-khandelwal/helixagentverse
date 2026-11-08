"""Task 6.3: the results-and-limits generator, tested fully offline.

Two things matter here. The document must reproduce byte-for-byte from the committed
artifacts, and no figure in sections 1 or 2 may be a literal in the generator source,
so a stale number cannot be hardcoded past a refresh.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from genegap.eval import report_results
from genegap.eval.report_results import (
    CHECKER_FUNCTIONS,
    OUT_PATH,
    MappingState,
    find_checker_call_sites,
    generate,
    mapping_versions,
    read_mapping_state,
)

SOURCE_PATH = Path(report_results.__file__)

# The nine limits, by their leading clause. All must survive any edit.
REQUIRED_LIMITS = (
    "One drug-gene pair",
    "Mapping",
    "Race is not ancestry",
    "Hardy-Weinberg",
    "No HLA, no CYP2D6",
    "Invisible trials",
    "No temporal holdout",
    "One model, one run",
    "No clinical claim",
)


@pytest.fixture(scope="module")
def document():
    return generate()


def _section(text: str, heading: str) -> str:
    """The body of one '## ' section."""
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts:
        if part.startswith(heading):
            return part
    raise AssertionError(f"section {heading!r} not found")


# ------------------------------------------------------------- reproducibility


def test_the_committed_document_matches_what_the_generator_produces(document):
    assert OUT_PATH.exists(), f"{OUT_PATH} is generated and committed"
    assert document == OUT_PATH.read_text(), (
        "the committed RESULTS_AND_LIMITS.md has drifted from the artifacts; regenerate it"
    )


def test_the_generator_is_deterministic(document):
    """No timestamp, no ordering wobble: two runs must agree exactly."""
    assert generate() == document


# ------------------------------------------------- no hardcoded figures


def _source_numeric_literals() -> set:
    """Every numeric literal in the generator source, as floats."""
    tree = ast.parse(SOURCE_PATH.read_text(), filename=str(SOURCE_PATH))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if not isinstance(node.value, bool):
                found.add(float(node.value))
    return found


def _document_numbers(section_text: str) -> set:
    """Numeric tokens in a section, ignoring identifiers and single digits.

    Single digits and zero are excluded because they are structurally unavoidable in
    source (indices, precision, `or 0.0` defaults), not because they are exempt from
    the rule. No real figure is a single digit or zero.
    """
    cleaned = re.sub(r"rs\d+|NCT\d+|CYP\w+|\b\d+-\d+-[ACGT]+-[ACGT]+\b|gnomAD v\d+",
                     " ", section_text)
    out = set()
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", cleaned):
        bare = token.replace(",", "")
        if len(bare.replace(".", "")) < 2:
            continue
        if float(bare) == 0.0:
            continue
        out.add(float(bare))
    return out


def test_no_figure_in_section_1_is_a_literal_in_the_source(document):
    literals = _source_numeric_literals()
    numbers = _document_numbers(_section(document, "1. What we found"))
    assert numbers, "section 1 reported no numbers at all"
    overlap = numbers & literals
    assert not overlap, f"section 1 figures hardcoded in the generator: {sorted(overlap)}"


def test_no_figure_in_section_2_is_a_literal_in_the_source(document):
    literals = _source_numeric_literals()
    numbers = _document_numbers(_section(document, "2. How we know"))
    assert numbers, "section 2 reported no numbers at all"
    overlap = numbers & literals
    assert not overlap, f"section 2 figures hardcoded in the generator: {sorted(overlap)}"


def test_the_source_holds_no_large_numeric_literal():
    """A real figure (allele counts, participant counts) must never appear in source."""
    big = {v for v in _source_numeric_literals() if abs(v) >= 10}
    assert not big, f"suspicious numeric literals in the generator: {sorted(big)}"


# --------------------------------------------------------------- the nine limits


@pytest.mark.parametrize("clause", REQUIRED_LIMITS)
def test_every_limit_is_present(document, clause):
    section = _section(document, "3. What we cannot claim")
    assert clause in section, f"limit missing: {clause}"


def test_the_limits_are_numbered_one_to_nine(document):
    section = _section(document, "3. What we cannot claim")
    numbered = re.findall(r"^\*\*(\d)\.", section, flags=re.MULTILINE)
    assert numbered == [str(i) for i in range(1, 10)], numbered


def test_limit_two_reports_the_real_mapping_row_counts(document):
    section = _section(document, "3. What we cannot claim")
    for version in mapping_versions():
        state = read_mapping_state(version)
        if state.version == "A":
            continue
        assert str(state.weighted_rows) in section, (
            f"mapping {version} row count {state.weighted_rows} not reported"
        )


def test_limit_two_says_the_robustness_check_is_unexercised(document):
    """B and C are placeholders, so the check must not be implied to have passed."""
    section = _section(document, "3. What we cannot claim")
    assert "robust_across_mappings" in section
    assert "never been exercised" in section
    assert "NOT demonstrated" in section
    # It must not claim the finding survived anything.
    assert "survives all mappings" not in section.replace(
        "'survives all mappings' would be false", ""
    )


# ----------------------------------------------------------------- mapping state


def test_the_alternative_mappings_are_detected_as_placeholders():
    """This is the fact limit 2 rests on; if it changes, the document must change."""
    states = {v: read_mapping_state(v) for v in mapping_versions()}
    assert "A" in states and "B" in states and "C" in states
    assert states["A"].placeholder_rows == 0
    assert states["B"].all_placeholder is True
    assert states["C"].all_placeholder is True


def test_a_missing_mapping_file_is_reported_not_crashed():
    state = read_mapping_state("ZZ_does_not_exist")
    assert isinstance(state, MappingState)
    assert state.exists is False
    assert state.weighted_rows == 0


# --------------------------------------------------------------- wiring status


def test_wiring_status_reflects_the_real_call_sites(document):
    """Section 4 must match what the AST scan actually finds, either way."""
    section = _section(document, "4. Wiring status")
    sites = find_checker_call_sites()
    for name in CHECKER_FUNCTIONS:
        assert name in section
        if sites.get(name):
            for site in sites[name]:
                assert site in section, f"{name} call site {site} not reported"
        else:
            assert "not called from the report path" in section


def test_the_call_site_scan_finds_a_planted_call(tmp_path, monkeypatch):
    """Guard against the scanner silently finding nothing because it is broken."""
    fake = tmp_path / "src" / "pretend"
    fake.mkdir(parents=True)
    (fake / "wired.py").write_text(
        "from genegap.critic import criticise\n"
        "def go(r):\n    return criticise(r)\n"
    )
    monkeypatch.setattr(report_results, "ROOT", tmp_path)
    found = find_checker_call_sites()
    assert any("wired.py" in s for s in found["criticise"]), found


# --------------------------------------------------------------- section 2 honesty


def test_section_2_states_the_baseline_fabricated_nothing_without_softening(document):
    section = _section(document, "2. How we know")
    assert "fabricated nothing" in section
    assert "approximately correct" in section
    assert "ami" in section, "the ami agreement must be reported"


def test_section_1_names_the_two_most_divergent_groups_and_the_ratio(document):
    section = _section(document, "1. What we found")
    assert "most divergent" in section
    assert re.search(r"ratio of \*\*\d+\.\d+x\*\*", section), section[:400]
    assert "TIER" in section
