"""Programmer 3: task 6.2 in the longer programming guide — export two charts.

Everything added for this task lives under charts/ to avoid shared-file edits.
Inputs are existing GapResult records or the recorded offline demonstration.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field

from genegap.schemas import Fact, GapResult


class ChartInput(BaseModel):
    """Optional chart context; the shared scientific schema is unchanged."""

    model_config = ConfigDict(extra="forbid")
    result: GapResult
    notes: list[str] = Field(default_factory=list)
    target_label: str = ""
    target_carriers: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    source: str = "Imported GapResult; source context supplied by the caller."
    collection_notes: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


def load_input(path: Path) -> ChartInput:
    """Accept a bare shared GapResult or an envelope with plot context."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Chart input must be a GapResult JSON object or an object containing result.")
    if "result" in data:
        return ChartInput.model_validate(data)
    return ChartInput(result=GapResult.model_validate(data))


def offline_input(mapping_version: str = "A", target_carriers: Optional[float] = None) -> ChartInput:
    """Call existing core functions; keep the demonstration in an isolated store."""
    from genegap import fixtures
    from genegap.gap import DEFAULT_TARGET_CARRIERS, compute_gap
    from genegap.mapping import load_all_mappings, map_counts
    from genegap.population import load_target_population
    from genegap.store import Store
    from genegap.trials import collect_trials, pooled_race_counts
    from genegap.variants import load_rules, variants_for_gene

    store = Store(":memory:")
    try:
        gnomad, clinvar, ctgov, replayed = fixtures.offline_clients(store)
        table = variants_for_gene("CYP2C19", gnomad, clinvar)
        variant = next((item for item in table.variants if item.rsid == fixtures.DEMO_RSID), None)
        if variant is None:
            raise ValueError("The recorded CYP2C19 variant was unavailable; no chart data was invented.")
        trials, trial_notes = collect_trials(ctgov, intervention=fixtures.DEMO_DRUG, max_trials=5)
        counts, used_trials, pool_notes = pooled_race_counts(trials)
        if not counts:
            raise ValueError("Recorded trials supplied no demographic counts.")
        target = load_target_population("us_acs_antiplatelet")
        mappings = load_all_mappings()
        if mapping_version not in mappings:
            raise ValueError(f"Unknown ancestry mapping: {mapping_version}")
        threshold = DEFAULT_TARGET_CARRIERS if target_carriers is None else target_carriers
        rule = load_rules()["CYP2C19"]
        run = compute_gap(
            variant, map_counts(counts, mappings[mapping_version]), target,
            inheritance=table.inheritance, min_an=rule.min_an, target_carriers=threshold,
        )
        fact_ids = set(run.result.fact_ids)
        for trial in trials:
            if trial.nct_id in used_trials:
                fact_ids.update(trial.fact_ids)
                for baseline in trial.baseline:
                    fact_ids.update(baseline.fact_ids)
        facts = [fact for fact_id in sorted(fact_ids) if (fact := store.get_fact(fact_id)) is not None]
        return ChartInput(
            result=run.result,
            target_label=run.target_label,
            target_carriers=threshold,
            source="Offline recorded clopidogrel / CYP2C19 demonstration; expert review pending.",
            notes=[
                "Offline recorded data; only a subset of variants and trial records is available.",
                "Trial demographic counts remain provisional pending medical review.",
                *run.notes,
            ],
            collection_notes=[*replayed, *table.notes, *trial_notes, *pool_notes],
            facts=facts,
        )
    finally:
        store.db.close()


def _export_bundle(figures: dict, payload: ChartInput, output_dir: Path) -> None:
    """Publish a complete chart bundle, restoring the old bundle on failure."""
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=".genegap-charts-", dir=output_dir.parent))
    preserve_backup = False
    output_existed = output_dir.exists()
    try:
        staged = workspace / "new"
        backup = workspace / "previous"
        staged.mkdir()
        backup.mkdir()
        names = []
        # Finish every expensive render and the metadata write before touching
        # any existing artifacts, including a handoff used as this run's input.
        for name, figure in figures.items():
            for suffix in ("png", "svg"):
                filename = f"{name}.{suffix}"
                figure.savefig(staged / filename, dpi=160, bbox_inches="tight")
                names.append(filename)
        names.append("chart_input.json")
        (staged / names[-1]).write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")

        output_dir.mkdir(parents=True, exist_ok=True)
        previous = set()
        for name in names:
            destination = output_dir / name
            if destination.exists() or destination.is_symlink():
                if not destination.is_file() and not destination.is_symlink():
                    raise ValueError(f"Chart destination must be a file: {destination}")
                shutil.copy2(destination, backup / name, follow_symlinks=False)
                previous.add(name)

        attempted = []
        try:
            for name in names:
                # Record before replacing so interruption immediately after a
                # successful rename still restores this artifact too.
                attempted.append(name)
                os.replace(staged / name, output_dir / name)
        except BaseException as exc:
            preserve_backup = True
            failures = []
            for name in reversed(attempted):
                destination = output_dir / name
                try:
                    if name in previous:
                        os.replace(backup / name, destination)
                    elif destination.exists() or destination.is_symlink():
                        destination.unlink()
                except OSError as restore_error:
                    failures.append(str(restore_error))
            if failures:
                raise OSError(f"Chart publication failed and rollback could not finish. Previous artifacts are retained in {backup}.") from exc
            preserve_backup = False
            if not output_existed:
                try:
                    output_dir.rmdir()
                except OSError:
                    pass  # Keep unrelated files another writer may have added.
            raise
    finally:
        if not preserve_backup:
            shutil.rmtree(workspace)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Existing GapResult JSON or chart-context envelope")
    source.add_argument("--offline", action="store_true", help="Render the recorded clopidogrel demonstration")
    parser.add_argument("--mapping", choices=("A", "B", "C"), help="Offline demo mapping; defaults to A")
    parser.add_argument("--target-carriers", type=float, help="Offline demo expected-carrier target; defaults to the core setting")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "charts" / "output")
    args = parser.parse_args(argv)
    if args.input and (args.mapping is not None or args.target_carriers is not None):
        parser.error("--mapping and --target-carriers apply to --offline; imported charts use their supplied metadata")
    if args.target_carriers is not None and (not math.isfinite(args.target_carriers) or args.target_carriers < 0):
        parser.error("--target-carriers must be finite and nonnegative")

    try:
        # Headless exports should not need to write into a user's home folder.
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "genegap-chart-matplotlib"))
        from charts.plots import plot_representation, plot_required_enrolment

        payload = load_input(args.input) if args.input else offline_input(args.mapping or "A", args.target_carriers)
        figures = {
            "representation_ratio": plot_representation(
                payload.result, notes=payload.notes, target_label=payload.target_label,
            ),
            "required_enrolment": plot_required_enrolment(
                payload.result, notes=payload.notes, target_label=payload.target_label,
                target_carriers=payload.target_carriers,
            ),
        }
        try:
            _export_bundle(figures, payload, args.output_dir)
        finally:
            for figure in figures.values():
                figure.clear()
        print(f"Task 6.2: saved representation and required-enrolment charts to {args.output_dir}")
        print(f"Variant: {payload.result.variant_id}; mapping: {payload.result.mapping_version}.")
        print("Research estimates; expert review pending. Input data and context are saved in chart_input.json.")
        return 0
    except ModuleNotFoundError as exc:
        print(f"Chart dependency missing: {exc}. Install charts/requirements.txt in the project environment.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Charts were not exported: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
