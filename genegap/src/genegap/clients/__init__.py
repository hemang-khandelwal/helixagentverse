"""Clients package: wrappers for external biological APIs with caching, rate-limiting, and provenance."""
from .base import ApiClient, ApiError, CacheMiss
from .clinvar import ClinVarClient
from .ctgov import CTGovClient
from .gnomad import GnomadClient, merge_populations
from .reference import ChemblClient, EuropePmcClient, OpenTargetsClient, UniProtClient

__all__ = [
    "ApiClient",
    "ApiError",
    "CacheMiss",
    "ClinVarClient",
    "CTGovClient",
    "GnomadClient",
    "merge_populations",
    "OpenTargetsClient",
    "ChemblClient",
    "UniProtClient",
    "EuropePmcClient",
]
