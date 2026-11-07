"""A1 interface checks, not scientific or end-to-end verification."""

# These automatic checks confirm that the shared setup follows our agreed rules. All
# medical-looking examples are invented for testing.



import importlib
import json
import unittest
from datetime import date
from pathlib import Path

from contracts import (
    ADJUDICATING_CHECKS, ALL_CHECKS, ANNOTATING_CHECKS,
    Candidate, CheckReport, CheckResult, Citation, FrequencyRecord,
    RunBudget, RunRequest, data_file_schemas, to_jsonable,
)


class ContractTests(unittest.TestCase):
    # A group of automatic checks for the shared setup. These are tests of
    # programming rules, not tests on patients.
    def test_all_owner_modules_import_without_optional_dependencies(self):
        # Test: all owner modules import without optional dependencies. The
        # assertions below say what should happen. A failed assertion points to a
        # rule we need to investigate.
        for name in ("tools", "graph", "verifier", "agent", "orchestrator", "eval", "app"):
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_published_file_manifest_matches_dataclasses(self):
        # Test: published file manifest matches dataclasses. The assertions below say
        # what should happen. A failed assertion points to a rule we need to
        # investigate.
        manifest = Path(__file__).resolve().parents[1] / "data" / "schemas.json"
        self.assertEqual(json.loads(manifest.read_text()), data_file_schemas())

    def test_null_allele_and_exact_date_survive_json_encoding(self):
        # Test: null allele and exact date survive json encoding. The assertions
        # below say what should happen. A failed assertion points to a rule we need
        # to investigate.
        candidate = Candidate("synthetic-1", "EXAMPLE_GENE", None, "example", "C", [])
        citation = Citation("synthetic-source", "Example only", "https://example.org", date(2020, 1, 2), "Synthetic text")
        encoded = json.loads(json.dumps(to_jsonable({"candidate": candidate, "citation": citation})))
        self.assertIsNone(encoded["candidate"]["allele"])
        self.assertEqual(encoded["citation"]["first_available"], "2020-01-02")

    def test_first_public_cutoff_is_inclusive(self):
        # Test: first public cutoff is inclusive. The assertions below say what
        # should happen. A failed assertion points to a rule we need to investigate.
        citation = Citation("synthetic-source", "Example", "https://example.org", date(2020, 1, 2), "Synthetic")
        self.assertFalse(citation.within(date(2020, 1, 1)))
        self.assertTrue(citation.within(date(2020, 1, 2)))

    def test_policy_matches_latest_task_split(self):
        # Test: policy matches latest task split. The assertions below say what
        # should happen. A failed assertion points to a rule we need to investigate.
        self.assertEqual(ADJUDICATING_CHECKS, {"C1", "C2", "C3", "C4"})
        self.assertFalse(ADJUDICATING_CHECKS & ANNOTATING_CHECKS)
        self.assertEqual(ALL_CHECKS, {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"})
        self.assertTrue(CheckResult("C3", "FAIL", "Unsupported allele").adjudicating)
        self.assertFalse(CheckResult("C5", "FAIL", "Weak analogy").adjudicating)

    def test_duplicate_or_unknown_check_cannot_enter_report(self):
        # Test: duplicate or unknown check cannot enter report. The assertions below
        # say what should happen. A failed assertion points to a rule we need to
        # investigate.
        with self.assertRaises(ValueError):
            CheckReport([CheckResult("C1", "PASS", "Example"), CheckResult("C1", "FAIL", "Example")])
        with self.assertRaises(ValueError):
            CheckResult("G1", "PASS", "Obsolete identifier")
        with self.assertRaises(ValueError):
            CheckResult("C1", "MAYBE", "Invalid status")

    def test_historical_retrieval_arms_require_cutoff(self):
        # Test: historical retrieval arms require cutoff. The assertions below say
        # what should happen. A failed assertion points to a rule we need to
        # investigate.
        for arm in ("A_plus", "B_full"):
            with self.subTest(arm=arm), self.assertRaises(ValueError):
                RunRequest("example-drug", None, arm)
        self.assertIsNone(RunRequest("example-drug", None, "C_live").max_date)
        self.assertIsNone(RunRequest("example-drug", None, "A_baseline").max_date)

    def test_frequency_bounds_and_measurement_type(self):
        # Test: frequency bounds and measurement type. The assertions below say what
        # should happen. A failed assertion points to a rule we need to investigate.
        def make(low, point, high, kind="carrier"):
            # Make an invented frequency record for this test. This small helper
            # avoids repeating the same example details.
            return FrequencyRecord("example-variant", "EXAMPLE_GENE", "example-population", kind, low, point, high, "synthetic", "https://example.org", "test-only", date(2020, 1, 1))

        self.assertEqual(make(0.01, 0.02, 0.03).frequency_kind, "carrier")
        self.assertEqual(make(0.01, 0.02, 0.03, "allele").frequency_kind, "allele")
        for values in ((-0.1, 0.2, 0.3), (0.3, 0.2, 0.4), (0.1, 0.2, 1.1), (0.1, float("nan"), 0.3)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                make(*values)
        with self.assertRaises(ValueError):
            make(0.1, 0.2, 0.3, "percentage")

    def test_invalid_budgets_fail_early(self):
        # Test: invalid budgets fail early. The assertions below say what should
        # happen. A failed assertion points to a rule we need to investigate.
        for kwargs in ({"max_expansions": 0}, {"max_analog_hops": -1}, {"max_seconds": 0}, {"max_seconds": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RunBudget(**kwargs)

    def test_stubs_do_not_pretend_to_find_or_approve_evidence(self):
        # Test: stubs do not pretend to find or approve evidence. The assertions
        # below say what should happen. A failed assertion points to a rule we need
        # to investigate.
        from tools import HelixTools
        from verifier import adjudicate

        with self.assertRaises(NotImplementedError):
            HelixTools(max_date=date(2020, 1, 1)).fetch_literature("example")
        with self.assertRaises(NotImplementedError):
            adjudicate(CheckReport())


if __name__ == "__main__":
    unittest.main()
