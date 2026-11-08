"""Task 6.1: the mini evaluation, two arms.

  ARM A  the model alone: no tools, no retrieval, one call per item, asked for the
         carrier frequency of a variant in one gnomAD ancestry group.
  ARM B  our pipeline, run offline against the committed fixtures in smoke/responses/.
         Arm B's numbers are ground truth for scoring Arm A.

Arm B's figures are correct by construction (they are replayed from recorded gnomAD
payloads through the tested stats functions). What Arm B measures here is that the
verification layers -- the critic and the language checker -- run clean on good input.

Network use is confined to :func:`run_arm_a`, which imports the SDK lazily. Everything
on the scoring path is pure and reads only the cached JSON, so the demo survives a dead
API. Every raw response is written to the cache as it returns, so a late failure can
never lose completed items.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import ROOT
from ..critic import criticise
from ..fixtures import offline_clients
from ..gap import compute_gap
from ..language import check_language
from ..mapping import load_all_mappings, map_counts
from ..population import load_target_population
from ..store import Store
from ..trials import collect_trials, pooled_race_counts
from ..variants import variants_for_gene

# ------------------------------------------------------------------ configuration

EVAL_OUT = ROOT / "eval_out"
BASELINE_CACHE = EVAL_OUT / "baseline_responses.json"
SUMMARY_PATH = EVAL_OUT / "mini_eval_summary.md"

# The only variant with committed gnomAD fixtures, across the 10 top-level gnomAD v4
# genetic ancestry groups. Ten items, well inside the cap of 25.
DEMO_GENE = "CYP2C19"
DEMO_RSID = "rs4244285"
DEMO_VARIANT_ID = "10-94781859-G-A"
DEMO_DRUG = "clopidogrel"
MAX_ITEMS = 25

ANCESTRY_GROUP_NAMES: Dict[str, str] = {
    "afr": "African/African-American",
    "ami": "Amish",
    "amr": "Admixed American",
    "asj": "Ashkenazi Jewish",
    "eas": "East Asian",
    "fin": "Finnish",
    "mid": "Middle Eastern",
    "nfe": "Non-Finnish European",
    "remaining": "Remaining",
    "sas": "South Asian",
}

# Classification bands, in percentage points of carrier frequency.
EXACT_PP = 2.0
CLOSE_PP = 5.0

CATEGORIES: Tuple[str, ...] = (
    "EXACT", "CLOSE", "WRONG", "REFUSED", "FABRICATED", "ERROR",
)

# Errors worth retrying: the project has been seeing 429s and http2 timeouts.
RETRYABLE = (
    "429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
    "DEADLINE", "timeout", "Timeout", "http2", "HTTP/2", "connection", "Connection",
    "ServerError", "overloaded",
)


# ------------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class EvalItem:
    """One question: this variant, in this ancestry group."""

    item_id: str
    gene: str
    rsid: str
    variant_id: str
    group: str
    group_name: str

    @property
    def question(self) -> str:
        return (
            f"In the gnomAD v4 {self.group_name} ({self.group}) genetic ancestry group, "
            f"what is the carrier frequency of the {self.gene} variant {self.rsid} "
            f"({self.variant_id})? Carrier frequency means the proportion of people "
            f"carrying at least one copy, under Hardy-Weinberg equilibrium."
        )


@dataclass(frozen=True)
class GroundTruth:
    """What our pipeline computed for one group, from the committed fixtures."""

    group: str
    carrier_freq: Optional[float]      # None when the group is flagged insufficient_data
    allele_freq: Optional[float]
    ac: Optional[int]
    an: Optional[int]
    insufficient_data: bool
    fact_ids: List[str] = field(default_factory=list)


@dataclass
class Summary:
    """The scored result. Serialisable, and reproducible from the cache alone."""

    n_items: int
    counts: Dict[str, int]
    mean_absolute_error_pp: Optional[float]
    n_scored_numeric: int
    arm_b_tiers: Dict[str, int]
    arm_b_language_pass: int
    arm_b_total: int
    arm_b_warned_checks: Dict[str, int]
    model: str
    generated_at: str
    headline: str
    coverage: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# -------------------------------------------------------------------------- items


def items() -> List[EvalItem]:
    """The evaluation set, defined in code. Ten groups for the one fixtured variant."""
    out = [
        EvalItem(
            item_id=f"{DEMO_RSID}:{group}",
            gene=DEMO_GENE,
            rsid=DEMO_RSID,
            variant_id=DEMO_VARIANT_ID,
            group=group,
            group_name=name,
        )
        for group, name in ANCESTRY_GROUP_NAMES.items()
    ]
    return out[:MAX_ITEMS]


# ----------------------------------------------------------- Arm B / ground truth


def _pipeline_result(store: Optional[Store] = None, mapping_version: str = "A"):
    """Run the deterministic chain offline and return (GapResult, variant, run)."""
    store = store or Store(":memory:")
    gnomad, clinvar, ctgov, _seeded = offline_clients(store)
    table = variants_for_gene(DEMO_GENE, gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention=DEMO_DRUG, max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == DEMO_RSID)
    mapping = load_all_mappings()[mapping_version]
    run = compute_gap(variant, map_counts(counts, mapping), target,
                      inheritance=table.inheritance)
    return run.result, variant, run, store


def ground_truth(store: Optional[Store] = None) -> Dict[str, GroundTruth]:
    """Arm B's numbers, keyed by ancestry group. These score Arm A."""
    result, _variant, _run, _store = _pipeline_result(store)
    truths: Dict[str, GroundTruth] = {}
    for g in result.groups:
        truths[g.group] = GroundTruth(
            group=g.group,
            carrier_freq=g.carrier_freq,
            allele_freq=g.allele_freq,
            ac=g.ac,
            an=g.an,
            insufficient_data=g.insufficient_data,
            fact_ids=list(result.fact_ids),
        )
    return truths


