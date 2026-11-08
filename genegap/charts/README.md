# Task 6.2 — charts

This is **6.2 in GeneGap_1_Programming_Guide.pdf**: a representation-ratio plot
and a required-enrolment chart, rendered from the existing `GapResult` schema.
It is Programmer 3's chart work, separate from the differently numbered tasks
in the three-hour plan.

All files for this task live in `genegap/charts/`. No shared schema, core module,
main requirements file, existing README or evaluation file is changed.

## Render the recorded demo

From `genegap/`:

```sh
.venv/bin/python -m pip install -r charts/requirements.txt
.venv/bin/python charts/render.py --offline
```

Output goes to `charts/output/`:

- `representation_ratio.png` and `.svg`
- `required_enrolment.png` and `.svg`
- `chart_input.json`: the exact result, chart context, collection notes and
  available source Facts used for this render

SVG is suitable for slides or resizing; PNG is a convenient preview. The
renderer runs without a graphical desktop and uses no network for the recorded
demo. Its temporary in-memory database leaves existing cache and human-review
records untouched.

`--mapping B` or `--mapping C` selects a different mapping for the demo. This
renders that mapping only; it does not implement another programmer's robustness
evaluation. `--target-carriers 30` explicitly sets the expected-carrier target
used by the existing core calculation. `--output-dir PATH` saves another render.

## Render existing results

```sh
.venv/bin/python charts/render.py --input path/to/gap_result.json
```

A bare `GapResult.model_dump_json()` works directly. An optional envelope adds
context that the shared schema does not hold:

```python
from charts.render import ChartInput

payload = ChartInput(
    result=gap_run.result,
    notes=gap_run.notes,
    target_label=gap_run.target_label,
    target_carriers=target_carriers_used_by_the_core,
)
```

Save `payload.model_dump_json()` to a file and pass it with `--input`. When
rendering a bare result, the chart says that the carrier target was not supplied;
it does not assume a threshold. Imported numbers are displayed as supplied.

The two functions in `plots.py` also return Matplotlib `Figure` objects for a
future UI to display without changing the core pipeline.

## Chart meaning

The representation plot uses **carrier ratio**: a group's share of expected
trial carriers divided by its share of expected target-population carriers.
The reference line at 1 marks parity. It does not substitute the separate
headcount-based enrolment ratio. The schema's carrier-frequency intervals are
not ratio intervals, so no ratio error bars are fabricated.

The enrolment chart uses **total required participants per group** for the target
number of *expected* carriers. It is not extra recruitment, a guarantee of carrier
counts, or a statistical power calculation. Both charts keep missing groups
visible as N/A; genuine zeros remain zero. Low-data and unsupported inheritance
results are not presented as valid genetic estimates.

Notes carried from the core remain visible, including assumption-based target
shares, placeholder mapping weights and missing demographics. The recorded demo
is marked as awaiting expert review.

## Test this isolated task

```sh
.venv/bin/python -m pytest charts/tests -q
```

To include the existing core tests too, run:

```sh
.venv/bin/python -m pytest tests charts/tests -q
```
