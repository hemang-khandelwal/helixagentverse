"""A3: Temporal holdout and cutoff integrity tests."""

from __future__ import annotations

import inspect
import json
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from contracts import (
    Candidate,
    Citation,
    EvidenceEdge,
    EvidenceGraph,
    GraphNode,
    RunRequest,
)
from tools import HelixTools
from verifier import adjudicate, verify


def edges_postdating_cutoff(
    graph: EvidenceGraph,
    max_date: date,
    edge_ids: list[str] | None = None,
) -> list[str]:
    """Return edge IDs that lack a valid citation or cite a source after max_date."""
    target_ids = edge_ids if edge_ids is not None else list(graph.edges.keys())
    violating: list[str] = []
    for edge_id in target_ids:
        edge = graph.edges.get(edge_id)
        if edge is None or not edge.citation_ids:
            violating.append(edge_id)
            continue
        for cid in edge.citation_ids:
            citation = graph.citations.get(cid)
            if citation is None or not citation.within(max_date):
                violating.append(edge_id)
                break
    return violating


def _build_synthetic_graph() -> EvidenceGraph:
    """Construct a multi-hop synthetic EvidenceGraph with pre- and post-cutoff edges."""
    nodes = {
        "drug:1": GraphNode("drug:1", "Drug", "SyntheticDrug", analog_depth=0),
        "route:cyp2d6": GraphNode("route:cyp2d6", "Route", "CYP2D6 clearance", analog_depth=0),
        "analog:1": GraphNode("analog:1", "Analog", "SyntheticAnalog", analog_depth=1),
        "variant:1": GraphNode("variant:1", "Variant", "CYP2D6*4", analog_depth=1),
        "pheno:1": GraphNode("pheno:1", "Phenotype", "severe toxicity", analog_depth=1),
    }
    citations = {
        "pmid:early": Citation(
            source_id="pmid:early",
            title="Early metabolism study",
            url="https://europepmc.org/article/MED/100001",
            first_available=date(2005, 6, 15),
            supporting_text="SyntheticDrug is primarily cleared by CYP2D6.",
        ),
        "pmid:boundary": Citation(
            source_id="pmid:boundary",
            title="Advance-online study published on exact cutoff date",
            url="https://europepmc.org/article/MED/100002",
            first_available=date(2008, 12, 31),
            supporting_text="CYP2D6*4 is associated with severe toxicity in SyntheticAnalog.",
        ),
        "pmid:late": Citation(
            source_id="pmid:late",
            title="Post-cutoff clinical trial",
            url="https://europepmc.org/article/MED/100003",
            first_available=date(2009, 1, 1),
            supporting_text="CYP2D6*4 causes severe toxicity with SyntheticDrug.",
        ),
    }
    edges = {
        "e_drug_route": EvidenceEdge(
            edge_id="e_drug_route",
            from_node_id="drug:1",
            to_node_id="route:cyp2d6",
            relation="is cleared by",
            citation_ids=["pmid:early"],
        ),
        "e_drug_analog": EvidenceEdge(
            edge_id="e_drug_analog",
            from_node_id="drug:1",
            to_node_id="analog:1",
            relation="is similar to",
            citation_ids=["pmid:early"],
        ),
        "e_analog_variant_in_window": EvidenceEdge(
            edge_id="e_analog_variant_in_window",
            from_node_id="analog:1",
            to_node_id="variant:1",
            relation="carries liability",
            citation_ids=["pmid:boundary"],
        ),
        "e_variant_pheno_in_window": EvidenceEdge(
            edge_id="e_variant_pheno_in_window",
            from_node_id="variant:1",
            to_node_id="pheno:1",
            relation="is associated with",
            citation_ids=["pmid:boundary"],
        ),
        "e_variant_pheno_post_cutoff": EvidenceEdge(
            edge_id="e_variant_pheno_post_cutoff",
            from_node_id="variant:1",
            to_node_id="pheno:1",
            relation="is associated with",
            citation_ids=["pmid:late"],
        ),
        "e_uncited": EvidenceEdge(
            edge_id="e_uncited",
            from_node_id="route:cyp2d6",
            to_node_id="pheno:1",
            relation="is associated with",
            citation_ids=[],
        ),
    }
    return EvidenceGraph(
        drug_node_id="drug:1",
        nodes=nodes,
        edges=edges,
        citations=citations,
    )


