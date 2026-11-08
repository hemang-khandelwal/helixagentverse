"""
GeneGap: Multi-agent system for finding genetic coverage gaps in clinical trials.
Compares trial participant demographics with ancestry-specific variant frequencies
to identify under-represented populations for pharmacogenomically relevant alleles.
"""

from . import config, schemas, store

__all__ = ["config", "schemas", "store"]
