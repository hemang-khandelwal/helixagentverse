"""
Multi-agent layer for GeneGap:
- Orchestrator (Gemini 4 Argon): handles user input, entity resolution, planning, and tool routing.
- Target Agent (Open Targets, ChEMBL, UniProt): resolves drugs to targets and metabolizing enzymes.
- Variant Agent (gnomAD, ClinVar): pulls variant frequencies across ancestry groups.
- Trial Agent (Gemini 3.8 Flash): searches ClinicalTrials.gov and parses baseline demographics.
- Epidemiology Agent: extracts real-world disease incidence and target population proportions.
- Gap Analyst: pure Python statistical engine computing carrier frequencies and representation ratios.
- Evidence Agent (Europe PMC): verifies clinical literature and assigns evidence tiers (1 to 5).
- Critic (Gemini 4 Argon, isolated context): validates numbers against the provenance store and checks language rules.
"""

__all__ = []
