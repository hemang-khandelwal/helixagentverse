# Programmer 3: report and demo

Task numbers below use **GeneGap_3hr_build_plan.pdf**, which explicitly assigns
Programmer 3 the report and demo lane. The longer programming guide uses different
numbers for some of the same work.

| Three-hour plan | Programmer 3 work | Longer programming guide | Current state |
| --- | --- | --- | --- |
| **5.1** | Human sign-off: approve, reject or annotate each finding | **6.3** | Implemented and tested; ready for critic output |
| **5.2** | Plain-language report template with sources | **6.5** | Implemented and tested as Markdown |
| 6.1 | Small ground-truth evaluation | 5.1 | Next; needs real agent output and medical review |
| 7.1 | Simple demo screen or command-line view | 6.1, then 6.4 | Command-line review scaffold ready; orchestrator connection pending |
| 7.3 | Demo script and backup recording | Part of 7.3 | Pending |
| 7.4 | Slides, including risks and responsible use | 4.4 and part of 7.3 | Pending |

In particular, **5.1 in the longer guide means evaluation**, whereas **5.1 in
the three-hour plan means sign-off**. The task numbers in the new Python modules
refer to the three-hour plan.

The active application is `genegap/`. Its deterministic pipeline already covers
tasks 2.1–2.7. The `agents/` and `safety/` folders currently contain descriptions,
so actual agent/critic integration is still pending. The separate root
`data/SCHEMAS.md` references absent `contracts.py` and `HelixTools`; these first
steps use the existing `genegap.schemas` models.

## Preview the report now

From `genegap/`:

```sh
.venv/bin/python scripts/review_report.py
```

This prints an empty, explicitly labelled draft using
`data/report_draft.example.json`. It contains no invented findings. The script
uses the existing SQLite store; `--db /tmp/genegap-review.sqlite` selects another
database.

## Handoff from the other programmers

Programmer 1 supplies a JSON document with `run_id`, `title`, `flags` and
`limitations`. `flags` is a list of existing `Flag.model_dump(mode="json")`
records. The same SQLite database must contain their supporting `Fact` records.
Programmer 2's critic sets `status="critic_passed"` only after its checks succeed.
The report code does not run or simulate that critic.

```python
import json
from pathlib import Path

payload = {
    "run_id": run_id,
    "title": "GeneGap research findings",
    "flags": [flag.model_dump(mode="json") for flag in flags],
    "limitations": limitations,
}
Path("data/report_draft.json").write_text(json.dumps(payload), encoding="utf-8")
```

First render a draft, then have the actual reviewer make decisions:

```sh
.venv/bin/python scripts/review_report.py data/report_draft.json --output reports/draft.md
.venv/bin/python scripts/review_report.py data/report_draft.json --review --reviewer "Your name" --final --output reports/final.md
```

Approval requires critic clearance, an evidence tier, citations and stored source
facts. This gate checks that those fields are present; the critic and human must
check whether the sources support the claim. The reviewer can reject a draft or
annotate it for further work. An annotation or skipped finding keeps final export
blocked until an explicit approval or rejection is recorded.

Decisions persist in the existing `steps` table with the reviewer, UTC time,
notes and reviewed content. Reopening the same run restores matching decisions.
Changed findings or stored source records need review again. Final reports contain approved findings and
a separate record of rejections. Empty templates cannot become final reports.
Reviewer names are supplied locally; this command does not authenticate people.
Recorded approvals cover each finding and its supporting source records. The
report title and additional context notes remain the report author's text and
are labelled separately from that approval scope.

## Validate

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/m1_clopidogrel.py --offline
```

Keep the current limitations visible: target-population shares and mapping
weights include placeholders; offline recorded data covers the clopidogrel path.
Evaluation results, medical sign-off and a working critic must come from actual
runs before they are claimed in a demo.
