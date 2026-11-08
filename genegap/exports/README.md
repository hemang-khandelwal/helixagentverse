# Task 6.5 — portable report export

Longer programming guide **6.5**. All additions stay in `genegap/exports/`.
No extra dependencies are needed.

From `genegap/`, preview the existing empty template as a draft ZIP:

```sh
.venv/bin/python exports/app.py
```

For a real run, use its handoff and the same database used for human review:

```sh
.venv/bin/python exports/app.py data/report_draft.json --db data/genegap.sqlite --final --output exports/output/final.zip
```

The ZIP contains:

- `report.md`: readable summary with sources and limits.
- `report.json`: findings, rejections and report metadata.
- `facts.json`: exact supporting source records.
- `reviews.json`: current recorded human decisions and reviewed snapshots.
- `manifest.json`: task/run/status, UTC export time and file checksums.

Final exports require all findings to have audited approvals or rejections.
Pending, stale or withdrawn clearance blocks final export. The default template
has no findings and can only export as draft. Rejected findings remain separate
from approved findings; their reviewed history is retained for audit.

Each bundle uses one consistent source/review snapshot. Files are packaged before
the destination is replaced, preserving an earlier ZIP on failure. The input
handoff and database cannot be overwritten. Environment files, the raw agent
trace and the database itself are not packaged; it includes selected findings and their
provenance/review records. Approval covers findings and source records; report
titles and additional context notes remain author-supplied.

The Python API is `export_bundle(session, destination, final=False)` in `bundle.py`.
Actual live results still depend on Programmer 1's task 3.11 orchestrator.

```sh
.venv/bin/python -m pytest exports/tests -q
```
