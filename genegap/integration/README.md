# Task 6.4 — connect the orchestrator to review

This is **6.4 in the longer programming guide**. All connector files are isolated
under `genegap/integration/`; existing agent code, shared schemas, calculations,
requirements and READMEs are unchanged.

The task 3.11 orchestrator is implemented in `src/genegap/agents/orchestrator.py`.
The default uses Gemini planning and live API clients. `--offline` explicitly
selects curated planning and recorded API responses; it does not call a model.

```sh
.venv/bin/python integration/app.py "clopidogrel" --offline
```

Findings remain drafts while clinical evidence grading and independent critic
clearance are pending. Configure `GEMINI_API_KEY` and `GEMINI_MODEL` in the existing
environment for live planning, then omit `--offline`.

## Check the connection

From `genegap/`:

```sh
.venv/bin/python integration/app.py --check
```

This checks the default orchestrator signature without starting a run. Missing
explicitly selected entry points exit with status 2 and create no review queue.

Once Programmer 1 provides an entry point, select it explicitly:

```sh
.venv/bin/python integration/app.py --orchestrator your_module:run_query --check
.venv/bin/python integration/app.py "clopidogrel" --orchestrator your_module:run_query --reviewer "Your name"
```

Replace `your_module:run_query` with the actual module and callable. The module
must be importable from the project environment. `--check` verifies its calling
convention without invoking it; it is not a live scientific validation.
`GENEGAP_ORCHESTRATOR` can supply the same explicit entry point instead of the
command-line option. No model, API key or new dependency is configured here.

## Callable contract for Programmer 1

The connector calls the supplied function once:

```python
def run_query(query, *, store, run_id, on_event):
    # Run the real orchestrator with query and the supplied Store/run_id.
    # Persist its supporting Facts in store as it runs.
    # Emit optional progress: on_event("tool_return", {"stage": "variant"}).
    # Return the existing handoff shape:
    return {
        "run_id": run_id,
        "title": title,
        "flags": [flag.model_dump(mode="json") for flag in actual_flags],
        "limitations": actual_limitations,
    }
```

The comments and undefined result variables above describe the interface; they
are not a replacement implementation. A returned `ReviewDraft` is also accepted.
Async callables are awaited by the terminal launcher. An adapter to another
orchestrator API can wrap that API in this function without changing its internals.

`on_event(kind, payload)` accepts a nonblank string and a JSON-compatible object.
It runs synchronously in the same execution thread as the supplied Store. The
UI displays progress and records it in `events.jsonl` and the existing step log.
If the producer has an open database transaction, progress remains visible while
step-log persistence waits for that transaction to finish. Emitting progress never
commits the producer's unfinished writes. On failure those writes are rolled back
before queued audit events are saved.
These events cannot grant critic clearance or human approval. Do not include
credentials in progress payloads.

The result must retain the fresh supplied run ID. Referenced Facts must exist in
the supplied Store. Draft flags can remain incomplete for rejection or annotation;
critic-cleared flags require an evidence tier, citations and source Facts. Their
upstream status is preserved. The connector does not perform the critic's work or
allow the orchestrator to submit human approval.

## Query-to-review flow

Each invocation creates a new directory under `integration/runs/` containing:

- `run.json`: query, producer, run ID and running/ready/failed/interrupted state.
- `events.jsonl`: recorded progress.
- `run.sqlite`: the shared producer/reviewer Store, unless `--db PATH` selects
  an existing API/Fact cache.
- `draft.json`: published only after the call and handoff validation succeed.

`--output-dir PATH` selects another run root. `--list` displays the resulting queue
and exits; otherwise the existing task 6.3 dashboard opens for inspection and
explicit human decisions. Its final-export gate remains in force. To reopen a
completed run later:

```sh
.venv/bin/python review/app.py integration/runs/RUN_ID/draft.json --db integration/runs/RUN_ID/run.sqlite
```

Use the database named in `run.json` when a shared cache was selected. Failed runs
keep their trace and failure type but never open a previous run's results. Detailed
exceptions remain available to Python callers through `RunFailed.cause`.
Failures and cancellations append a final `failed` or `interrupted` event even
when the progress observer itself failed. Async cancellation is recorded as
interrupted and then propagates to the caller.

## Validate

```sh
.venv/bin/python -m pytest integration/tests -q
.venv/bin/python -m pytest tests charts/tests review/tests integration/tests -q
```

These are callable-contract, handoff and review-boundary tests. The recorded
pipeline can now run end to end; live Gemini/API validation still requires model
credentials and access.
