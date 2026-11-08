# Licence and Citation Register (Task 0.4)

Data licenses, access terms, and redistribution policies for all external databases, registries, and literature sources queried by GeneGap.

## 1. Data Sources and Licenses

| Source | Category | URL | Licence / Terms | Access Date | Notes |
|---|---|---|---|---|---|
| **gnomAD** | Population Genomics | https://gnomad.broadinstitute.org | Open Access (Broad Institute Terms of Use) | 2026-11-08 | Pinned dataset version: `gnomad_r4`. Allele counts (AC) and numbers (AN) recorded with full provenance. |
| **ClinVar** | Variant Clinical Significance | https://www.ncbi.nlm.nih.gov/clinvar/ | Public Domain (NLM / NIH Data Policy) | 2026-11-08 | Free for research and commercial use. Queried via NCBI Entrez E-utilities with rate-limiting. |
| **ClinicalTrials.gov** | Clinical Study Registry | https://clinicaltrials.gov/data-api/about-api | Public Domain (U.S. Government Work) | 2026-11-08 | API v2 queried for completed trials with posted tabular results. |
| **Open Targets Platform** | Drug-Target Associations | https://platform.opentargets.org | Creative Commons CC0 1.0 (Public Domain Dedication) | 2026-11-08 | Unrestricted global access to drug targets and mechanisms of action via GraphQL. |
| **ChEMBL** | Bioactive Molecules | https://www.ebi.ac.uk/chembl/ | Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0) | 2026-11-08 | Used to resolve canonical drug names to ChEMBL molecule identifiers. |
| **UniProtKB / Swiss-Prot** | Protein Sequence & Function | https://www.uniprot.org | Creative Commons Attribution 4.0 (CC BY 4.0) | 2026-11-08 | Used to map HGNC gene symbols to human protein accessions and functional active sites. |
| **Europe PMC** | Scientific Literature | https://europepmc.org | Open Access / CC licenses (varies by publisher) | 2026-11-08 | Abstract text and citation metadata used for evidence synthesis. Quotations restricted to short snippets (<15 words). |
| **PubMed / Entrez** | Biomedical Citations | https://pubmed.ncbi.nlm.nih.gov | Public Domain (NLM Data Policy) | 2026-11-08 | Literature lookup via NCBI E-utilities. |
| **AlphaFold DB** | Protein 3D Structures | https://alphafold.ebi.ac.uk | Creative Commons Attribution 4.0 (CC BY 4.0) | 2026-11-08 | Structural confidence metrics (pLDDT) used to evaluate variant binding sites. |
| **PharmGKB / CPIC** | Pharmacogenomic Guidelines | https://www.pharmgkb.org | Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0) | 2026-11-08 | Clinical Pharmacogenetics Implementation Consortium (CPIC) Level A/B guidelines used for ground-truth curation. |
| **UK ONS Census 2021** | Population Demographics | https://www.ons.gov.uk | Open Government Licence v3.0 (OGL) | 2026-11-08 | Secondary baseline for UK population demographic distributions. |
| **US Census Bureau** | Population Demographics | https://www.census.gov | Public Domain (U.S. Government Data) | 2026-11-08 | Secondary baseline for U.S. population demographic distributions. |

## 2. Usage Policies

1. **No Individual Patient Data:** All data processed by GeneGap consists of public, aggregate population frequencies (gnomAD) and published summary trial characteristics (ClinicalTrials.gov). No individual patient health records or raw sequencing files are ingested or stored.
2. **Provenance Tracking:** Every statistic in UI tables or report exports carries a persistent `Fact` record specifying the upstream source, record ID, and URL.
3. **Quotation Limits:** Text extracts from published journal abstracts are kept under 15 words per claim, accompanied by PubMed/DOI citations.
