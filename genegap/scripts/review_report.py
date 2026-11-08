"""Tasks 5.1/5.2: review sourced findings and render a Markdown report.

Run without arguments to preview the empty report template. A real run takes a
JSON handoff containing run_id, title, flags and limitations, plus the SQLite
database in which the agents stored the supporting Facts.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import BaseModel, ConfigDict, Field

from genegap.config import DB_PATH, ROOT
from genegap.reporting import validate_report_destination
from genegap.schemas import Flag
from genegap.store import Store
from review.app import show_finding
from review.session import FindingView, ReviewSession


class ReportDraft(BaseModel):
    """File handoff for the report; the shared Flag schema stays unchanged."""

    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1)
    title: str = "GeneGap findings"
    flags: list[Flag]
    limitations: list[str] = Field(default_factory=list)


def _ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    response = sys.stdin.readline()
    if not response:
        raise EOFError("Review input ended. No final report was exported.")
    return response.strip()


def _show(view: FindingView) -> None:
    print("\nDRAFT — human review preview.", file=sys.stderr)
    # Display the source snapshots bound to this view's revision token, rather
    # than rereading live Facts after the view was captured.
    with redirect_stdout(sys.stderr):
        show_finding(view)


def _review(session: ReviewSession, reviewer: str) -> None:
    _draft, initial_views = session.overview()
    for initial in initial_views:
        view = session.view(initial.index)
        _show(view)
        while True:
            action = _ask("Decision [approve/reject/annotate/skip/quit]: ").lower()
            if action == "quit":
                raise EOFError("Review stopped. Recorded decisions remain in the audit log.")
            if action == "skip":
                break
            if action not in {"approve", "reject", "annotate"}:
                print("Enter approve, reject, annotate, skip, or quit.", file=sys.stderr)
                continue
            note = _ask("Reviewer note (required for annotate): ")
            try:
                reviewed = session.review(view.index, action, reviewer, note, revision=view.revision)
            except ValueError as exc:
                print(f"Decision not recorded: {exc}", file=sys.stderr)
                view = session.view(view.index)
                _show(view)
                continue
            print(f"Recorded {action} for {reviewed.variant_id}.", file=sys.stderr)
            if action != "annotate":
                break
            # An annotation leaves the finding open for a subsequent decision.
            view = session.view(view.index)
            _show(view)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "draft", nargs="?", type=Path,
        default=ROOT / "data" / "report_draft.example.json",
        help="JSON from the agents; defaults to the empty draft template",
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Agents' SQLite Fact and review store")
    parser.add_argument("--review", action="store_true", help="Ask a human to review each finding")
    parser.add_argument("--reviewer", help="Name of the person making the decisions")
    parser.add_argument("--final", action="store_true", help="Export only after every finding is reviewed")
    parser.add_argument("--output", type=Path, help="Write Markdown to a file instead of stdout")
    args = parser.parse_args(argv)
    if args.review and (not args.reviewer or not args.reviewer.strip()):
        parser.error("--review requires a nonblank --reviewer name")
    if args.reviewer and not args.review:
        parser.error("--reviewer requires --review")

    store = None
    try:
        store = Store(args.db)
        session = ReviewSession(args.draft, store)
        if args.output:
            validate_report_destination(args.output, args.draft, store)
        if args.review:
            _review(session, args.reviewer)
        if args.output:
            session.export(args.output, final=args.final)
            print(f"Wrote {'final' if args.final else 'draft'} report to {args.output}.")
        else:
            print(session.report(final=args.final), end="")
        return 0
    except (OSError, ValueError, EOFError, sqlite3.Error) as exc:
        print(f"Report not exported: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nReview stopped. Recorded decisions remain in the audit log.", file=sys.stderr)
        return 130
    finally:
        if store is not None:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
