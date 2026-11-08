# Helix / GeneGap · Pharmacogenomic Evidence & Trial Coverage System

**Agentverse 2026 · Biotech Track**

GeneGap is a multi-agent system designed to evaluate whether clinical trial populations adequately represent the pharmacogenomic variants that affect drug response in real-world patients.

---

## Quick Start

The active codebase is located in the `genegap/` directory:

```sh
cd genegap
pip install -r requirements.txt
pytest
```

For full system architecture, documentation, and data guides, see [genegap/README.md](genegap/README.md).

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
