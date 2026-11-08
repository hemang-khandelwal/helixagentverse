"""Tasks 2.2 and 2.3: find trials and read their baseline participant tables.

2.2  drug or condition -> a list of completed trials that posted results
2.3  one trial's raw record -> BaselineDemographics (race, ethnicity, sex), plus a
     missing-data rate and a plain-English log of every judgement the parser made.

ClinicalTrials.gov baseline tables are not uniform. The three traps named in the build
plan are all handled here and each one leaves a note in the output:

  * some trials report counts, others report percentages;
  * multi-arm trials repeat every category once per arm, so summing them double-counts
    participants unless the registry's own "Total" column is used;
  * race and ethnicity are separate measures, and a participant appears in both.

Nothing in this module calls an LLM and nothing here invents a number: a value either
comes from the record or the category is left out and noted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .clients.base import ApiError
from .schemas import BaselineDemographics, Trial

# How a baseline measure's title maps onto the schemes we care about. Order matters:
# "Race (NIH/OMB)" and "Race/Ethnicity, Customized" both exist in the registry, so
# ethnicity is tested before race only where the title does not also mention race.
SCHEME_PATTERNS: list[tuple[str, str]] = [
    ("ethnicity", r"ethnicit|hispanic|latino"),
    ("race", r"\brace\b|racial"),
    ("sex", r"\bsex\b|\bgender\b"),
    ("age", r"\bage\b"),
]

# paramTypes that give us a headcount we can use directly.
COUNT_PARAM_TYPES = {"COUNT_OF_PARTICIPANTS", "COUNT_OF_UNITS", "NUMBER"}

# Labels that mean "we do not know", which are never reassigned to an ancestry group.
MISSING_LABELS = ("unknown", "not reported", "not specified", "unspecified", "missing", "not collected")


class BaselineError(ValueError):
    """The record is shaped in a way the parser refuses to guess about."""


@dataclass
class ParsedBaseline:
    """Everything task 2.3 produces for one trial."""

    nct_id: str
    demographics: list[BaselineDemographics] = field(default_factory=list)
    total_participants: Optional[int] = None
    group_basis: str = "none"        # "total_row", "summed_arms" or "none"
    has_results: bool = False
    notes: list[str] = field(default_factory=list)

    def by_scheme(self, scheme: str) -> Optional[BaselineDemographics]:
        for d in self.demographics:
            if d.scheme == scheme:
                return d
        return None

    @property
    def has_race_data(self) -> bool:
        return self.by_scheme("race") is not None

    @property
    def missing_fraction(self) -> Optional[float]:
        """Share of participants whose race was unknown or not reported."""
        race = self.by_scheme("race")
        return race.missing_fraction if race else None


def classify_measure(title: str) -> Optional[str]:
    """Return 'race', 'ethnicity', 'sex', 'age' or None for a baseline measure title."""
    t = (title or "").lower()
    # A combined "Race/Ethnicity" measure is treated as race: it carries the race categories
    # that the ancestry mapping is written against.
    if re.search(r"\brace\b|racial", t):
        return "race"
    for scheme, pattern in SCHEME_PATTERNS:
        if re.search(pattern, t):
            return scheme
    return None


def is_missing_label(label: str) -> bool:
    """True for categories that record absent information rather than a population."""
    low = (label or "").lower()
    return any(term in low for term in MISSING_LABELS)


def _to_number(raw: Any) -> Optional[float]:
    """Parse a registry measurement value, which always arrives as a string."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"NA", "N/A", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _denominator_map(denoms: list[dict]) -> dict[str, int]:
    """Map groupId -> participant count from a denoms block, ignoring non-participant units."""
    out: dict[str, int] = {}
    for block in denoms or []:
        if "participant" not in str(block.get("units", "")).lower():
            continue
        for c in block.get("counts") or []:
            n = _to_number(c.get("value"))
            if c.get("groupId") and n is not None:
                out[c["groupId"]] = int(round(n))
    return out


