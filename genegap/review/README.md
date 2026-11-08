# Task 6.3 — human sign-off

This is **6.3 in the longer GeneGap programming guide**: approve, reject or
annotate findings, persist an audit log, and prevent final reports until review
is complete. This terminal dashboard builds on the existing `SignOffGate` and
report template. All new files for this task live under `genegap/review/`.

## Open the dashboard

From `genegap/`:

```sh
.venv/bin/python review/app.py
```

The default input is the existing empty report template. It does not invent
findings, critic results or approvals. To see the queue without entering the
interactive dashboard:

```sh
.venv/bin/python review/app.py --list
```

For real findings, use the agents' JSON handoff and the database containing
their supporting Facts:

```sh
.venv/bin/python review/app.py data/report_draft.json --db data/genegap.sqlite --reviewer "Your name"
```

No new dependencies are required beyond the existing project requirements.
This is a local terminal interface; it does not launch a website or contact an
external service.

## Review workflow

1. `list` shows every finding and whether it is pending, approved, rejected,
   annotated, or stale.
2. `view 1` opens a finding with its exact source values, URLs, evidence tier,
   citations, mapping verdict, and previous review.
3. Enter `approve`, `reject`, or `annotate`, then supply a note. An annotation
   requires a note and leaves the finding pending. Use `back` to return to the
   queue. If `--reviewer` was omitted, the dashboard asks for a name when a
   decision is made.
4. `history` shows saved reviewer names, UTC times, decisions, findings and notes
   for the current run.
5. `draft` saves a draft; `final` attempts a final export. Defaults are
   `review/output/draft.md` and `review/output/final.md`. Pass a quoted path, such
   as `final "reports/final review.md"`, to choose another destination.
6. `quit` exits. Reopening the original handoff restores matching saved decisions.

The dashboard reads the handoff again for each action. If the finding, source
records or another review changes while a person is reading, the old view cannot
authorize a decision: it must be reopened. Final validation and formatting use
one database snapshot so concurrent source updates cannot slip into an approved
report.

## Handoff contract

The input format matches `scripts/review_report.py`:

```python
payload = {
    "run_id": run_id,
    "title": "GeneGap findings",
    "flags": [flag.model_dump(mode="json") for flag in flags],
    "limitations": limitations,
}
```

The dashboard never changes this JSON or the shared scientific schemas. Human
decisions are stored through the existing `Store` and `SignOffGate` APIs, with
full reviewed snapshots. UI-independent callers can use `ReviewSession` from
`review/session.py` for the same queue, version checks, audit history and export
behavior.

Approval requires the existing critic-cleared status, evidence tier, citations,
and stored source Facts. The critic and reviewer determine whether those sources
actually support the claim; this interface does not generate critic clearance.
Reviewer names are entered locally and are not authenticated identities.

A final report needs an audited approve/reject decision for every current
finding. Annotations, stale reviews, missing evidence and empty queues block
final export. Rejected findings are excluded from approved findings and retained
in a separate rejection section. Recorded approvals cover findings and their
source records; the report title and additional context notes remain explicitly
labelled author-supplied material.

Actual agent and critic integration is the following task, **6.4**. The empty
example remains empty until real output is supplied.

## Validate

```sh
.venv/bin/python -m pytest review/tests -q
.venv/bin/python -m pytest tests charts/tests review/tests -q
```

Shared README files, schemas, core calculations, agents and requirements are
unchanged by this task. This folder can be merged separately from those lanes.
