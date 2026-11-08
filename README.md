# Helix / GeneGap · Pharmacogenomic Evidence & Trial Coverage System

**Agentverse 2026 · Biotech Track**

GeneGap is a multi-agent system designed to evaluate whether clinical trial populations adequately represent the pharmacogenomic variants that affect drug response in real-world patients.

---

## Quick Start

Start the integrated local app from this folder:

```sh
./run.sh
```

Open **http://127.0.0.1:8000**. Click **Clopidogrel** to run the recorded demo.
The app includes saved analyses, source inspection, A/B/C charts, reviewer-supplied
evidence grading, numerical checks, approve/reject/annotate, and report downloads.
Recorded mode requires no API key. Live mode requires the Gemini settings in
`genegap/.env`. Use `./run.sh --port 8001` if port 8000 is busy.

Results are research hypotheses; the supplied target-population and some mapping
weights remain provisional. Numerical checks do not independently verify the
reviewer's clinical evidence assessment.

For command-line tools and details, see [genegap/README.md](genegap/README.md).

---

## Core Components

| Component | Path | Description |
|---|---|---|
| **Data Schemas** | `genegap/src/genegap/schemas.py` | Sourced provenance `Fact`, `Variant`, `AncestryFrequency`, `Trial`, `GapResult`, and `Flag` models. |
| **Persistence & Cache** | `genegap/src/genegap/store.py` | SQLite store managing HTTP cache, Fact provenance repository, and agent execution step logs. |
| **API Clients** | `genegap/src/genegap/clients/` | Resilient, rate-limited, and cached clients for gnomAD, ClinicalTrials.gov, ClinVar, Open Targets, ChEMBL, UniProt, and Europe PMC. |
| **Agent Layer** | `genegap/src/genegap/agents/` | Orchestrator (Gemini 4 Argon), Target, Variant, Trial (Gemini 3.8 Flash), Epidemiology, Gap Analyst (Math), Evidence, and Critic agents. |
| **Safety & Governance** | `genegap/src/genegap/safety/` | Prompt injection defenses, language rules (no biological essentialism), and clinical refusal handlers. |
| **Evaluation Data** | `genegap/data/ground_truth.csv` | 20 curated gene-drug pairs (positive controls, negative controls, and limit cases). |
| **Smoke Tests** | `genegap/smoke/` | Connectivity checks for Gemini models and all external bioinformatics APIs. |
