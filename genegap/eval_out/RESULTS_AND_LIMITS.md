# GeneGap: results and limits

Generated from the committed artifacts by `report_results.py`. Every figure below is read or computed from an artifact; none is typed by hand.

Demo pair: **clopidogrel / CYP2C19**, variant rs4244285 (`10-94781859-G-A`), mapping version `A`.

## 1. What we found

Carrier frequency of rs4244285 varies widely across gnomAD genetic ancestry groups. The two most divergent assessable groups are **South Asian (`sas`)** at 53.80% and **Middle Eastern (`mid`)** at 20.6%, a ratio of **2.61x** between them.

Across the 10 groups, 9 are assessable and 1 (`ami`) falls below the allele-number floor and is reported as insufficient data rather than given a frequency.

Pooled trial enrolment mapped to ancestry groups totals 14,573 participants. Per group:

| Group | Mapped participants | Carrier frequency | Expected carriers | Participants needed |
|---|---|---|---|---|
| `afr` | 2,329 | 32.50% | 756.9 | 93 |
| `ami` | 146 | not assessable | - | - |
| `amr` | 0 | 21.74% | 0.0 | 139 |
| `asj` | 0 | 24.73% | 0.0 | 122 |
| `eas` | 24 | 51.50% | 12.4 | 59 |
| `fin` | 0 | 31.93% | 0.0 | 94 |
| `mid` | 0 | 20.6% | 0.0 | 146 |
| `nfe` | 11,935 | 27.1101% | 3,235.6 | 111 |
| `remaining` | 123 | 30.12% | 37.0 | 100 |
| `sas` | 16 | 53.80% | 8.6 | 56 |

The critic returned **TIER A**. 5 of 5 checks passed (C1, C2, C3, C4, C5), over 13 cited provenance facts.

## 2. How we know it isn't invented

A baseline arm asked `gemini-4-argon` the same 10 questions with no tools and no retrieval. Of 10 groups, it produced a usable value for **3**, declined **7**, failed outright on **0**, and fabricated **0**.

**The baseline fabricated nothing.** Where it answered it was approximately correct: 1 within the exact band, 2 within the close band, 0 outside both, with a mean absolute error of 1.6 percentage points over the 3 scorable answers. This is the honest result and it is not the result we expected: this model declines rather than inventing. The argument for retrieval here is coverage, not hallucination.

Our pipeline produced **10 of 10** values. Each is traced to a cited record and replayed through the arithmetic by the critic's independent recomputation, and the language checker passed on 10 of 10 generated finding sentences.

One agreement is worth naming: the baseline declined on `ami` (Amish), which is the same group our pipeline independently flags as insufficient data from the allele number. Two unrelated routes reached the same conclusion that this value should not be stated.

## 3. What we cannot claim

**1. One drug-gene pair.** Everything above is clopidogrel / CYP2C19 at a single variant, rs4244285. Nothing here demonstrates that the method generalises to any other drug, gene or variant.

**2. Mapping A only.** The result above was computed under mapping `A` (8 weighted rows across 8 trial categories, 0 of them placeholders). The alternatives: `B` has 18 weighted rows, 18 of them placeholders; `C` has 20 weighted rows, 20 of them placeholders.

> Every weighted row in `B`, `C` is a placeholder carrying no citation. `robust_across_mappings` exists and runs, but it has **never been exercised against a populated alternative mapping**. Running it over `B`, `C` compares mapping `A` against invented weights, which tests the plumbing and not the robustness of the finding. **Robustness across mapping choices is NOT demonstrated.** Any statement that a finding 'survives all mappings' would be false as the configuration stands.

**3. Race is not ancestry.** Trial-reported race is self-identified and administrative; gnomAD groups are genetic ancestry inferred from genotype. The mapping between them is a documented assumption, not a fact, and every ratio above is sensitive to it.

**4. Hardy-Weinberg.** Carrier frequency is computed as 1 - (1 - p)^2, which assumes Hardy-Weinberg equilibrium in each group. Population structure, inbreeding and selection all violate it to some degree.

**5. No HLA, no CYP2D6.** The method does not transfer to these. They are haplotypes and copy-number variants; gnomAD allele frequencies do not represent star alleles, and nothing in this system handles them. Asked about them, the pipeline reports that it cannot assess rather than guessing.

**6. Invisible trials.** Trials without posted results have no baseline demographic table, so they are invisible to us. Which trials we can assess at all is therefore itself a biased sample of trials.

**7. No temporal holdout.** We have not shown the system would have found this before the finding was public. The CYP2C19 / clopidogrel association is long established and was in the model's training data and in our own curated files.

**8. One model, one run, 10 groups.** The evaluation is a single model (`gemini-4-argon`) at one temperature, one call per item, over 10 ancestry groups of one variant. No repeats, no sampling, no second model. It is a demonstration, not a benchmark.

**9. No clinical claim.** The system makes no clinical claim and must not be used to inform prescribing. Its output is a flag for a human expert to review.

## 4. Wiring status

- `criticise()` is **not called from the report path**. It is implemented and tested, but no report, demo, agent or script invokes it.
- `check_language()` is **not called from the report path**. It is implemented and tested, but no report, demo, agent or script invokes it.

**2 of 2 verification layers are not wired into the report path.** The figures in section 1 were produced by the evaluation harness calling them directly, not by the demo. Until a report path calls them, the guarantee in section 2 holds for this document only, not for anything the demo prints.
