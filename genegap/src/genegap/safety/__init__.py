"""
Safety, language guards, and refusal handling for GeneGap:
- Treats retrieved external text as untrusted data (prompt injection defense).
- Enforces language rules (Deliverable D9): distinguishes self-identified race from genetic ancestry,
  avoids biological essentialism, and rejects causal overstatements without high-tier evidence.
- Refusal handling: politely rejects individual genotype interpretation or clinical dosing requests.
"""

__all__ = []
