# Helix — pharmacogenomic evidence screen

Agentverse 2026. Given a small-molecule drug, investigate evidence for candidate
gene/allele/adverse-effect associations and display their provenance and checks.
Outputs are hypotheses for clinical review, not patient exclusion decisions.

## Current milestone: A1

The shared contracts, data-file schemas, and importable placeholder modules are
in place. Retrieval, model calls, verification, evaluation, and the interface are
**not implemented yet**. Placeholder functions raise `NotImplementedError`;
they never return fabricated findings or pretend that a check passed.

The old retrospective `contracts.py` has been replaced with the prospective
contracts for the task-split plan.

## Check the foundation

Use Python 3.9 or newer. A1 uses only the standard library: no API key or package
installation is required.

From the project folder:

```sh
python3 -m unittest discover -s tests -v
```

This checks module imports, schema consistency, date serialization, frequency
bounds, and the contract's check policy. It does not test an implemented medical
analysis or an end-to-end agent.

## Ownership and next tasks

| Owner | Files | Next work |
| --- | --- | --- |
| A | `contracts.py`, `tools.py`, `graph.py`, graph/candidate fixtures | A2: complete example graph and candidate rows. Then A3-A8. |
| B | `verifier.py`, `eval.py`, evaluation/data contents, planted test cases | Use the declared data formats for B5; implement B1/C3 against A2 fixtures. |
| C | `agent.py`, `orchestrator.py`, `app.py` | Start C2 against the tool interfaces; C1 renders A2 fixtures when available. |
| M1 | Clinical evidence/review protocol | Review drug/route/association evidence and define the review protocol. |
| M2 | Severity-table contents and independent medical review | Supply an initial severity table and review evaluation cases. |

Coordinate changes to `contracts.py` with all three programmers. Do not change
someone else's interface independently. A1 alone does not provide A2 fixtures.

## Shared connections

1. `HelixTools` returns typed records from the declared data files/retrieval.
2. Graph operations build an `EvidenceGraph`; edges identify their citations.
3. The agent returns an `ExpansionDecision`; the orchestrator enforces the budget.
4. Candidates reference graph edges, preserving their supporting evidence chain.
5. The verifier returns a `CheckReport` and `Verdict` for each candidate.
6. The UI and evaluation read the same `RunResult`, including rejected candidates.

See `data/SCHEMAS.md` and `data/schemas.json` before creating any dataset files.
Do not create empty dataset files and present them as collected evidence.

## Policy choices from the latest task split

- Eight checks: C1 temporal integrity, C2 provenance, C3 allele specificity,
  C4 topicality, C5 analogy, C6 carrier burden, C7 severity, C8 actionability.
- C1-C4 adjudicate; C5-C8 annotate. This follows B1/B2 in the newer task-split
  document, rather than the earlier proposal's three adjudicators and C9/GTEx.
  Missing or uncertain evidence should not be treated as a proved contradiction.
- Tier A: evidence for this drug; Tier B: related-drug evidence with an explicit
  shared mechanism; Tier C: gene-level inference with a null allele. A Tier B
  claim must satisfy the analogy requirement; a warning alone is not a pass.
- Unverified candidate objects may contain mistakes so the verifier can reject
  them. Construction is not approval. Do not equate an empty check report with
  successful verification.
- Historical retrieval arms require an explicit exact-date cutoff. Bind it in
  `HelixTools`; do not give the model a per-call override. First-public dates,
  not publication years or retrieval dates, determine eligibility.
- B_full runs first; A_plus receives that exact final evidence graph. Baseline
  has no retrieval. Every arm can abstain. Today's model may still know later
  facts; a date filter does not erase its training knowledge.
- Python enforces 12 expansions, at most 2 analog hops, and 120 seconds by default.
  Reaching a limit is visible as `BUDGET_EXHAUSTED`.
- Keep allele frequencies distinct from carrier frequencies. No confidence
  scalar is supplied. Population frequency is not a probability of harm.
- Per-candidate human acceptance/rejection belongs on `CandidateResult`.
  C4 must implement the sign-off gate before export.

## Scope boundaries

The input is a drug, not an entire trial protocol. The programme is intended to
generalise across supported small-molecule drugs; one initial example tests the
workflow rather than training a model. Broad coverage needs more sources and
evaluation. Insufficient evidence is a valid outcome.

A1 makes no external calls and collects no patient data. Later source integrations
must preserve provenance and verify source usage/redistribution terms.
