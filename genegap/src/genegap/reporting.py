"""Task 5.2: a report template over sourced flags, with audited final export.

Task numbers follow the three-hour plan. In the longer programming guide this
is the report export portion of task 6.5. This module formats existing values;
it does not calculate statistics, grade evidence, or replace the critic.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlsplit

from .schemas import Fact, Flag
from .signoff import SignOffGate
from .store import Store


def validate_report_destination(destination: Path, draft_path: Path, store: Store) -> None:
    """Reject report paths that would overwrite the handoff or SQLite files.

    Resolve symbolic links and also compare existing inodes so hard links cannot
    bypass the protection. Auxiliary SQLite paths are reserved even when the
    journal or shared-memory file has not been created yet.
    """
    destination = Path(destination)
    protected = [Path(draft_path)]
    for row in store.db.execute("PRAGMA database_list").fetchall():
        if row["file"]:
            protected.extend(Path(row["file"] + suffix) for suffix in ("", "-wal", "-shm", "-journal"))
    for source in protected:
        if destination.resolve() == source.resolve() or (
            destination.exists() and source.exists() and destination.samefile(source)
        ):
            raise ValueError("A report cannot overwrite the input handoff or review database files.")


def _literal(value: object) -> str:
    """Display supplied text without interpreting its Markdown or HTML."""
    text = " ".join(str(value).splitlines())
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_\[\]{}#|!])", r"\\\1", text)


def _source_link(url: str) -> str:
    """Only link web URLs; never activate a scheme supplied by external text."""
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if (
        parts is not None
        and parts.scheme.lower() in {"http", "https"}
        and parts.netloc
        and not any(char.isspace() or ord(char) < 32 for char in url)
    ):
        safe_url = quote(url, safe="/:?&=#%;+,@!$~")
        return f"[Open source](<{safe_url}>)"
    return f"Source URL: {_literal(url)}" if url else "Source URL not provided."


def _fact_lines(fact: Fact) -> list[str]:
    # A dynamically sized fence also handles literal backticks in retrieved
    # strings. JSON preserves source values without rounding or recomputation.
    value = json.dumps(fact.value, ensure_ascii=False, sort_keys=True, default=str)
    runs = [len(match.group()) for match in re.finditer(r"`+", value)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    lines = [
        f"**Fact {_literal(fact.id)} — {_literal(fact.field)}**",
        "",
        f"{fence}json",
        value,
        fence,
        "",
        f"Source: {_literal(fact.source)}; record: {_literal(fact.record_id)}; "
        f"method: {_literal(fact.method)}; retrieved: {_literal(fact.retrieved_at.isoformat())}.",
        "",
        _source_link(fact.url),
    ]
    if fact.quote:
        lines.extend(["", f"Supporting excerpt: {_literal(fact.quote)}"])
    if fact.note:
        lines.extend(["", f"Source note: {_literal(fact.note)}"])
    return lines


def _review_lines(flag: Flag, gate: SignOffGate) -> list[str]:
    decision = gate.decision_for(flag)
    if decision is not None and decision.get("stale", False):
        return [
            "Review status: pending — content changed since the recorded review.",
            "Human review is required for this version of the finding.",
        ]
    lines = [f"Review status: {_literal(flag.status)}."]
    if decision is None:
        lines.append("No human review is recorded for this version of the finding.")
    else:
        lines.extend([
            f"Recorded decision: {_literal(decision['decision'])}; "
            f"reviewer: {_literal(decision['reviewer'])}; "
            f"reviewed: {_literal(decision['reviewed_at'])}.",
            f"Reviewer note: {_literal(decision['note']) or 'None provided.'}",
        ])
    return lines


def _render_report(
    flags: Iterable[Flag],
    gate: SignOffGate,
    title: str = "GeneGap findings",
    limitations: Iterable[str] = (),
    final: bool = False,
) -> str:
    """Render Markdown; final reports require a matching audit for every flag.

    Drafts may show incomplete evidence and unreviewed flags. A final report
    contains approved findings and a separate record of reviewed rejections.
    All numbers are copied from supplied flags and stored Facts.
    """
    flags = list(flags)
    if final:
        gate.require_finalizable(flags)

    lines = [
        f"# {_literal(title)}",
        "",
        "**FINAL — findings have completed human review.**" if final else "**DRAFT — human review is required before final export.**",
        "",
        f"Run: {_literal(gate.run_id)}",
        "",
        "These findings are research hypotheses for expert review. They are not "
        "individual genotype interpretations, treatment recommendations, or dosing advice.",
        "",
        "Recorded approvals cover the findings and their cited source records. "
        "The report title and any additional context notes are supplied by the report author.",
        "",
        "## Findings",
        "",
    ]
    included = [flag for flag in flags if not final or flag.status == "human_approved"]
    if not included:
        lines.extend(["No findings approved." if final else "No findings supplied.", ""])

    for flag in included:
        robustness = {
            True: "Reported as surviving all evaluated mappings.",
            False: "Not robust across the evaluated mappings.",
            None: "Not assessed.",
        }[flag.robust_across_mappings]
        tier = str(flag.evidence_tier) if flag.evidence_tier is not None else "Not graded"
        lines.extend([
            f"### Variant {_literal(flag.variant_id)}",
            "",
            _literal(flag.claim),
            "",
            f"Evidence tier: {tier}.",
            "",
            "Citations: " + (
                "; ".join(_literal(citation) for citation in flag.citations)
                if flag.citations else "None supplied."
            ),
            "",
            f"Mapping robustness: {robustness}",
            "",
            *_review_lines(flag, gate),
            "",
            "Supporting source records:",
            "",
        ])
        if not flag.fact_ids:
            lines.extend(["No supporting fact IDs supplied.", ""])
        for fact_id in dict.fromkeys(flag.fact_ids):
            fact = gate.store.get_fact(fact_id)
            if fact is None:
                lines.extend([f"Source fact unavailable: {_literal(fact_id)}.", ""])
            else:
                lines.extend([*_fact_lines(fact), ""])

    rejected = [flag for flag in flags if final and flag.status == "rejected"]
    if rejected:
        lines.extend(["## Rejected findings", ""])
        for flag in rejected:
            # Rejected claims are omitted from the approved findings. Retain
            # the decision and audit metadata so rejection is still visible.
            lines.extend([
                f"### Variant {_literal(flag.variant_id)}",
                "",
                *_review_lines(flag, gate),
                "",
            ])

    lines.extend([
        "## Limits and assumptions",
        "",
        "Self-identified trial race and ethnicity and genetic ancestry are different "
        "measures. Their mapping is an explicit assumption.",
        "",
        "Carrier estimates do not predict individual drug response. Missing evidence "
        "does not establish safety.",
        "",
    ])
    limitations = list(limitations)
    if limitations:
        lines.extend(["Report author's context notes (outside per-finding approval):", ""])
    for limitation in limitations:
        lines.extend([f"- {_literal(limitation)}", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_report(
    flags: Iterable[Flag],
    gate: SignOffGate,
    title: str = "GeneGap findings",
    limitations: Iterable[str] = (),
    final: bool = False,
) -> str:
    """Format reports, keeping final validation and source reads consistent.

    A final report holds the database stable until formatting is complete. An
    existing caller transaction, such as ReviewSession's, is reused without a
    nested BEGIN or a commit of the caller's work.
    """
    if not final or gate.store.db.in_transaction:
        return _render_report(flags, gate, title, limitations, final)
    with gate.store.db:
        gate.store.db.execute("BEGIN IMMEDIATE")
        return _render_report(flags, gate, title, limitations, final)
