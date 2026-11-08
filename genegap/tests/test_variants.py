import pytest

from genegap.clients.base import ApiError
from genegap.schemas import AncestryFrequency, Fact, Variant
from genegap.variants import (GeneRule, RuleError, VariantDetail, apply_filters, format_table, load_rules,
                              load_variant_details, variants_for_gene)


def mk(variant_id, groups, rsid="rs1"):
    return Variant(variant_id=variant_id, rsid=rsid, dataset="gnomad_r4",
                   frequencies=[AncestryFrequency(group=g, ac=ac, an=an, fact_id="F-x") for g, (ac, an) in groups.items()])


RULE = GeneRule("GENEX", True, "", "dominant", ["rs1"], 0.005, 5000, "", "src", "someone")


class FakeGnomad:
    def __init__(self, by_rsid): self.by_rsid = by_rsid
    def variants_for_rsid(self, rsid, gene=None):
        r = self.by_rsid[rsid]
        if isinstance(r, Exception):
            raise r
        return r


class FakeClinVar:
    def __init__(self, labels=None, error=None): self.labels, self.error = labels or [], error
    def lookup_rsid(self, rsid):
        if self.error:
            raise self.error
        return [Fact(field="classification", value={"title": "t", "classification": c}, source="ClinVar", record_id=f"u{i}")
                for i, c in enumerate(self.labels)]


# ---- rules file ----
def test_real_rules_file_loads_and_marks_hard_genes():
    rules = load_rules()
    assert rules["CYP2C19"].assessable and rules["CYP2C19"].verified_by
    for g in ("G6PD", "HLA-B", "HLA-A"):
        assert not rules[g].assessable and rules[g].reason_if_not


def test_rules_file_errors_are_loud(tmp_path):
    p = tmp_path / "r.csv"
    head = "gene,assessable,reason_if_not,inheritance,rsids,min_af,min_an,function_note,source,verified_by\n"
    p.write_text(head + "ABC,yes,,sideways,rs1,0.01,100,,,\n")
    with pytest.raises(RuleError, match="inheritance"):
        load_rules(p)
    p.write_text(head + "ABC,no,,dominant,,,,,,\n")
    with pytest.raises(RuleError, match="reason"):
        load_rules(p)
    p.write_text("gene,assessable\nABC,yes\n")
    with pytest.raises(RuleError, match="missing columns"):
        load_rules(p)


# ---- filter ----
def test_filter_drops_variant_rare_everywhere():
    assert not apply_filters(mk("v", {"afr": (1, 10000), "nfe": (2, 50000)}), RULE).keep


def test_filter_keeps_variant_common_in_one_group():
    out = apply_filters(mk("v", {"afr": (3000, 10000), "nfe": (2, 50000)}), RULE)
    assert out.keep and "afr" in out.reason


def test_filter_flags_low_data_group_but_keeps_variant():
    out = apply_filters(mk("v", {"afr": (3000, 10000), "ami": (1, 200)}), RULE)
    assert out.keep and out.low_data_groups == ["ami"]


def test_filter_drops_when_every_group_has_too_little_data():
    out = apply_filters(mk("v", {"afr": (50, 100), "nfe": (10, 100)}), RULE)
    assert not out.keep and "enough data" in out.reason


def test_low_data_group_cannot_trigger_the_frequency_cutoff():
    # 50% in a tiny group must not rescue a variant that is rare in every well-sampled group
    assert not apply_filters(mk("v", {"afr": (1, 10000), "ami": (50, 100)}), RULE).keep


# ---- whole function ----
def rules_with(**over):
    return {"GENEX": GeneRule(**{**RULE.__dict__, **over})}


def test_unknown_gene_and_unassessable_gene_return_a_message_not_numbers():
    t = variants_for_gene("NOPE", FakeGnomad({}), FakeClinVar(), rules_with())
    assert not t.assessable and not t.variants and "No rule" in t.notes[0]
    t = variants_for_gene("GENEX", FakeGnomad({}), FakeClinVar(), rules_with(assessable=False, reason_if_not="HLA"))
    assert not t.assessable and "Cannot assess" in t.notes[0]


def test_happy_path_adds_clinvar_label_and_facts():
    v = mk("1-1-A-G", {"afr": (3000, 10000)})
    t = variants_for_gene("genex", FakeGnomad({"rs1": [v]}), FakeClinVar(["Pathogenic", "Pathogenic", "Benign"]), rules_with())
    assert t.assessable and len(t.variants) == 1
    assert t.variants[0].classification == "Benign; Pathogenic"
    assert len(t.variants[0].fact_ids) == 3
    assert "GENEX" in format_table(t)


