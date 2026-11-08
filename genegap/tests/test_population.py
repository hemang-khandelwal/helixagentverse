"""Tests for task 2.5: the target-population table."""
import pytest

from genegap.population import PopulationError, load_target_population, load_target_populations

HEADER = "population_id,label,gnomad_group,share,source,quote,year,geography,basis,caveat\n"


def write(tmp_path, body):
    p = tmp_path / "target_population.csv"
    p.write_text(HEADER + body)
    return p


def test_the_real_table_loads_and_every_population_sums_to_one():
    pops = load_target_populations()
    assert pops
    for pid, pop in pops.items():
        assert sum(r.share for r in pop.rows) == pytest.approx(1.0, abs=0.01), pid


def test_the_real_table_declares_itself_assumption_based():
    """The shipped table is placeholder shares, so it must say so loudly (D5)."""
    pop = load_target_population("us_acs_antiplatelet")
    assert pop.is_assumption_based
    assert any("ASSUMPTION" in n for n in pop.notes)


def test_shares_that_do_not_sum_to_one_are_rejected(tmp_path):
    p = write(tmp_path, "x,X,nfe,0.5,src,,2024,UK,burden,\nx,X,afr,0.2,src,,2024,UK,burden,\n")
    with pytest.raises(PopulationError, match="sum to 0.700"):
        load_target_populations(p)


def test_a_row_without_a_source_is_rejected(tmp_path):
    p = write(tmp_path, "x,X,nfe,1.0,,,2024,UK,burden,\n")
    with pytest.raises(PopulationError, match="No source, no row"):
        load_target_populations(p)


def test_a_long_quote_is_rejected(tmp_path):
    quote = " ".join(["word"] * 20)
    p = write(tmp_path, f'x,X,nfe,1.0,src,"{quote}",2024,UK,burden,\n')
    with pytest.raises(PopulationError, match="keep it under 15"):
        load_target_populations(p)


def test_a_non_gnomad_group_is_rejected(tmp_path):
    p = write(tmp_path, "x,X,caucasian,1.0,src,,2024,UK,burden,\n")
    with pytest.raises(PopulationError, match="not a gnomAD v4 ancestry group"):
        load_target_populations(p)


def test_a_bad_basis_is_rejected(tmp_path):
    p = write(tmp_path, "x,X,nfe,1.0,src,,2024,UK,vibes,\n")
    with pytest.raises(PopulationError, match="basis must be one of"):
        load_target_populations(p)


def test_a_duplicate_group_in_one_population_is_rejected(tmp_path):
    p = write(tmp_path, "x,X,nfe,0.5,src,,2024,UK,burden,\nx,X,nfe,0.5,src,,2024,UK,burden,\n")
    with pytest.raises(PopulationError, match="appears twice"):
        load_target_populations(p)


def test_burden_backed_rows_do_not_trigger_the_assumption_warning(tmp_path):
    p = write(tmp_path, "x,X,nfe,0.7,Registry 2024,,2024,UK,burden,\nx,X,afr,0.3,Registry 2024,,2024,UK,burden,\n")
    pop = load_target_populations(p)["x"]
    assert not pop.is_assumption_based and pop.notes == []


def test_partly_assumed_population_reports_the_assumed_share(tmp_path):
    p = write(tmp_path, "x,X,nfe,0.7,Registry,,2024,UK,burden,\nx,X,afr,0.3,Guess,,2024,UK,assumption,\n")
    pop = load_target_populations(p)["x"]
    assert pop.assumption_share == pytest.approx(0.3)
    assert any("30%" in n for n in pop.notes)


def test_asking_for_a_population_that_is_not_there_lists_the_options():
    with pytest.raises(PopulationError, match="Available:"):
        load_target_population("mars_colony")
