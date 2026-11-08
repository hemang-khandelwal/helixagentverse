# Orchestrator — task 3.11

The actual orchestrator is now implemented and connected to the task 6.4 launcher.
From `genegap/`, run the recorded demo without API keys or network access:

```sh
.venv/bin/python integration/app.py "clopidogrel" --offline
```

Add `--list` to show findings and exit. The run saves a handoff, supporting Facts,
calculated gap tables and trace in `integration/runs/`. It then opens the existing
human-review dashboard. Findings remain drafts until the independent evidence
and critic stages establish clearance; this code never fabricates approval.

For live Gemini planning and API retrieval, configure the existing `.env` values
`GEMINI_API_KEY` and an accessible `GEMINI_MODEL`, then omit `--offline`:

```sh
.venv/bin/python integration/app.py "clopidogrel"
```

The planner uses the Google SDK's [Pydantic structured-output interface](https://googleapis.github.io/python-genai/).
It selects only curated drug/gene pairs and population tables. Python performs
all statistics using the existing core; missing rules, data or a suitable target
population produce an explicit limitation rather than guessed results. Live
planning errors do not silently switch to recorded data.

The offline entry point uses curated name matching rather than Gemini. Recorded
responses currently cover clopidogrel/CYP2C19; mappings and population shares still
contain documented assumptions. Literature search hits are candidate citations,
not evidence grading. Other drugs need their own valid target-population table.

The entry points `run_query` and `run_offline_query` accept the existing connector
contract: query, Store, run_id and progress callback. Planning and analysis are
separate modules; execution is bounded by a fixed progress-step limit.