def _finding_sentence(group: str, truth: GroundTruth) -> str:
    """A report sentence stating the finding, for the language checker to judge."""
    name = ANCESTRY_GROUP_NAMES.get(group, group)
    if truth.insufficient_data or truth.carrier_freq is None:
        return (
            f"The {name} group is reported as insufficient data, because gnomAD records "
            f"only {truth.an} alleles for it, below the threshold this analysis requires "
            f"(gnomAD, {DEMO_RSID})."
        )
    # Quote to the precision the allele number supports (language rule L5).
    allowed = int(math.floor(math.log10(truth.an))) if truth.an else 4
    pct_dp = max(0, allowed - 2)
    return (
        f"In the {name} group, the estimated carrier frequency of {DEMO_RSID} is "
        f"{truth.carrier_freq * 100:.{pct_dp}f}% (gnomAD, {DEMO_RSID}); this is a "
        f"hypothesis for expert review."
    )


def run_arm_b(store: Optional[Store] = None) -> dict:
    """Run the pipeline offline and record what the verification layers said."""
    result, variant, run, used_store = _pipeline_result(store)
    report = criticise(result, facts=used_store, variant=variant)

    warned = {
        cid: 1 for cid, chk in report.checks.items() if chk.status in ("WARN", "FAIL")
    }
    truths = {g.group: t for g, t in
              ((g, GroundTruth(g.group, g.carrier_freq, g.allele_freq, g.ac, g.an,
                               g.insufficient_data, list(result.fact_ids)))
               for g in result.groups)}

    per_item = []
    for item in items():
        truth = truths.get(item.group)
        if truth is None:
            continue
        sentence = _finding_sentence(item.group, truth)
        lang = check_language(sentence, result, report.tier)
        per_item.append({
            "item_id": item.item_id,
            "group": item.group,
            "carrier_freq": truth.carrier_freq,
            "ac": truth.ac,
            "an": truth.an,
            "insufficient_data": truth.insufficient_data,
            "sentence": sentence,
            "language_status": lang.status,
            "language_passed": lang.passed,
            "language_failed_rules": lang.failed_rules,
            "cited_fact_ids": truth.fact_ids[:4],
        })

    return {
        "tier": report.tier,
        "critic_checks": {cid: chk.status for cid, chk in report.checks.items()},
        "critic_warned": sorted(warned),
        "critic_reasons": {
            cid: chk.reason for cid, chk in report.checks.items()
            if chk.status in ("WARN", "FAIL")
        },
        "mapping_version": result.mapping_version,
        "n_cited_facts": len(result.fact_ids),
        "items": per_item,
    }


