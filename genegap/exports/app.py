"""Task 6.5: export a reviewed summary, sources and audit records as one ZIP."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from exports.bundle import export_bundle
from genegap.config import DB_PATH
from genegap.store import Store
from review.session import ReviewSession


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", nargs="?", type=Path, default=ROOT / "data" / "report_draft.example.json")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="The original Fact and human-review database")
    parser.add_argument("--output", type=Path, default=ROOT / "exports" / "output" / "report.zip")
    parser.add_argument("--final", action="store_true", help="Require completed human review for every finding")
    args = parser.parse_args(argv)
    store = None
    try:
        store = Store(args.db)
        destination = export_bundle(ReviewSession(args.draft, store), args.output, final=args.final)
        print(f"Task 6.5: exported {'FINAL' if args.final else 'DRAFT'} report to {destination}")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Export not written: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