def choose_group_ids(groups: list[dict], denominators: dict[str, int]) -> tuple[list[str], str, list[str]]:
    """
    Decide which baseline column(s) to read.

    Returns (group_ids, basis, notes). If the registry supplies its own "Total" column we use
    only that, which is the one safe way to avoid double-counting overlapping arms. Otherwise
    we sum every arm and say so, because summing is only correct when the arms are disjoint.
    """
    notes: list[str] = []
    totals = [g for g in groups if str(g.get("title", "")).strip().lower() in {"total", "all participants", "overall"}]
    if len(totals) == 1:
        return [totals[0]["id"]], "total_row", notes

    ids = [g["id"] for g in groups if g.get("id")]
    if len(totals) > 1:
        notes.append(
            f"{len(totals)} columns are called 'Total'; summing all {len(ids)} columns instead, "
            "which may double-count participants. A human should check this trial."
        )
    elif len(ids) > 1:
        notes.append(
            f"No 'Total' column: summing {len(ids)} arms. This is only correct if the arms do not "
            "share participants, which the registry does not state."
        )
    return ids, "summed_arms", notes


def _measure_counts(
    measure: dict,
    group_ids: list[str],
    denominators: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """Read one baseline measure into {category label: participant count}."""
    notes: list[str] = []
    param = str(measure.get("paramType", "")).upper()
    unit = str(measure.get("unitOfMeasure", "")).lower()
    title = measure.get("title", "(untitled)")

    # A measure may carry its own denominators, which take precedence over the module's.
    local_denoms = _denominator_map(measure.get("denoms") or [])
    denoms = {**denominators, **local_denoms}

    is_percentage = "percent" in unit or "%" in unit
    if param not in COUNT_PARAM_TYPES:
        return {}, [f"'{title}': skipped, paramType {param or 'unset'} is not a headcount."]
    if is_percentage and not denoms:
        return {}, [f"'{title}': reported as percentages with no participant denominator, so no counts can be derived."]

    classes = measure.get("classes") or []
    if len(classes) > 1:
        notes.append(
            f"'{title}': {len(classes)} sub-tables are summed together. A human should confirm "
            "they are not overlapping breakdowns of the same participants."
        )

    counts: dict[str, int] = {}
    for cls in classes:
        for cat in cls.get("categories") or []:
            label = (cat.get("title") or cls.get("title") or "").strip()
            if not label:
                continue
            subtotal = 0.0
            seen = False
            for meas in cat.get("measurements") or []:
                gid = meas.get("groupId")
                if gid not in group_ids:
                    continue
                value = _to_number(meas.get("value"))
                if value is None:
                    continue
                if is_percentage:
                    denom = denoms.get(gid)
                    if denom is None:
                        continue
                    value = value / 100.0 * denom
                subtotal += value
                seen = True
            if seen:
                counts[label] = counts.get(label, 0) + int(round(subtotal))

    if is_percentage and counts:
        notes.append(
            f"'{title}': the trial reported percentages; counts were derived as percentage x "
            "participants and are therefore approximate."
        )
    return counts, notes


def parse_baseline(study: dict, nct_id: Optional[str] = None) -> ParsedBaseline:
    """
    Task 2.3. Turn one ClinicalTrials.gov study record into BaselineDemographics.

    Pure function: no network, no store. `study` is the full JSON from
    CTGovClient.study_raw(). Trials with no posted results come back with an empty
    demographics list and a note saying so, never an exception.
    """
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    nct = nct_id or ident.get("nctId") or ""
    if not nct:
        raise BaselineError("study record has no NCT ID")

    out = ParsedBaseline(nct_id=nct, has_results=bool(study.get("hasResults")))

    results = study.get("resultsSection") or {}
    module = results.get("baselineCharacteristicsModule") or {}
    if not module:
        out.notes.append(
            "No baseline characteristics table posted. Eligibility-only analysis is the fallback here."
        )
        return out

    groups = module.get("groups") or []
    denominators = _denominator_map(module.get("denoms") or [])
    group_ids, basis, group_notes = choose_group_ids(groups, denominators)
    out.group_basis = basis
    out.notes += group_notes

    if not group_ids:
        out.notes.append("Baseline table has no participant columns; nothing could be read.")
        return out

    out.total_participants = sum(denominators[g] for g in group_ids if g in denominators) or None
    if out.total_participants is None:
        out.notes.append("The baseline table does not state how many participants each column holds.")

    for measure in module.get("measures") or []:
        scheme = classify_measure(measure.get("title", ""))
        if scheme is None or scheme == "age":
            # Age is reported as a mean in most trials and is not used by the gap maths.
            continue
        counts, notes = _measure_counts(measure, group_ids, denominators)
        out.notes += notes
        if not counts:
            continue
        if out.by_scheme(scheme) is not None:
            out.notes.append(
                f"More than one '{scheme}' measure found; keeping the first and ignoring "
                f"'{measure.get('title')}'."
            )
            continue
        out.demographics.append(
            BaselineDemographics(
                nct_id=nct,
                scheme=scheme,  # type: ignore[arg-type]
                counts=counts,
                total_participants=out.total_participants,
            )
        )

    if not out.has_race_data:
        out.notes.append("No race breakdown in the baseline table, so this trial cannot be mapped to ancestry groups.")
    else:
        missing = out.missing_fraction
        if missing:
            out.notes.append(f"{missing:.1%} of participants have race recorded as unknown or not reported.")

    return out


# ---------- provenance-recording wrappers (these do touch the network) ----------

def baseline_for_trial(ctgov, nct_id: str) -> ParsedBaseline:
    """Fetch one trial's full record, parse it, and store a Fact per demographic category."""
    study = ctgov.study_raw(nct_id)
    parsed = parse_baseline(study, nct_id=nct_id)
    url = f"https://clinicaltrials.gov/study/{nct_id}?tab=results"

    for demo in parsed.demographics:
        fact_ids = []
        for label, count in demo.counts.items():
            f = ctgov.fact(
                field=f"baseline:{demo.scheme}:{label}",
                value={"count": count, "basis": parsed.group_basis},
                record_id=nct_id,
                url=url,
                note=f"Read from the posted baseline table using the '{parsed.group_basis}' column rule.",
            )
            fact_ids.append(f.id)
        demo.fact_ids = fact_ids
    return parsed


def collect_trials(
    ctgov,
    intervention: Optional[str] = None,
    condition: Optional[str] = None,
    max_trials: int = 10,
) -> tuple[list[Trial], list[str]]:
    """
    Tasks 2.2 + 2.3 together: search for completed trials with results, then fill in each
    one's baseline demographics. Returns (trials, notes). A trial whose record cannot be
    fetched is skipped with a note rather than stopping the run.
    """
    notes: list[str] = []
    try:
        found = ctgov.search(intervention=intervention, condition=condition, has_results=True)
    except ApiError as e:
        return [], [f"Trial search failed: {e}"]

    if not found:
        return [], [f"No completed trials with posted results for {intervention or condition!r}."]

    notes.append(f"Found {len(found)} completed trials with results; reading the first {min(max_trials, len(found))}.")
    trials: list[Trial] = []
    for trial in found[:max_trials]:
        try:
            parsed = baseline_for_trial(ctgov, trial.nct_id)
        except ApiError as e:
            notes.append(f"{trial.nct_id}: could not fetch the full record ({e}).")
            continue
        trial.baseline = parsed.demographics
        trials.append(trial)
        for n in parsed.notes:
            notes.append(f"{trial.nct_id}: {n}")

    usable = [t for t in trials if any(d.scheme == "race" for d in t.baseline)]
    notes.append(f"{len(usable)} of {len(trials)} trials report a race breakdown.")
    return trials, notes


def pooled_race_counts(trials: list[Trial]) -> tuple[dict[str, int], list[str], list[str]]:
    """
    Add up race categories across several trials.

    Returns (counts, nct_ids_used, notes). Pooling assumes the trials do not share
    participants; that assumption is returned as a note so the report can state it.
    """
    counts: dict[str, int] = {}
    used: list[str] = []
    for t in trials:
        race = next((d for d in t.baseline if d.scheme == "race"), None)
        if race is None:
            continue
        used.append(t.nct_id)
        for label, n in race.counts.items():
            counts[label] = counts.get(label, 0) + n
    notes = []
    if len(used) > 1:
        notes.append(
            f"Counts are pooled across {len(used)} trials ({', '.join(used)}), which assumes no "
            "participant took part in more than one of them."
        )
    return counts, used, notes
