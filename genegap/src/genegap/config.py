"""Central settings. Reads .env (if present) without needing python-dotenv."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-argon")
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", "gemini-3.8-flash")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")

DB_PATH = ROOT / "data" / "genegap.sqlite"
SMOKE_DIR = ROOT / "data" / "smoke"
USER_AGENT = f"GeneGap-hackathon/0.1 ({CONTACT_EMAIL or 'no-contact-given'})"
