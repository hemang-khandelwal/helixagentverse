"""Offline mode for the demo: replay the recorded API responses in smoke/responses/.

The files in smoke/responses/ are real payloads captured from the live APIs during the
Phase 0 smoke tests. This module loads them into the ordinary HTTP cache under the exact
keys the clients will look up, so an offline run exercises the real client code paths and
the real recorded data, with no network and no stubbing.

Nothing here invents a value. Only responses actually recorded are seeded; anything else
raises CacheMiss offline, which the pipeline reports as a note rather than a crash.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .clients.clinvar import ClinVarClient
from .clients.ctgov import FIELDS, CTGovClient
from .clients.gnomad import Q_SEARCH, Q_VARIANT, GnomadClient
from .config import ROOT
from .store import Store

RESPONSES_DIR = ROOT / "smoke" / "responses"

# What the recorded payloads cover. The demo scope is deliberately narrow (task 7.1).
DEMO_RSID = "rs4244285"                       # CYP2C19*2
DEMO_VARIANT_ID = "10-94781859-G-A"
DEMO_DRUG = "clopidogrel"
DEMO_NCT_ID = "NCT01609842"


def _load(name: str, directory: Path = RESPONSES_DIR) -> Any:
    path = directory / name
    if not path.exists():
        raise FileNotFoundError(f"Recorded response {path} is missing; run smoke/smoke_apis.py online first.")
    return json.loads(path.read_text())


def _seed(client, method: str, url: str, params: Any, body: Any, payload: Any) -> None:
    """Write one recorded payload into the cache under the key this client would compute."""
    key = client._key(method, url, params, body)
    client.store.cache_put(key, url, 200, json.dumps(payload))


def seed_demo_cache(store: Optional[Store] = None, responses_dir: Path = RESPONSES_DIR) -> list[str]:
    """
    Seed the cache for the clopidogrel / CYP2C19 demo path.

    Returns a list of what was seeded, so the run can print exactly which records are
    being replayed rather than implying the whole pipeline ran live.
    """
    store = store or Store()
    load = lambda name: _load(name, responses_dir)
    seeded: list[str] = []

    # --- gnomAD ---
    gnomad = GnomadClient(store=store)
    _seed(
        gnomad, "POST", gnomad.url, None,
        {"query": Q_SEARCH, "variables": {"q": DEMO_RSID, "ds": gnomad.dataset}},
        load("gnomad_variant_search.json"),
    )
    seeded.append(f"gnomAD variant_search for {DEMO_RSID}")
    _seed(
        gnomad, "POST", gnomad.url, None,
        {"query": Q_VARIANT, "variables": {"id": DEMO_VARIANT_ID, "ds": gnomad.dataset}},
        load(f"gnomad_variant_{DEMO_VARIANT_ID}.json"),
    )
    seeded.append(f"gnomAD frequencies for {DEMO_VARIANT_ID}")

    # --- ClinVar (esearch then esummary over the returned UIDs) ---
    clinvar = ClinVarClient(store=store)
    cv = load("clinvar_esearch_esummary.json")
    esearch, esummary = cv["esearch"], cv["esummary"]
    _seed(
        clinvar, "GET", f"{clinvar.base}/esearch.fcgi",
        clinvar._p(db="clinvar", term=DEMO_RSID, retmax=5), None, esearch,
    )
    uids = esearch.get("esearchresult", {}).get("idlist", [])
    if uids:
        _seed(
            clinvar, "GET", f"{clinvar.base}/esummary.fcgi",
            clinvar._p(db="clinvar", id=",".join(uids)), None, esummary,
        )
    seeded.append(f"ClinVar records for {DEMO_RSID} ({len(uids)} UIDs)")

    # --- ClinicalTrials.gov ---
    ctgov = CTGovClient(store=store)
    search_params = {
        "query.intr": DEMO_DRUG,
        "filter.overallStatus": "COMPLETED",
        "aggFilters": "results:with",
        "pageSize": 50,
        "fields": FIELDS,
    }
    _seed(ctgov, "GET", f"{ctgov.base}/studies", search_params, None, load("ctgov_search.json"))
    seeded.append(f"ClinicalTrials.gov search for {DEMO_DRUG}")
    _seed(
        ctgov, "GET", f"{ctgov.base}/studies/{DEMO_NCT_ID}", None, None,
        load(f"ctgov_study_raw_{DEMO_NCT_ID}.json"),
    )
    seeded.append(f"ClinicalTrials.gov full record for {DEMO_NCT_ID}")

    return seeded


def offline_clients(store: Optional[Store] = None) -> tuple[GnomadClient, ClinVarClient, CTGovClient, list[str]]:
    """Seed the cache and return clients that will refuse to touch the network."""
    store = store or Store()
    seeded = seed_demo_cache(store)
    return (
        GnomadClient(store=store, offline=True),
        ClinVarClient(store=store, offline=True),
        CTGovClient(store=store, offline=True),
        seeded,
    )
