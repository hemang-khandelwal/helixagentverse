"""Task 6.3 in the longer guide: interactive human sign-off dashboard."""
from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from genegap.config import DB_PATH
from genegap.store import Store
from review.session import FindingView, ReviewSession


def _plain(value: object) -> str:
    # Render externally supplied text literally, including terminal control bytes.
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def _ask(prompt: str) -> str:
    print(prompt, end="", flush=True)
    value = sys.stdin.readline()
    if not value:
        raise EOFError
    return value.strip()


def show_queue(session: ReviewSession) -> None:
    draft, views = session.overview()
    print(f"\nGeneGap · Task 6.3 · Human sign-off\n{_plain(draft.title)}\nRun: {_plain(draft.run_id)}")
    print("\n No.  Variant                       Human review")
    print(" " + "-" * 68)
    for view in views:
        print(f" {view.index + 1:>3}  {_plain(view.flag.variant_id):<28}  {view.status}")
    if not views:
        print(" No findings supplied. Agent and critic output is needed before review.")
    print("\nResearch findings for expert review. Approval requires critic clearance and source records.")
    if draft.limitations:
        print("\nReport author's context notes:")
        for note in draft.limitations:
            print(f"  - {_plain(note)}")


def show_finding(view: FindingView) -> None:
    flag = view.flag
    print(f"\nFinding {view.index + 1}: {_plain(flag.variant_id)}\n{_plain(flag.claim)}")
    print(f"Human review: {view.status}")
    print(f"Supplied pipeline status: {_plain(view.source_flag.status)}")
    print(f"Evidence tier: {flag.evidence_tier if flag.evidence_tier is not None else 'not graded'}")
    robustness = {True: "reported robust", False: "not robust", None: "not assessed"}
    print(f"Mapping robustness: {robustness[flag.robust_across_mappings]}")
    print("Citations: " + ("; ".join(_plain(citation) for citation in flag.citations) or "none supplied"))
    if view.decision:
        print(f"Last decision: {_plain(view.decision.get('decision', 'unknown'))}")
        print(f"Reviewer: {_plain(view.decision.get('reviewer', 'unknown'))}")
        print(f"Time: {_plain(view.decision.get('reviewed_at', 'unknown'))}")
        print(f"Note: {_plain(view.decision.get('note', ''))}")
    print("\nSupporting source records:")
    if not view.facts:
        print("  No supporting Facts supplied.")
    for fact_id, fact in zip(flag.fact_ids, view.facts):
        if fact is None:
            print(f"  Missing source Fact: {_plain(fact_id)}")
        else:
            print(json.dumps(fact.model_dump(mode="json"), indent=2, ensure_ascii=False))


def inspect(session: ReviewSession, index: int, reviewer: Optional[str]) -> None:
    view = session.view(index)
    show_finding(view)
    while True:
        action = _ask("\nDecision [approve/reject/annotate/back]: ").lower()
        if action == "back":
            return
        if action not in {"approve", "reject", "annotate"}:
            print("Enter approve, reject, annotate, or back.")
            continue
        identity = reviewer if reviewer and reviewer.strip() else _ask("Reviewer name: ")
        note = _ask("Reviewer note (required for annotate): ")
        try:
            session.review(index, action, identity, note, revision=view.revision)
        except ValueError as exc:
            print(f"Decision not recorded: {_plain(exc)}")
        else:
            print(f"Recorded {action} by {_plain(identity)}.")
        # Show current evidence again before another decision, including after
        # annotations and rejected attempts to approve a changed finding.
        view = session.view(index)
        show_finding(view)


def dashboard(session: ReviewSession, reviewer: Optional[str]) -> int:
    show_queue(session)
    print("\nCommands: list | view NUMBER | history | draft [PATH] | final [PATH] | quit")
    while True:
        try:
            parts = shlex.split(_ask("\nreview> "))
            if not parts:
                continue
            command = parts[0].lower()
            if command in {"quit", "exit"} and len(parts) == 1:
                return 0
            if command == "list" and len(parts) == 1:
                show_queue(session)
            elif command == "view" and len(parts) == 2:
                inspect(session, int(parts[1]) - 1, reviewer)
            elif command == "history" and len(parts) == 1:
                history = session.history()
                if not history:
                    print("No human decisions recorded for this run.")
                for step in history:
                    event = step["payload"]
                    finding = event.get("flag", {})
                    values = [event.get(key, "") for key in ("reviewed_at", "reviewer", "decision")]
                    values.extend([finding.get("variant_id", ""), finding.get("claim", ""), event.get("note", "")])
                    print(" | ".join(_plain(value) for value in values))
            elif command in {"draft", "final"} and len(parts) <= 2:
                path = Path(parts[1]) if len(parts) == 2 else ROOT / "review" / "output" / f"{command}.md"
                saved = session.export(path, final=command == "final")
                print(f"Saved {command} report: {_plain(saved)}")
            else:
                print("Use list, view NUMBER, history, draft [PATH], final [PATH], or quit.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            print(f"Action blocked: {_plain(exc)}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", nargs="?", type=Path, default=ROOT / "data" / "report_draft.example.json")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Existing Fact and review database")
    parser.add_argument("--reviewer", help="Name of the person making decisions; otherwise asked at each decision")
    parser.add_argument("--list", action="store_true", help="Show the queue and exit without changing review decisions")
    args = parser.parse_args(argv)
    store = None
    try:
        store = Store(args.db)
        session = ReviewSession(args.draft, store)
        if args.list:
            show_queue(session)
            return 0
        return dashboard(session, args.reviewer)
    except (EOFError, KeyboardInterrupt):
        print("\nReview closed. Saved decisions remain in the audit log.")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Review could not open: {_plain(exc)}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
