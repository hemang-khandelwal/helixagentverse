import json
from pathlib import Path

from genegap.clients.gnomad import merge_populations

EXPECTED_GROUPS = {"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"}


def test_merge_populations_returns_exact_10_gnomad_v4_ancestry_groups():
    resp_path = Path(__file__).resolve().parents[1] / "smoke" / "responses" / "gnomad_variant_10-94781859-G-A.json"
    payload = json.loads(resp_path.read_text())
    merged = merge_populations(payload["data"]["variant"])
    assert set(merged.keys()) == EXPECTED_GROUPS
