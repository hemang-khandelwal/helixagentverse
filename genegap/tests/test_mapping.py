"""Tests for task 2.4: applying the ancestry mapping to trial counts."""
import pytest

from genegap.mapping import (
    AncestryMapping,
    MappingError,
    MappingRow,
    load_all_mappings,
    load_mapping,
    map_counts,
    map_counts_all_versions,
)

HEADER = "trial_category,gnomad_group,weight,confidence,source,rationale\n"


def write(tmp_path, body, name="ancestry_mapping_T.csv"):
    p = tmp_path / name
    p.write_text(HEADER + body)
    return p


def simple(rows):
    return AncestryMapping(version="T", path=type("P", (), {"name": "test.csv"})(),
                           rows=[MappingRow(*r) for r in rows])


# ---- the real config files ----
def test_all_three_real_mapping_versions_load():
    mappings = load_all_mappings()
    assert set(mappings) >= {"A", "B", "C"}
    for version, m in mappings.items():
        assert m.rows, f"mapping {version} is empty"


def test_version_a_is_fully_cited_and_bc_are_marked_placeholder():
    mappings = load_all_mappings()
    assert mappings["A"].placeholder_rows == []
    assert mappings["B"].placeholder_rows, "version B weights must be marked PLACEHOLDER until cited"
    assert mappings["C"].placeholder_rows


def test_unknown_is_never_assigned_in_any_real_mapping():
    for m in load_all_mappings().values():
        for row in m.rows:
            if "unknown" in row.trial_category.lower() or "not reported" in row.trial_category.lower():
                assert row.gnomad_group == "" and row.weight == 0.0


# ---- validation ----
def test_weights_that_do_not_sum_to_one_are_rejected(tmp_path):
    p = write(tmp_path, "Asian,eas,0.6,medium,src,why\nAsian,sas,0.3,medium,src,why\n")
    with pytest.raises(MappingError, match="sum to 0.900"):
        load_mapping(p)


def test_a_group_outside_gnomad_v4_is_rejected(tmp_path):
    p = write(tmp_path, "White,european,1.0,high,src,why\n")
    with pytest.raises(MappingError, match="not a gnomAD v4 group"):
        load_mapping(p)


def test_assigning_unknown_to_a_group_is_rejected(tmp_path):
    p = write(tmp_path, "Unknown or Not Reported,nfe,1.0,high,src,why\n")
    with pytest.raises(MappingError, match="never be assigned"):
        load_mapping(p)


def test_bad_confidence_and_bad_weight_are_rejected(tmp_path):
    with pytest.raises(MappingError, match="confidence"):
        load_mapping(write(tmp_path, "White,nfe,1.0,certain,src,why\n"))
    with pytest.raises(MappingError, match="weight must be a number"):
        load_mapping(write(tmp_path, "White,nfe,lots,high,src,why\n"))
    with pytest.raises(MappingError, match="between 0 and 1"):
        load_mapping(write(tmp_path, "White,nfe,1.4,high,src,why\n"))


def test_missing_columns_are_rejected(tmp_path):
    p = tmp_path / "ancestry_mapping_X.csv"
    p.write_text("trial_category,gnomad_group\nWhite,nfe\n")
    with pytest.raises(MappingError, match="missing columns"):
        load_mapping(p)


# ---- applying the mapping ----
def test_one_to_one_mapping_moves_counts_across_unchanged():
    m = simple([("White", "nfe", 1.0, "high", "src", "why"),
                ("Black or African American", "afr", 1.0, "high", "src", "why")])
    out = map_counts({"White": 800, "Black or African American": 200}, m)
    assert out.groups == {"nfe": 800.0, "afr": 200.0}
    assert out.total_reported == 1000 and out.missing == 0
    assert out.shares() == pytest.approx({"nfe": 0.8, "afr": 0.2})


def test_split_weights_divide_a_category():
    m = simple([("Asian", "eas", 0.6, "medium", "src", "why"), ("Asian", "sas", 0.4, "medium", "src", "why")])
    out = map_counts({"Asian": 100}, m)
    assert out.groups == pytest.approx({"eas": 60.0, "sas": 40.0})
    assert out.total_mapped == pytest.approx(100.0)


def test_unknown_is_counted_as_missing_and_never_reassigned():
    m = simple([("White", "nfe", 1.0, "high", "src", "why"),
                ("Unknown or Not Reported", "", 0.0, "high", "src", "why")])
    out = map_counts({"White": 90, "Unknown or Not Reported": 10}, m)
    assert out.groups == {"nfe": 90.0}
    assert out.missing == 10
    assert out.missing_fraction == pytest.approx(0.1)
    assert any("never assigned to an ancestry group" in n for n in out.notes)


def test_a_missing_category_absent_from_the_file_is_still_not_reassigned():
    # "Not Reported" has no row at all; it must land in missing, not unmapped.
    m = simple([("White", "nfe", 1.0, "high", "src", "why")])
    out = map_counts({"White": 90, "Not Reported": 10}, m)
    assert out.missing == 10 and out.unmapped == {}


def test_an_unmapped_category_is_reported_not_dropped_silently():
    m = simple([("White", "nfe", 1.0, "high", "src", "why")])
    out = map_counts({"White": 90, "Martian": 10}, m)
    assert out.unmapped == {"Martian": 10}
    assert out.groups == {"nfe": 90.0}
    assert any("No mapping row for" in n and "Martian" in n for n in out.notes)


def test_category_matching_ignores_case_and_whitespace():
    m = simple([("White", "nfe", 1.0, "high", "src", "why")])
    assert map_counts({"  white  ": 10}, m).groups == {"nfe": 10.0}


def test_all_versions_run_over_the_same_counts():
    counts = {"White": 800, "Black or African American": 150, "Asian": 50}
    out = map_counts_all_versions(counts)
    assert set(out) >= {"A", "B", "C"}
    for version, mapped in out.items():
        assert mapped.total_reported == 1000
        # The mappings move participants around but must never create or destroy them.
        assert mapped.total_mapped == pytest.approx(1000.0)


def test_the_three_versions_genuinely_disagree():
    """If every mapping gave the same answer the robustness check would be theatre."""
    counts = {"Asian": 100}
    out = map_counts_all_versions(counts)
    eas_shares = {v: m.groups.get("eas", 0.0) for v, m in out.items()}
    assert len(set(eas_shares.values())) > 1, f"all mappings agree on eas: {eas_shares}"
