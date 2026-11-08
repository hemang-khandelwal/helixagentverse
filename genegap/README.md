# GeneGap

A multi-agent system for identifying genetic coverage gaps in clinical trials.

GeneGap evaluates whether clinical trial enrollment adequately represents the genetic variation that influences drug response across diverse global populations.

---

## The Problem

Clinical trials frequently fail to enroll populations that carry the specific genetic variants that affect how a drug works.
- For example, clopidogrel (Plavix) requires activation by the liver enzyme **CYP2C19**. Loss-of-function variants (such as CYP2C19*2 and *3) prevent drug activation, increasing cardiovascular risk.
- These loss-of-function variants occur in ~30% of East Asian populations and ~15% of European populations.
- When trials enroll predominantly European cohorts, the efficacy and safety data collected may not adequately represent populations with higher variant carrier rates.

GeneGap systematically identifies these gaps by comparing trial enrollment demographics against global ancestry-stratified genomic databases.

---

## System Architecture

```
User Input (Drug / Condition / Gene)
                  │
                  ▼
          ┌───────────────┐
          │ Orchestrator  │  (Gemini 4 Argon: planning, entity resolution, routing)
          └───────┬───────┘
                  │
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌────────────────┐
│ Target Agent │ │ Variant Agent│ │  Trial Agent   │
│ (OpenTargets,│ │ (gnomAD,     │ │ (CT.gov API v2,│
│  ChEMBL,     │ │  ClinVar)    │ │  demographics) │
│  UniProt)    │ └──────┬───────┘ └───────┬────────┘
└──────────────┘        │                 │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │    Gap Analyst (Math)    │  (Pure Python: Hardy-Weinberg,
                    │  - carrier frequency     │   Beta Monte Carlo,
                    │  - representation ratio  │   representation ratio)
                    │  - required enrollment   │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │      Evidence Agent      │  (Europe PMC / PubMed:
                    │  - literature tiers 1-5  │   clinical citations)
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │       Critic Agent       │  (Gemini 4 Argon: isolated context;
                    │  - audit against Facts   │   checks numbers against provenance,
                    │  - language rules        │   flags unbacked claims)
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │     Human Sign-off       │  (Expert review before export)
                    └──────────────────────────┘
```

### Key Design Principles

1. **No Hallucinated Numbers:** The LLM never calculates statistics. Pure Python functions compute carrier frequencies, confidence intervals, and representation ratios. The LLM's role is planning, extracting unstructured text, and explaining results.
2. **Every Datapoint Has Provenance:** Every reported number travels inside a `Fact` record with source repository, record ID, direct URL, retrieval timestamp, and extraction method.
3. **Adversarial Critic in an Isolated Context:** The Critic agent runs in a separate context window so it cannot simply echo earlier agent outputs. It audits every number against the stored `Fact` records.
4. **Distinction Between Self-Identified Race and Genetic Ancestry:** Clinical trials report self-identified race/ethnicity, whereas gnomAD reports statistical genetic ancestry groups. GeneGap tests findings under multiple mapping assumptions (conservative, admixture-aware, stress-test); a finding is only robust if it holds across all mappings.
5. **Human in the Loop:** Automated outputs are hypotheses for researchers, not clinical prescriptions. Reports require human sign-off before final export.

---

## Repository Layout

```
genegap/
├── config/
│   ├── demo_scope.csv          # Curated demo drugs, target genes, and rationale (CPIC level A/B)
│   └── ancestry_mapping_A.csv  # Trial race/ethnicity to gnomAD ancestry mapping rules
├── data/
│   ├── ground_truth.csv        # Evaluation dataset: positive pairs, negative controls, limit tests
│   └── genegap.sqlite          # SQLite store (HTTP cache, Fact table, agent step logs)
├── smoke/
│   ├── smoke_apis.py           # Verification script for all 7 external biological APIs
│   └── smoke_gemini.py         # Verification script for Gemini API models (Argon and Flash)
├── src/genegap/
│   ├── schemas.py              # Pydantic data models (Fact, Variant, Trial, GapResult, Flag)
│   ├── store.py                # SQLite cache and provenance persistence
│   ├── config.py               # Settings, model selection, environment loader
│   ├── clients/
│   │   ├── base.py             # ApiClient base class (throttling, retries, caching, Fact creation)
│   │   ├── gnomad.py           # gnomAD GraphQL API client (population frequencies)
│   │   ├── ctgov.py            # ClinicalTrials.gov API v2 client (trial discovery and raw studies)
│   │   ├── clinvar.py          # ClinVar NCBI Entrez client (clinical classifications)
│   │   └── reference.py        # Open Targets, ChEMBL, UniProt, and Europe PMC clients
│   ├── agents/                 # Agent layer (Orchestrator, Target, Variant, Trial, Critic)
│   └── safety/                 # Prompt-injection defense, language rules, refusal handling
├── tests/
│   └── test_foundations.py     # Unit test suite verifying schemas, caching, and parsers
├── LICENCES.md                 # License and citation register for all data sources
├── pytest.ini                  # Pytest configuration
└── requirements.txt            # Python dependencies
```

---

## Getting Started

### 1. Requirements

- Python 3.9+
- Install dependencies:
  ```sh
  pip install -r requirements.txt
  ```

### 2. Configure Environment

Create a `.env` file in the project root:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-4-argon
GEMINI_FAST_MODEL=gemini-3.8-flash
NCBI_API_KEY=optional_ncbi_key
CONTACT_EMAIL=your_email@example.com
```

### 3. Run the Verification Tests

**Run API Connectivity Smoke Test:**
```sh
python smoke/smoke_apis.py
```
This queries gnomAD, ClinicalTrials.gov, ClinVar, Open Targets, ChEMBL, UniProt, and Europe PMC, saving sample JSON responses to `data/smoke/`.

**Run Gemini Model Smoke Test:**
```sh
python smoke/smoke_gemini.py
```
This tests both plain text and structured Pydantic JSON output modes on Gemini 4 Argon and Gemini 3.8 Flash.

**Run Foundation Unit Tests:**
```sh
pytest
```
The test suite validates data schemas, deterministic fact hashing, biological constraints ($AC \le AN$), HTTP caching, retry backoff, and offline mode.
