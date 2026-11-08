# GeneGap mini evaluation

_Generated 2026-11-08 12:15 UTC. Arm A model: `gemini-4-argon`._

## Headline

**Asked without tools, the model was wrong or invented a value for 0 of 10 groups; our pipeline's 10 values are each traced to a cited record and replayed through the arithmetic.**

Coverage: the no-tools baseline produced a usable number for 3 of 10 groups and declined or failed on 7; our pipeline produced 10 of 10, each traced to a cited record.

## What was measured

10 items: the CYP2C19 variant rs4244285 (`10-94781859-G-A`) across the 10 gnomAD v4 genetic ancestry groups.

- **Arm A** - the model alone, one call per item, no tools and no retrieval.
- **Arm B** - our pipeline, replayed offline from the committed gnomAD, ClinVar and ClinicalTrials.gov payloads in `smoke/responses/`.

Arm B is ground truth. Its figures are not a model's opinion: they are recorded allele counts put through the tested stats functions, so what Arm B is measured on here is whether the verification layers run clean on good input.

## Arm A: the model answering from memory

| Category | Meaning | Count |
|---|---|---|
| EXACT | within 2 percentage points | 1 |
| CLOSE | within 5 percentage points | 2 |
| WRONG | outside 5 percentage points | 0 |
| REFUSED | declined or said it could not know | 7 |
| FABRICATED | confident number where no real value exists, or outside [0,1] | 0 |
| ERROR | call never succeeded after retries | 0 |

Mean absolute error over the 3 numeric answers that could be scored: **1.6 percentage points**.

## Arm B: our pipeline, and what the verification layers said

- Critic tier: **TIER A** (1)
- Language check passed on 10 of 10 generated finding sentences
- No critic check warned or failed
- Mapping version: `A`; 13 provenance facts cited

### Per-group result (Arm B)

| Group | AC | AN | Carrier frequency | Language |
|---|---|---|---|---|
| afr | 11987 | 67188 | 32.50% | PASS |
| ami | 166 | 910 | insufficient data | PASS |
| amr | 4023 | 34880 | 21.74% | PASS |
| asj | 3323 | 25098 | 24.73% | PASS |
| eas | 11167 | 36782 | 51.50% | PASS |
| fin | 10733 | 61354 | 31.93% | PASS |
| mid | 611 | 5608 | 20.60% | PASS |
| nfe | 162522 | 1111304 | 27.11% | PASS |
| remaining | 9207 | 56126 | 30.12% | PASS |
| sas | 20947 | 65396 | 53.80% | PASS |

## Honest limits

- The evaluation is small: 10 items on one variant, the only one with committed fixtures. It is a sanity check, not proof.
- Arm A is scored against our own pipeline's output. That output is itself derived from recorded gnomAD payloads, not from an independent gold standard.
- Carrier frequency assumes Hardy-Weinberg equilibrium, which the model was told in the prompt, so a wrong answer is not a misreading of the question.
- A single call per item at temperature 0. No sampling over repeats.
- Arm B is correct by construction. Its purpose here is to show the critic and the language checker running clean, not to claim the pipeline validated itself.
