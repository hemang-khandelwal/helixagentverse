# GeneGap

**An AI-assisted tool for identifying potential genetic evidence gaps in clinical trials.**

GeneGap helps clinical-trial designers and pharmacogenomics researchers investigate whether trial evidence adequately covers genetic variation relevant to drug response. It produces a referenced report of potential gaps and limitations—or explicitly acknowledges insufficient evidence.

## The problem

Drug response can depend on genetic variants, but clinical trials often report demographic categories rather than participant genotypes. This makes it difficult to assess which genetically relevant groups are represented in the evidence.

GeneGap helps researchers identify where evidence may be limited and which questions merit further investigation. **Reported race or ethnicity is not equivalent to genetic ancestry, and neither establishes an individual's genotype.**

## How it works

**Input:** A drug name, such as `clopidogrel`.

1. **Retrieve evidence.** Tools gather drug–gene associations, variant information, population frequencies, and trial characteristics from supported sources.
2. **Check applicability.** Assess whether the available data and biological assumptions support a comparison. Flag or block invalid demographic-to-ancestry mappings.
3. **Calculate statistics.** A deterministic statistics engine estimates carrier frequencies, expected carrier counts, the probability of fewer than a specified number of carriers, and possible recruitment requirements where justified.
4. **Explain and review.** AI agents organise the investigation and explain the evidence. An independent critic checks claims against sources and calculated results.
5. **Report uncertainty.** Return a referenced report or an explicit insufficient-evidence warning, with human sign-off for consequential decisions.

Model-based estimates are not measurements of trial participants' genotypes.

## Scientific approach

Clopidogrel is activated partly by the **CYP2C19** enzyme. The no-function **CYP2C19*2** allele can reduce activation and weaken the antiplatelet response.

For a suitable autosomal biallelic variant, under Hardy–Weinberg equilibrium, the estimated fraction carrying at least one copy is:

```text
Carrier frequency = 1 − (1 − q)²

q = allele frequency
At q = 0.10, estimated carrier frequency = 0.19, or 19%.
```

**This is an illustrative calculation, not a measured GeneGap result.**

Biological constraints matter:

- **G6PD is X-linked:** the autosomal carrier formula does not apply directly.
- **HLA alleles and CYP2C19 star-allele haplotypes** require separate handling.
- **Genetic ancestry and reported race or ethnicity are distinct:** population comparisons require explicit, justified assumptions.
- **Expected carrier counts do not establish statistical power** to detect a treatment effect in a subgroup.

## Data sources

| Source | Intended role |
|---|---|
| [ClinicalTrials.gov](https://clinicaltrials.gov/) | Trial characteristics, enrolment, and reported demographics where available |
| [gnomAD](https://gnomad.broadinstitute.org/) | Population allele frequencies and supporting allele counts |
| [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) | Reported variant interpretations and supporting records |
| [CPIC](https://cpicpgx.org/) / [PharmVar](https://www.pharmvar.org/) | Pharmacogenetic guidance and allele definitions |
| Additional supported literature and drug-target sources | Context and corroborating evidence |

Retain source identifiers, provenance, verification status, and applicable licence or usage terms for each record. These sources provide different types of evidence and are not interchangeable.

## Agent workflow

| Role | Responsibility |
|---|---|
| Target agent | Retrieve relevant drug–gene associations |
| Variant agent | Retrieve variant definitions and frequency evidence |
| Trial agent | Retrieve trial characteristics and demographic reporting |
| Statistics explanation agent | Explain deterministic outputs and their assumptions |
| Independent critic | Check numerical claims, evidence support, and limitations |

**Mathematical calculations remain independent of the language model.** Agent review supports traceability; it does not replace expert scientific review.

## Evaluation plan

The team reports having prepared a **20-pair drug–gene evidence set** and a **28-trial labelling dataset**. Each record should retain verification and provenance.

Planned evaluation uses held-out, reviewed records and compares GeneGap with:

1. A language model without external tools.
2. A demographic-only parity baseline.

Metrics include:

- Trial-extraction accuracy.
- Precision and recall for drug–gene facts.
- Unsupported-number rate.
- Critic error-catch rate.

**Evaluation results are pending. No performance numbers are claimed.**

## Risks and safeguards

| Risk | Mitigation |
|---|---|
| Unreliable ancestry proxies | Flag or block invalid mappings; run sensitivity analyses where comparisons are justified |
| Hallucinations, source errors, or prompt injection | Retain provenance, constrain tool access, and use an independent critic |
| Clinical overinterpretation | Use public, aggregated inputs; provide no individual genotype or dosing recommendations; require human sign-off |

## Two-minute judge test

A researcher enters a new drug name. GeneGap should expose its research steps, show its sources and assumptions, and either produce a supported report or acknowledge that the information is insufficient.

Success means helping the researcher identify **which potential genetic evidence gaps merit further study**, rather than claiming to prove trial unfairness from demographic data alone.

**Next practical step:** Pilot the workflow with trial-design researchers.

## References

- [CPIC guideline for CYP2C19 genotype and clopidogrel therapy: 2022 update](https://pmc.ncbi.nlm.nih.gov/articles/PMC9287492/)
- [gnomAD](https://gnomad.broadinstitute.org/)
- [ClinicalTrials.gov](https://clinicaltrials.gov/)
- *GeneGap Build Plan 1* — project planning reference; link to be added.