# -------------------------------------------------------------------- Arm A (LLM)


PROMPT_SUFFIX = (
    "\n\nAnswer from your own knowledge. Do not use tools and do not browse. "
    "Reply with JSON only, in this exact shape:\n"
    '{"carrier_frequency": <number between 0 and 1, or null>, '
    '"refused": <true or false>, "note": "<one short sentence>"}\n'
    "Set refused to true and carrier_frequency to null if you cannot know this value."
)


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"model": "", "started_at": "", "responses": {}}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {"model": "", "started_at": "", "responses": {}}


def _save_cache(path: Path, cache: dict) -> None:
    """Write the whole cache after every item, so nothing completed is ever lost."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def parse_answer(text: str) -> Tuple[Optional[float], bool, str]:
    """Pull (carrier_frequency, refused, note) out of a model reply.

    Tolerant on purpose: the reply may be JSON, fenced JSON, or prose with a number.
    """
    if not text or not text.strip():
        return None, False, "empty response"

    blob = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", blob, re.DOTALL)
    if fence:
        blob = fence.group(1).strip()

    obj = None
    try:
        obj = json.loads(blob)
    except ValueError:
        brace = re.search(r"\{.*\}", blob, re.DOTALL)
        if brace:
            try:
                obj = json.loads(brace.group(0))
            except ValueError:
                obj = None

    if isinstance(obj, dict):
        raw = obj.get("carrier_frequency", obj.get("carrier_freq"))
        refused = bool(obj.get("refused", False))
        note = str(obj.get("note", ""))[:300]
        if raw is None:
            return None, refused, note
        try:
            return float(raw), refused, note
        except (TypeError, ValueError):
            return None, refused, note

    # Fall back to prose: an explicit refusal, or the first number we can find.
    lowered = blob.lower()
    if any(p in lowered for p in ("cannot know", "can't know", "i do not know",
                                  "i don't know", "unable to", "cannot provide",
                                  "not able to", "no reliable")):
        return None, True, blob[:300]
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", blob)
    if pct:
        return float(pct.group(1)) / 100.0, False, blob[:300]
    num = re.search(r"(?<![\w.])(0?\.\d+|[01](?:\.\d+)?)(?![\w.])", blob)
    if num:
        return float(num.group(1)), False, blob[:300]
    return None, False, blob[:300]


def _ask_once(client, model: str, prompt: str) -> str:
    """One no-tools call. Returns the raw response text."""
    from google.genai import types

    config_obj = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model, contents=prompt, config=config_obj
    )
    text = getattr(resp, "text", None)
    if text:
        return text
    if resp.candidates and resp.candidates[0].content:
        parts = resp.candidates[0].content.parts or []
        return "".join(p.text for p in parts if getattr(p, "text", None))
    return ""


def run_arm_a(
    eval_items: Optional[Sequence[EvalItem]] = None,
    cache_path: Path = BASELINE_CACHE,
    model: Optional[str] = None,
    max_attempts: int = 5,
    refresh: bool = False,
    sleep_fn=time.sleep,
) -> dict:
    """Ask the model, with no tools, one item at a time.

    Caches every raw response as it returns. Retries a failing call with exponential
    backoff up to ``max_attempts``; an item that never succeeds is recorded as ERROR
    and the run continues.
    """
    from google import genai   # imported lazily: the scoring path must stay network-free

    from .. import config

    eval_items = list(eval_items if eval_items is not None else items())
    model = model or config.GEMINI_MODEL
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is empty; Arm A needs it. Fill in .env.")

    cache = _load_cache(cache_path)
    cache.setdefault("responses", {})
    cache["model"] = model
    cache.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    cache["arm"] = "A: model alone, no tools, no retrieval"

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    for item in eval_items:
        if not refresh and item.item_id in cache["responses"]:
            continue

        prompt = item.question + PROMPT_SUFFIX
        record: Dict[str, Any] = {
            "item_id": item.item_id,
            "group": item.group,
            "variant_id": item.variant_id,
            "rsid": item.rsid,
            "prompt": prompt,
            "model": model,
            "attempts": 0,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }

        last_error = ""
        for attempt in range(1, max_attempts + 1):
            record["attempts"] = attempt
            try:
                text = _ask_once(client, model, prompt)
                value, refused, note = parse_answer(text)
                record.update({
                    "status": "OK",
                    "response_text": text,
                    "parsed_carrier_frequency": value,
                    "refused": refused,
                    "note": note,
                    "error": "",
                })
                break
            except Exception as e:                       # noqa: BLE001 - must never abort the run
                last_error = f"{type(e).__name__}: {e}"[:500]
                retryable = any(w in str(e) for w in RETRYABLE)
                if attempt < max_attempts and retryable:
                    sleep_fn(min(2 ** (attempt - 1), 30))
                    continue
                record.update({
                    "status": "ERROR",
                    "response_text": "",
                    "parsed_carrier_frequency": None,
                    "refused": False,
                    "note": "",
                    "error": last_error,
                })
                break

        cache["responses"][item.item_id] = record
        cache["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save_cache(cache_path, cache)   # persist after every item

    return cache


# ------------------------------------------------------------------- classifying


def classify(record: dict, truth: Optional[GroundTruth]) -> str:
    """Put one Arm A answer into a category against ground truth."""
    if record.get("status") != "OK":
        return "ERROR"

    value = record.get("parsed_carrier_frequency")

    if record.get("refused") and value is None:
        return "REFUSED"
    if value is None:
        return "REFUSED" if record.get("refused") else "ERROR"

    # A number outside [0, 1] is not a frequency at all.
    if not (0.0 <= float(value) <= 1.0):
        return "FABRICATED"

    # A confident number where our pipeline has no real value to give.
    if truth is None or truth.carrier_freq is None:
        return "FABRICATED"

    delta_pp = abs(float(value) - truth.carrier_freq) * 100.0
    if delta_pp <= EXACT_PP:
        return "EXACT"
    if delta_pp <= CLOSE_PP:
        return "CLOSE"
    return "WRONG"


def absolute_error_pp(record: dict, truth: Optional[GroundTruth]) -> Optional[float]:
    """Absolute error in percentage points, when both sides have a number."""
    if record.get("status") != "OK":
        return None
    value = record.get("parsed_carrier_frequency")
    if value is None or truth is None or truth.carrier_freq is None:
        return None
    if not (0.0 <= float(value) <= 1.0):
        return None
    return abs(float(value) - truth.carrier_freq) * 100.0


# ----------------------------------------------------------------------- scoring


def headline(counts: Dict[str, int], n_items: int, n_traced: int) -> str:
    """The one sentence the pitch rests on."""
    bad = counts.get("WRONG", 0) + counts.get("FABRICATED", 0)
    return (
        f"Asked without tools, the model was wrong or invented a value for {bad} of "
        f"{n_items} groups; our pipeline's {n_traced} values are each traced to a cited "
        f"record and replayed through the arithmetic."
    )


def coverage_sentence(counts: Dict[str, int], n_items: int, n_traced: int) -> str:
    """The second half of the story, which the headline alone does not carry.

    On this run the model mostly declined rather than inventing. Refusing is the
    honest failure mode, but it still cannot produce the table: a baseline that
    answers 3 of 10 groups is not a usable substitute for retrieval.
    """
    answered = (counts.get("EXACT", 0) + counts.get("CLOSE", 0)
                + counts.get("WRONG", 0) + counts.get("FABRICATED", 0))
    declined = counts.get("REFUSED", 0) + counts.get("ERROR", 0)
    return (
        f"Coverage: the no-tools baseline produced a usable number for {answered} of "
        f"{n_items} groups and declined or failed on {declined}; our pipeline produced "
        f"{n_traced} of {n_items}, each traced to a cited record."
    )


def score(
    cache_path: Path = BASELINE_CACHE,
    arm_b: Optional[dict] = None,
    truths: Optional[Dict[str, GroundTruth]] = None,
) -> Summary:
    """Score the cached Arm A responses. Pure: reads the cache, never the network."""
    cache = _load_cache(cache_path)
    responses = cache.get("responses", {})

    if truths is None:
        truths = ground_truth()
    if arm_b is None:
        arm_b = run_arm_b()

    counts = {c: 0 for c in CATEGORIES}
    errors: List[float] = []

    for item in items():
        record = responses.get(item.item_id)
        if record is None:
            counts["ERROR"] += 1
            continue
        counts[classify(record, truths.get(item.group))] += 1
        err = absolute_error_pp(record, truths.get(item.group))
        if err is not None:
            errors.append(err)

    tiers: Dict[str, int] = {}
    tiers[arm_b["tier"]] = tiers.get(arm_b["tier"], 0) + 1

    warned: Dict[str, int] = {}
    for cid in arm_b.get("critic_warned", []):
        warned[cid] = warned.get(cid, 0) + 1

    b_items = arm_b.get("items", [])
    lang_pass = sum(1 for i in b_items if i.get("language_passed"))
    n_traced = sum(1 for i in b_items if i.get("cited_fact_ids"))

    return Summary(
        n_items=len(items()),
        counts=counts,
        mean_absolute_error_pp=(sum(errors) / len(errors)) if errors else None,
        n_scored_numeric=len(errors),
        arm_b_tiers=tiers,
        arm_b_language_pass=lang_pass,
        arm_b_total=len(b_items),
        arm_b_warned_checks=warned,
        model=cache.get("model", "") or "(not recorded)",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        headline=headline(counts, len(items()), n_traced),
        coverage=coverage_sentence(counts, len(items()), n_traced),
    )


def render_summary(summary: Summary, arm_b: Optional[dict] = None) -> str:
    """The committed markdown report."""
    c = summary.counts
    mae = (f"{summary.mean_absolute_error_pp:.1f} percentage points"
           if summary.mean_absolute_error_pp is not None else "not applicable")

    lines = [
        "# GeneGap mini evaluation",
        "",
        f"_Generated {summary.generated_at}. Arm A model: `{summary.model}`._",
        "",
        "## Headline",
        "",
        f"**{summary.headline}**",
        "",
        f"{summary.coverage}",
        "",
        "## What was measured",
        "",
        f"{summary.n_items} items: the {DEMO_GENE} variant {DEMO_RSID} "
        f"(`{DEMO_VARIANT_ID}`) across the 10 gnomAD v4 genetic ancestry groups.",
        "",
        "- **Arm A** - the model alone, one call per item, no tools and no retrieval.",
        "- **Arm B** - our pipeline, replayed offline from the committed gnomAD, ClinVar "
        "and ClinicalTrials.gov payloads in `smoke/responses/`.",
        "",
        "Arm B is ground truth. Its figures are not a model's opinion: they are recorded "
        "allele counts put through the tested stats functions, so what Arm B is measured "
        "on here is whether the verification layers run clean on good input.",
        "",
        "## Arm A: the model answering from memory",
        "",
        "| Category | Meaning | Count |",
        "|---|---|---|",
        f"| EXACT | within {EXACT_PP:g} percentage points | {c['EXACT']} |",
        f"| CLOSE | within {CLOSE_PP:g} percentage points | {c['CLOSE']} |",
        f"| WRONG | outside {CLOSE_PP:g} percentage points | {c['WRONG']} |",
        f"| REFUSED | declined or said it could not know | {c['REFUSED']} |",
        f"| FABRICATED | confident number where no real value exists, or outside [0,1] "
        f"| {c['FABRICATED']} |",
        f"| ERROR | call never succeeded after retries | {c['ERROR']} |",
        "",
        f"Mean absolute error over the {summary.n_scored_numeric} numeric answers that "
        f"could be scored: **{mae}**.",
        "",
        "## Arm B: our pipeline, and what the verification layers said",
        "",
        f"- Critic tier: " + ", ".join(f"**{t}** ({n})" for t, n in summary.arm_b_tiers.items()),
        f"- Language check passed on {summary.arm_b_language_pass} of "
        f"{summary.arm_b_total} generated finding sentences",
    ]

    if summary.arm_b_warned_checks:
        lines.append(
            "- Critic checks that warned or failed: "
            + ", ".join(sorted(summary.arm_b_warned_checks))
        )
    else:
        lines.append("- No critic check warned or failed")

    if arm_b:
        lines += [
            f"- Mapping version: `{arm_b.get('mapping_version')}`; "
            f"{arm_b.get('n_cited_facts')} provenance facts cited",
            "",
            "### Per-group result (Arm B)",
            "",
            "| Group | AC | AN | Carrier frequency | Language |",
            "|---|---|---|---|---|",
        ]
        for i in arm_b.get("items", []):
            cf = i.get("carrier_freq")
            cf_text = "insufficient data" if cf is None else f"{cf * 100:.2f}%"
            lines.append(
                f"| {i['group']} | {i.get('ac')} | {i.get('an')} | {cf_text} "
                f"| {i.get('language_status')} |"
            )
        for cid, reason in (arm_b.get("critic_reasons") or {}).items():
            lines += ["", f"> {cid}: {reason}"]

    lines += [
        "",
        "## Honest limits",
        "",
        f"- The evaluation is small: {summary.n_items} items on one variant, the only one "
        "with committed fixtures. It is a sanity check, not proof.",
        "- Arm A is scored against our own pipeline's output. That output is itself "
        "derived from recorded gnomAD payloads, not from an independent gold standard.",
        "- Carrier frequency assumes Hardy-Weinberg equilibrium, which the model was "
        "told in the prompt, so a wrong answer is not a misreading of the question.",
        "- A single call per item at temperature 0. No sampling over repeats.",
        "- Arm B is correct by construction. Its purpose here is to show the critic and "
        "the language checker running clean, not to claim the pipeline validated itself.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GeneGap mini evaluation (task 6.1)")
    parser.add_argument("--offline", action="store_true",
                        help="score the cached responses only; make no API calls")
    parser.add_argument("--refresh", action="store_true",
                        help="re-ask items already in the cache")
    parser.add_argument("--model", default=None, help="override the Arm A model")
    parser.add_argument("--cache", default=str(BASELINE_CACHE))
    parser.add_argument("--out", default=str(SUMMARY_PATH))
    args = parser.parse_args(argv)

    cache_path = Path(args.cache)
    out_path = Path(args.out)

    if not args.offline:
        print(f"Arm A: asking {args.model or 'the configured model'} "
              f"for {len(items())} items, no tools ...")
        run_arm_a(cache_path=cache_path, model=args.model, refresh=args.refresh)
        print(f"Arm A: cached to {cache_path}")
    else:
        print("Offline: scoring the cached responses, no API calls.")

    print("Arm B: replaying the pipeline offline ...")
    arm_b = run_arm_b()
    truths = ground_truth()
    summary = score(cache_path=cache_path, arm_b=arm_b, truths=truths)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_summary(summary, arm_b))

    print()
    print(render_summary(summary, arm_b))
    print()
    print("HEADLINE:", summary.headline)
    print("COVERAGE:", summary.coverage)
    print(f"Summary written to {out_path}")
    return 0


if __name__ == "__main__":        # pragma: no cover
    raise SystemExit(main())
