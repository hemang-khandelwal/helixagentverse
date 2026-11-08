"""Task 0.2. Run:  python smoke/smoke_gemini.py
Checks that your key works for the main model and the fast model, in plain and JSON mode."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import BaseModel

from genegap import config


class Pair(BaseModel):
    gene: str
    drug: str


def main() -> int:
    if not config.GEMINI_API_KEY:
        print("FAIL  GEMINI_API_KEY is empty. Copy .env.example to .env and fill it in.")
        return 1
    try:
        from google import genai
    except ImportError:
        print("FAIL  google-genai not installed. Run: pip install -r requirements.txt")
        return 1

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    failed = 0
    for model in (config.GEMINI_MODEL, config.GEMINI_FAST_MODEL):
        try:
            r = client.models.generate_content(model=model, contents="Reply with the single word: ready")
            print(f"PASS  {model} plain text -> {r.text.strip()[:60]!r}")
            r = client.models.generate_content(
                model=model,
                contents="Give the gene most associated with clopidogrel activation and the drug name.",
                config={"response_mime_type": "application/json", "response_schema": Pair, "temperature": 0},
            )
            print(f"PASS  {model} structured JSON -> {r.parsed}")
        except Exception as e:  # keep going so you see every failure
            failed += 1
            print(f"FAIL  {model}: {type(e).__name__}: {str(e)[:200]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
