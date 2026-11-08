"""The chart CLI must use the existing schema and preserve source context."""
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from matplotlib.figure import Figure

from charts import render as render_module
from charts.render import ChartInput, load_input, offline_input
from genegap.schemas import GapResult, GroupGap


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "charts" / "render.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR", "/tmp/genegap-chart-tests-matplotlib")},
    )


def test_bare_schema_preserves_numbers_without_inventing_a_carrier_threshold(tmp_path):
    result = GapResult(
        variant_id="TEST-VARIANT", gene="TEST", mapping_version="TEST-MAPPING",
        groups=[GroupGap(group="TEST-GROUP", n_trial=12.5, carrier_ratio=0.0, required_n=65)],
    )
    path = tmp_path / "gap.json"
    path.write_text(result.model_dump_json())
    loaded = load_input(path)
    assert loaded.result == result
    assert loaded.target_carriers is None
    assert loaded.result.groups[0].n_trial == 12.5
    assert loaded.result.groups[0].carrier_ratio == 0.0


def test_optional_context_survives_round_trip(tmp_path):
    payload = ChartInput(
        result=GapResult(variant_id="TEST"), target_carriers=30,
        target_label="Synthetic test target", notes=["Synthetic software-test assumptions."],
    )
    path = tmp_path / "input.json"
    path.write_text(payload.model_dump_json())
    assert load_input(path) == payload


def test_offline_adapter_keeps_real_zeros_missing_data_and_assumptions():
    payload = offline_input()
    assert payload.result.mapping_version == "A"
    rows = {row.group: row for row in payload.result.groups}
    assert rows["amr"].carrier_ratio == 0
    assert rows["ami"].insufficient_data
    assert rows["ami"].required_n is None
    assert rows["asj"].carrier_ratio is None
    assert rows["asj"].required_n is not None
    assert payload.target_carriers == 30
    assert any("ASSUMPTION" in note for note in payload.notes)
    assert payload.facts
    assert payload.collection_notes


def test_cli_exports_two_valid_svg_and_png_charts_and_exact_input(tmp_path):
    output = tmp_path / "charts"
    proc = run_cli("--offline", "--output-dir", output)
    assert proc.returncode == 0, proc.stderr
    for name in ("representation_ratio", "required_enrolment"):
        assert (output / f"{name}.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert ET.parse(output / f"{name}.svg").getroot().tag.endswith("svg")
    saved = load_input(output / "chart_input.json")
    assert saved.result.variant_id == "10-94781859-G-A"
    assert saved.result.mapping_version == "A"
    assert saved.target_carriers == 30
    assert "Task 6.2" in proc.stdout


def test_imported_export_does_not_override_mapping_or_target(tmp_path):
    path = tmp_path / "gap.json"
    path.write_text(GapResult(
        variant_id="TEST", mapping_version="IMPORTED",
        groups=[GroupGap(group="TEST-GROUP", n_trial=0, carrier_ratio=0, required_n=65)],
    ).model_dump_json())
    output = tmp_path / "charts"
    proc = run_cli("--input", path, "--output-dir", output)
    assert proc.returncode == 0, proc.stderr
    saved = load_input(output / "chart_input.json")
    assert saved.target_carriers is None
    assert saved.result.mapping_version == "IMPORTED"
    assert saved.result.groups[0].required_n == 65
    blocked = run_cli("--input", path, "--target-carriers", "99", "--output-dir", tmp_path / "blocked")
    assert blocked.returncode != 0
    assert not (tmp_path / "blocked").exists()


@pytest.mark.parametrize("text", ["[]", '{"result": {"groups": []}}', "invalid JSON"])
def test_invalid_import_does_not_create_outputs(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    output = tmp_path / "charts"
    proc = run_cli("--input", path, "--output-dir", output)
    assert proc.returncode == 1
    assert not output.exists()


def _synthetic_chart_input(variant, ratio, required):
    return GapResult(variant_id=variant, groups=[GroupGap(
        group="TEST", n_trial=10, carrier_ratio=ratio, required_n=required,
    )]).model_dump_json()


def _bundle_bytes(output):
    return {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}


def test_failed_second_render_preserves_the_entire_previous_bundle(tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    output = tmp_path / "charts"
    source.write_text(_synthetic_chart_input("VARIANT_A", 0.2, 10))
    assert render_module.main(["--input", str(source), "--output-dir", str(output)]) == 0
    (output / "unrelated.txt").write_text("User-owned file.")
    previous = _bundle_bytes(output)
    source.write_text(_synthetic_chart_input("VARIANT_B", 0.5, 20))
    original_savefig = Figure.savefig

    def fail_later_chart(self, filename, *args, **kwargs):
        if Path(filename).name == "required_enrolment.png":
            raise OSError("Synthetic disk/write failure.")
        return original_savefig(self, filename, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", fail_later_chart)
    assert render_module.main(["--input", str(source), "--output-dir", str(output)]) == 1
    assert _bundle_bytes(output) == previous
    assert not list(tmp_path.glob(".genegap-charts-*"))


def test_mid_publication_failure_rolls_back_all_replaced_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    output = tmp_path / "charts"
    source.write_text(_synthetic_chart_input("VARIANT_A", 0.2, 10))
    assert render_module.main(["--input", str(source), "--output-dir", str(output)]) == 0
    previous = _bundle_bytes(output)
    source.write_text(_synthetic_chart_input("VARIANT_B", 0.5, 20))
    original_replace = render_module.os.replace
    failed = []

    def fail_one_publication(staged, destination):
        if Path(staged).parent.name == "new" and Path(destination).name == "required_enrolment.svg" and not failed:
            failed.append(True)
            raise OSError("Synthetic publication failure.")
        return original_replace(staged, destination)

    monkeypatch.setattr(render_module.os, "replace", fail_one_publication)
    assert render_module.main(["--input", str(source), "--output-dir", str(output)]) == 1
    assert failed == [True]
    assert _bundle_bytes(output) == previous
    assert not list(tmp_path.glob(".genegap-charts-*"))


def test_failed_publication_preserves_input_inside_the_output_bundle(tmp_path, monkeypatch):
    output = tmp_path / "charts"
    output.mkdir()
    source = output / "chart_input.json"
    source.write_text(_synthetic_chart_input("VARIANT_A", 0.2, 10))
    original = source.read_bytes()
    original_replace = render_module.os.replace
    failed = []

    def interrupt_after_input_replacement(staged, destination):
        result = original_replace(staged, destination)
        if Path(staged).parent.name == "new" and Path(destination) == source and not failed:
            failed.append(True)
            raise OSError("Synthetic failure immediately after metadata replacement.")
        return result

    monkeypatch.setattr(render_module.os, "replace", interrupt_after_input_replacement)
    assert render_module.main(["--input", str(source), "--output-dir", str(output)]) == 1
    assert failed == [True]
    assert source.read_bytes() == original
    assert list(output.iterdir()) == [source]
    assert not list(tmp_path.glob(".genegap-charts-*"))