class TemporalIntegrityTests(unittest.TestCase):
    def test_citation_first_available_cutoff_is_inclusive(self) -> None:
        cutoff = date(2008, 12, 31)
        before = Citation(
            "pmid:1", "Before cutoff", "https://example.org/1",
            date(2008, 12, 30), "Supporting text",
        )
        exact = Citation(
            "pmid:2", "On exact cutoff (advance-online)", "https://example.org/2",
            date(2008, 12, 31), "Supporting text",
        )
        one_day_late = Citation(
            "pmid:3", "One day after cutoff", "https://example.org/3",
            date(2009, 1, 1), "Supporting text",
        )

        self.assertTrue(before.within(cutoff))
        self.assertTrue(exact.within(cutoff))
        self.assertFalse(one_day_late.within(cutoff))

    def test_provenance_chain_passes_when_all_edges_within_max_date(self) -> None:
        graph = _build_synthetic_graph()
        cutoff = date(2008, 12, 31)
        candidate = Candidate(
            candidate_id="cand:in_window",
            gene="CYP2D6",
            allele="CYP2D6*4",
            phenotype="severe toxicity",
            tier="B",
            provenance_edge_ids=[
                "e_drug_route",
                "e_drug_analog",
                "e_analog_variant_in_window",
                "e_variant_pheno_in_window",
            ],
            route="CYP2D6 clearance",
            analog_drug="SyntheticAnalog",
            shared_feature="CYP2D6 metabolic clearance",
            tanimoto=0.78,
            shared_target=True,
            shared_route=True,
        )

        violations = edges_postdating_cutoff(
            graph, cutoff, candidate.provenance_edge_ids
        )
        self.assertEqual(violations, [])

    def test_provenance_chain_fails_when_any_edge_postdates_max_date(self) -> None:
        graph = _build_synthetic_graph()
        cutoff = date(2008, 12, 31)
        candidate = Candidate(
            candidate_id="cand:leaked_future_edge",
            gene="CYP2D6",
            allele="CYP2D6*4",
            phenotype="severe toxicity",
            tier="A",
            provenance_edge_ids=[
                "e_drug_route",
                "e_variant_pheno_post_cutoff",
            ],
            route="CYP2D6 clearance",
        )

        violations = edges_postdating_cutoff(
            graph, cutoff, candidate.provenance_edge_ids
        )
        self.assertEqual(violations, ["e_variant_pheno_post_cutoff"])

    def test_provenance_chain_fails_when_edge_lacks_or_has_unknown_citation(self) -> None:
        graph = _build_synthetic_graph()
        cutoff = date(2008, 12, 31)
        graph.edges["e_dangling_citation"] = EvidenceEdge(
            edge_id="e_dangling_citation",
            from_node_id="drug:1",
            to_node_id="route:cyp2d6",
            relation="is cleared by",
            citation_ids=["pmid:nonexistent"],
        )

        self.assertEqual(
            edges_postdating_cutoff(graph, cutoff, ["e_uncited"]),
            ["e_uncited"],
        )
        self.assertEqual(
            edges_postdating_cutoff(graph, cutoff, ["e_dangling_citation"]),
            ["e_dangling_citation"],
        )
        self.assertEqual(
            edges_postdating_cutoff(graph, cutoff, ["e_missing_edge"]),
            ["e_missing_edge"],
        )

    def test_toolset_binds_max_date_at_init_and_exposes_no_per_call_date_override(self) -> None:
        cutoff = date(2012, 5, 1)
        tools = HelixTools(max_date=cutoff)
        self.assertEqual(tools.max_date, cutoff)

        retrieval_methods = (
            "fetch_compound",
            "similar_compounds",
            "fetch_pgx",
            "fetch_frequency",
            "fetch_literature",
        )
        forbidden_param_names = {"max_date", "cutoff", "cutoff_date", "before_date", "as_of"}

        for method_name in retrieval_methods:
            with self.subTest(method=method_name):
                method = getattr(HelixTools, method_name)
                sig = inspect.signature(method)
                param_names = set(sig.parameters.keys()) - {"self"}
                self.assertFalse(
                    param_names & forbidden_param_names,
                    f"{method_name} must not expose a per-call date parameter: {param_names}",
                )

    def test_historical_evaluation_arms_require_non_null_max_date(self) -> None:
        for arm in ("B_full", "A_plus"):
            with self.subTest(arm=arm):
                with self.assertRaises(ValueError):
                    RunRequest(drug_name="abacavir", max_date=None, arm=arm)
                req = RunRequest(drug_name="abacavir", max_date=date(2001, 12, 31), arm=arm)
                self.assertEqual(req.max_date, date(2001, 12, 31))

    def test_a2_fixture_graph_and_candidates_respect_temporal_integrity_if_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        graph_fixture = repo_root / "fixtures" / "graph_example.json"
        candidates_fixture = repo_root / "fixtures" / "candidate_rows.json"

        if not graph_fixture.exists() and not candidates_fixture.exists():
            self.skipTest("A2 fixtures not created yet")

        if graph_fixture.exists():
            raw_graph: dict[str, Any] = json.loads(graph_fixture.read_text())
            max_date_str = raw_graph.get("max_date")
            max_date_val = date.fromisoformat(max_date_str) if max_date_str else None

            citations = raw_graph.get("citations", {})
            citation_dates: dict[str, date] = {}
            if isinstance(citations, dict):
                items = citations.items()
            else:
                items = [(c["source_id"], c) for c in citations]

            for cid, cdata in items:
                fa = date.fromisoformat(cdata["first_available"])
                citation_dates[cid] = fa
                if max_date_val is not None:
                    self.assertLessEqual(
                        fa,
                        max_date_val,
                        f"Citation {cid} in graph_example.json post-dates max_date {max_date_val}",
                    )

            edges = raw_graph.get("edges", {})
            edge_items = edges.values() if isinstance(edges, dict) else edges
            for edge in edge_items:
                for cid in edge.get("citation_ids", []):
                    self.assertIn(cid, citation_dates, f"Edge references unknown citation {cid}")
                    if max_date_val is not None:
                        self.assertLessEqual(
                            citation_dates[cid],
                            max_date_val,
                            f"Edge {edge.get('edge_id')} cites post-cutoff source {cid}",
                        )

        if candidates_fixture.exists():
            raw_candidates = json.loads(candidates_fixture.read_text())
            rows = (
                raw_candidates
                if isinstance(raw_candidates, list)
                else raw_candidates.get("candidates", [])
            )
            for row in rows:
                for prov in row.get("provenance", []):
                    if "first_available" in prov and prov["first_available"] is not None:
                        # Must be valid ISO YYYY-MM-DD
                        date.fromisoformat(prov["first_available"])

    def test_verifier_c1_rejects_post_cutoff_edges_when_implemented(self) -> None:
        graph = _build_synthetic_graph()
        cutoff = date(2008, 12, 31)

        in_window_candidate = Candidate(
            candidate_id="cand:valid",
            gene="CYP2D6",
            allele="CYP2D6*4",
            phenotype="severe toxicity",
            tier="B",
            provenance_edge_ids=[
                "e_drug_route",
                "e_drug_analog",
                "e_analog_variant_in_window",
                "e_variant_pheno_in_window",
            ],
            route="CYP2D6 clearance",
            analog_drug="SyntheticAnalog",
            shared_feature="CYP2D6 metabolic clearance",
            tanimoto=0.78,
            shared_target=True,
            shared_route=True,
        )
        out_of_window_candidate = Candidate(
            candidate_id="cand:post_cutoff",
            gene="CYP2D6",
            allele="CYP2D6*4",
            phenotype="severe toxicity",
            tier="A",
            provenance_edge_ids=[
                "e_drug_route",
                "e_variant_pheno_post_cutoff",
            ],
            route="CYP2D6 clearance",
        )

        try:
            in_report = verify(
                in_window_candidate,
                graph,
                max_date=cutoff,
                frequencies=[],
                associations=[],
                severity_table=[],
            )
            out_report = verify(
                out_of_window_candidate,
                graph,
                max_date=cutoff,
                frequencies=[],
                associations=[],
                severity_table=[],
            )
        except NotImplementedError:
            self.skipTest("verifier.verify not implemented yet (B2)")

        self.assertIn("C1", in_report.ids_with_status("PASS"))
        self.assertIn("C1", out_report.ids_with_status("FAIL"))

        try:
            verdict = adjudicate(out_report)
        except NotImplementedError:
            return
        self.assertEqual(verdict.outcome, "REJECTED")
        self.assertIn("C1", verdict.reason)


if __name__ == "__main__":
    unittest.main()
