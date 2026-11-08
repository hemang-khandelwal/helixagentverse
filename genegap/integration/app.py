"""Task 6.4: a query-to-review launcher for Programmer 1's real orchestrator."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from genegap.store import Store
from integration.bridge import RunFailed, open_review, run_pipeline
from integration.entrypoint import resolve_entrypoint
from review.app import dashboard, show_queue


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="Drug, gene or condition to pass to the orchestrator")
    parser.add_argument("--orchestrator", help="Trusted local module:function entry point; defaults to GeneGap's orchestrator")
    parser.add_argument("--offline", action="store_true", help="Use recorded data and curated query resolution without a model or network")
    parser.add_argument("--check", action="store_true", help="Check the callable contract without starting a run")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "integration" / "runs")
    parser.add_argument("--db", type=Path, help="Optional shared API/Fact cache; otherwise each run gets its own database")
    parser.add_argument("--reviewer", help="Person making human decisions after the agent run")
    parser.add_argument("--list", action="store_true", help="After running, show the review queue and exit")
    args = parser.parse_args(argv)
    if args.offline and args.orchestrator:
        parser.error("--offline selects the recorded orchestrator; omit --orchestrator")
    selected = (
        "genegap.agents.orchestrator:run_offline_query" if args.offline
        else args.orchestrator or os.getenv("GENEGAP_ORCHESTRATOR") or "genegap.agents.orchestrator:run_query"
    )
    try:
        callback = resolve_entrypoint(selected)
    except Exception as exc:
        print(f"Orchestrator unavailable: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"Callable is ready: {selected}. No query or live validation was run.")
        return 0

    query = args.query
    if query is None:
        print("Drug, gene or condition: ", end="", flush=True)
        try:
            query = sys.stdin.readline().strip()
        except KeyboardInterrupt:
            print("\nStopped before starting a run.")
            return 130
    if not query.strip():
        print("A nonblank query is required; no run was started.", file=sys.stderr)
        return 2

    def progress(kind, payload):
        print(json.dumps({"event": kind, "data": payload}, ensure_ascii=False), file=sys.stderr, flush=True)

    store = None
    run = None
    try:
        run = run_pipeline(callback, query, output_root=args.output_dir, db_path=args.db, on_event=progress)
        print(f"Ready for human review: {run.draft_path}")
        store = Store(run.db_path)
        session = open_review(run, store)
        if args.list:
            show_queue(session)
            return 0
        return dashboard(session, args.reviewer)
    except RunFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except EOFError:
        print("\nStopped. Completed runs and recorded human decisions remain saved.")
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nReview closed. The completed run remains saved." if run else "\nStopped. The interrupted run was not published for review.")
        return 130
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Integration could not continue: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