def test_unverified_rule_is_warned_about():
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": [mk("v", {"afr": (3000, 10000)})]}), FakeClinVar(), rules_with(verified_by=""))
    assert any("not been verified" in n for n in t.notes)


def test_failures_become_notes_not_crashes():
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": ApiError("down")}), FakeClinVar(), rules_with())
    assert not t.variants and any("lookup failed" in n for n in t.notes)
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": []}), FakeClinVar(), rules_with())
    assert any("not found" in n for n in t.notes)
    v = mk("v", {"afr": (3000, 10000)})
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": [v]}), FakeClinVar(error=ApiError("x")), rules_with())
    assert len(t.variants) == 1 and t.variants[0].classification is None


def test_multi_allele_rsid_is_noted():
    a, b = mk("1-1-A-G", {"afr": (3000, 10000)}), mk("1-1-A-T", {"afr": (1, 10000)})
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": [a, b]}), FakeClinVar(), rules_with())
    assert [v.variant_id for v in t.variants] == ["1-1-A-G"] and any("2 alleles" in n for n in t.notes)


# ---- the medical student's per-variant file ----
DHEAD = "gene,rsid,star_allele,function,copies_needed,source_link,notes\n"


def test_real_details_file_loads():
    d = load_variant_details()["CYP2C19"]
    assert len(d) == 10 and {"rs4244285", "rs4986893", "rs12248560"} <= set(d)
    assert d["rs12248560"].function == "increased function" and d["rs4244285"].star_allele == "*2"
    assert all(x.source_link.startswith("http") for x in d.values())


def test_details_file_errors_are_loud(tmp_path):
    p = tmp_path / "variant_details_x.csv"
    p.write_text(DHEAD + "G,12345,*1,no function,1,http://s,\n")
    with pytest.raises(RuleError, match="rsid"):
        load_variant_details([p])
    p.write_text(DHEAD + "G,rs1,*1,no function,1,,\n")
    with pytest.raises(RuleError, match="source_link"):
        load_variant_details([p])
    p.write_text(DHEAD + "G,rs1,*1,,1,http://s,\n")
    with pytest.raises(RuleError, match="function"):
        load_variant_details([p])
    p.write_text(DHEAD + "G,rs1,*1,no function,1,http://s,\nG,rs1,*2,no function,1,http://s,\n")
    with pytest.raises(RuleError, match="twice"):
        load_variant_details([p])
    p.write_text("gene,rsid\nG,rs1\n")
    with pytest.raises(RuleError, match="missing columns"):
        load_variant_details([p])


def test_details_labels_and_caveats_reach_the_table():
    det = {"GENEX": {"rs1": VariantDetail("GENEX", "rs1", "*2", "no function", "1 or 2", "http://s", "Haplotype: rsID alone may miss suballeles")}}
    v = mk("1-1-A-G", {"afr": (3000, 10000)})
    t = variants_for_gene("GENEX", FakeGnomad({"rs1": [v]}), FakeClinVar(), rules_with(rsids=[]), det)
    out = t.variants[0]
    assert (out.star_allele, out.function, out.copies_needed) == ("*2", "no function", "1 or 2")
    text = format_table(t)
    assert "*2" in text and "no function" in text and "Caveat: Haplotype" in text


def test_details_decide_which_rsids_are_looked_up():
    det = {"GENEX": {"rs2": VariantDetail("GENEX", "rs2", "*3", "no function", "", "http://s", "")}}
    g = FakeGnomad({"rs1": [mk("a", {"afr": (3000, 10000)})], "rs2": [mk("b", {"afr": (3000, 10000)}, rsid="rs2")]})
    t = variants_for_gene("GENEX", g, FakeClinVar(), rules_with(rsids=["rs1"]), det)
    assert [v.variant_id for v in t.variants] == ["b"]


def test_gene_with_no_rsids_anywhere_is_not_silently_empty():
    t = variants_for_gene("GENEX", FakeGnomad({}), FakeClinVar(), rules_with(rsids=[]), {})
    assert not t.assessable and "No rsIDs" in t.notes[0]


def test_real_cyp2c19_rules_pick_up_all_ten_rsids():
    seen = []

    class Spy(FakeGnomad):
        def variants_for_rsid(self, rsid, gene=None):
            seen.append(rsid)
            return []
    variants_for_gene("CYP2C19", Spy({}), FakeClinVar())
    assert len(seen) == 10 and seen[0] == "rs4244285"
