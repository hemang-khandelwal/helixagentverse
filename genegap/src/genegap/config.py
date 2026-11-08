"""Central configuration and environment loader for GeneGap."""
import os
from pathlib import Path

# Project root directory (two levels above this file)
ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path) -> None:
    """
    Minimal .env loader without third-party dependencies.
    Reads KEY=VALUE lines into os.environ, skipping blank lines and comments.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Load environment variables from .env in project root if present
_load_env(ROOT / ".env")

# Google Gemini API key for running LLM agents
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Flagship reasoning model (Gemini 4 Argon): used for planning, multi-paper evidence synthesis, and the Critic
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-argon")

# Fast parsing model (Gemini 3.8 Flash): used for high-volume extraction of messy trial demographic tables
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", "gemini-3.8-flash")

# Optional NCBI Entrez API key: increases query rate from 3 req/s to 10 req/s on ClinVar/PubMed
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")

# Contact email included in HTTP User-Agent headers to comply with NCBI/EBI good-citizen policies
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")

# Path to the primary SQLite database holding cache, facts, and agent trajectory steps
DB_PATH = ROOT / "data" / "genegap.sqlite"

# Directory where Phase 0 raw API smoke test payloads are saved for inspection
SMOKE_DIR = ROOT / "data" / "smoke"

# Identification header passed to external scientific APIs (gnomAD, ClinicalTrials.gov, EBI)
USER_AGENT = f"GeneGap-hackathon/0.1 ({CONTACT_EMAIL or 'no-contact-given'})"
