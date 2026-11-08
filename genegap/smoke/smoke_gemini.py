"""
Gemini API smoke test (Task 0.2).
Run: python smoke/smoke_gemini.py

Tests that your GEMINI_API_KEY works for both the flagship model (Gemini 4 Argon)
and the fast model (Gemini 3.8 Flash), verifying plain text and structured JSON responses.
"""
import sys
from pathlib import Path

# Add the 'src' directory to sys.path so 'genegap' can be imported without installation
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import BaseModel
from genegap import config


class Pair(BaseModel):
    """Simple schema used to test structured JSON output generation."""
    gene: str
    drug: str


def main() -> int:
    # Check that an API key has been provided
    if not config.GEMINI_API_KEY:
        print("FAIL  GEMINI_API_KEY is empty. Copy .env.example to .env and fill it in.")
        return 1

    # Ensure the Google GenAI SDK is available
    try:
        from google import genai
    except ImportError:
        print("FAIL  google-genai not installed. Run: pip install -r requirements.txt")
        return 1

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    failed = 0

    # Test both models: flagship model for deep reasoning, fast model for bulk parsing
    for model in (config.GEMINI_MODEL, config.GEMINI_FAST_MODEL):
        try:
            # 1. Plain text generation check
            r = client.models.generate_content(
                model=model,
                contents="Reply with the single word: ready",
            )
            print(f"PASS  {model} plain text -> {r.text.strip()[:60]!r}")

            # 2. Structured JSON generation check with Pydantic validation
            r = client.models.generate_content(
                model=model,
                contents="Give the gene most associated with clopidogrel activation and the drug name.",
                config={
                    "response_mime_type": "application/json",
                    "response_schema": Pair,
                    "temperature": 0,  # Zero temperature ensures reproducible responses
                },
            )
            print(f"PASS  {model} structured JSON -> {r.parsed}")

        except Exception as e:
            # Catch and display the error so we can test both models even if one fails
            failed += 1
            print(f"FAIL  {model}: {type(e).__name__}: {str(e)[:200]}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
