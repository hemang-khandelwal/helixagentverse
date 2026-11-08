"""Deterministic language and claim-strength checker for GeneGap reports.

Hard rule, the same as the critic: this module contains NO LLM calls and NO network
calls. It reads the report text and the ``GapResult`` the text claims to describe, and
decides whether the prose says more than the numbers support.

The critic checks that the numbers are right. This checks that the write-up does not
overclaim them.

Rules:
  Adjudicating (can FAIL and block the report -> must not be issued as-is):
    L1  no clinical directives
    L2  no claims about individuals
    L3  no causal language for associative evidence
    L4  claim strength must match tier
    L5  no invented precision
    L6  no confidence scalars in prose
  Annotating (can only WARN):
    L7  absolute and totalising words
    L8  numeric claim with no citation marker nearby
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from .schemas import GapResult, GroupGap

RuleStatus = Literal["PASS", "WARN", "FAIL"]
RuleId = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]

ADJUDICATING_RULES: Tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5", "L6")
ANNOTATING_RULES: Tuple[str, ...] = ("L7", "L8")


# --------------------------------------------------------------------------- models


class LanguageFinding(BaseModel):
    """One offending sentence, quoted back with its position in the source text."""

    rule_id: RuleId
    sentence: str               # the offending sentence, verbatim
    offset: int                 # character offset of the sentence within the input text
    match: str = ""             # the specific phrase that tripped the rule
    reason: str = ""            # why this sentence is a problem, in plain words


class RuleResult(BaseModel):
    """Outcome of a single language rule (L1-L8). Mirrors critic.CheckResult."""

    rule_id: RuleId
    name: str
    adjudicating: bool
    status: RuleStatus
    reason: str
    findings: List[LanguageFinding] = Field(default_factory=list)

    @property
    def details(self) -> List[str]:
        """One line per finding, for printing beside the critic's own details."""
        return [
            f"[offset {f.offset}] {f.reason} -- {f.sentence!r}"
            for f in self.findings
        ]


class LanguageReport(BaseModel):
    """Aggregate report produced by ``check_language(text, result, tier)``.

    Mirrors ``critic.CriticReport``: a status, a dict of per-rule results, a ``passed``
    property, per-rule accessors and ``__getitem__``.
    """

    status: RuleStatus
    tier: str
    rules: Dict[str, RuleResult]

    @property
    def passed(self) -> bool:
        """True when no adjudicating rule failed, i.e. the report may be issued."""
        return self.status != "FAIL"

    @property
    def findings(self) -> List[LanguageFinding]:
        """Every finding across every rule, in rule order."""
        out: List[LanguageFinding] = []
        for rid in ADJUDICATING_RULES + ANNOTATING_RULES:
            if rid in self.rules:
                out += self.rules[rid].findings
        return out

    @property
    def failed_rules(self) -> List[str]:
        return [rid for rid, r in self.rules.items() if r.status == "FAIL"]

    @property
    def l1(self) -> RuleResult: return self.rules["L1"]

    @property
    def l2(self) -> RuleResult: return self.rules["L2"]

    @property
    def l3(self) -> RuleResult: return self.rules["L3"]

    @property
    def l4(self) -> RuleResult: return self.rules["L4"]

    @property
    def l5(self) -> RuleResult: return self.rules["L5"]

    @property
    def l6(self) -> RuleResult: return self.rules["L6"]

    @property
    def l7(self) -> RuleResult: return self.rules["L7"]

    @property
    def l8(self) -> RuleResult: return self.rules["L8"]

    def __getitem__(self, key: str) -> RuleResult:
        return self.rules[key]


# ----------------------------------------------------------------- sentence handling


@dataclass(frozen=True)
class Sentence:
    """One sentence of the report, with its character offset in the original text."""

    text: str
    offset: int

    @property
    def lower(self) -> str:
        return self.text.lower()


# A boundary is end punctuation followed by space and a capital/opening quote, or a
# newline. Requiring a capital after the space keeps "0.5" and "e.g. the" intact.
_BOUNDARY = re.compile(r'(?<=[.!?])[ \t]+(?=["\'(\[]?[A-Z])|\n+')


def split_sentences(text: str) -> List[Sentence]:
    """Split ``text`` into sentences, keeping each one's offset in the original string."""
    sentences: List[Sentence] = []
    cursor = 0
    for boundary in _BOUNDARY.finditer(text):
        chunk = text[cursor:boundary.start()]
        if chunk.strip():
            lead = len(chunk) - len(chunk.lstrip())
            sentences.append(Sentence(chunk.strip(), cursor + lead))
        cursor = boundary.end()
    tail = text[cursor:]
    if tail.strip():
        lead = len(tail) - len(tail.lstrip())
        sentences.append(Sentence(tail.strip(), cursor + lead))
    return sentences


# Identifiers that contain digits but are not quantities. Masked (length-preserving)
# before numbers are scanned, so "rs4244285" never reads as the number 4244285.
_IDENTIFIER_PATTERNS = (
    r"\bNCT\d+\b",                      # trial registry ids
    r"\brs\d+\b",                       # rsIDs
    r"\bF-[0-9a-fA-F]+\b",              # our own Fact ids
    r"\b\d+-\d+-[ACGT]+-[ACGT]+\b",     # gnomAD variant ids
    r"\b[A-Z]{2,}\d+[A-Z]*\d*\b",       # gene symbols: CYP2C19, SLCO1B1
    r"\*\d+[A-Z]?\b",                   # star alleles
    r"\bPMID:?\s*\d+\b",
    r"\bdoi:\s*\S+",
    r"\b10\.\d{4,}/\S+",                # bare DOIs
    r"\bgnomAD\s*v?\d+(\.\d+)?\b",      # dataset versions
)
_IDENTIFIERS = re.compile("|".join(_IDENTIFIER_PATTERNS))


def _mask_identifiers(text: str) -> str:
    """Replace identifier characters with 'x', preserving length so offsets still hold."""
    out = list(text)
    for m in _IDENTIFIERS.finditer(text):
        for i in range(m.start(), m.end()):
            if out[i].isdigit():
                out[i] = "x"
    return "".join(out)


_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%)?")


@dataclass(frozen=True)
class NumberToken:
    """A quantity found in the prose."""

    raw: str
    value: float          # on the proportion scale: "19.3%" -> 0.193, "0.193" -> 0.193
    decimals: int         # decimal places on the proportion scale
    is_percent: bool
    start: int            # offset within the sentence


def extract_numbers(sentence: str) -> List[NumberToken]:
    """Every quantity in a sentence, with identifiers masked out first."""
    masked = _mask_identifiers(sentence)
    tokens: List[NumberToken] = []
    for m in _NUMBER.finditer(masked):
        whole, frac, pct = m.group(1), m.group(2) or "", m.group(3)
        if "x" in whole or "x" in frac:
            continue
        literal = float(whole.replace(",", "") + ("." + frac if frac else ""))
        decimals = len(frac)
        if pct:
            tokens.append(NumberToken(m.group(0).strip(), literal / 100.0, decimals + 2, True, m.start()))
        else:
            tokens.append(NumberToken(m.group(0).strip(), literal, decimals, False, m.start()))
    return tokens


# ------------------------------------------------------------- matching prose to gaps


# Fields that are proportions (0-1) and may legitimately be written as percentages.
_PROPORTION_FIELDS = (
    "allele_freq", "trial_share", "target_share", "carrier_freq",
    "ci_low", "ci_high", "prob_fewer_than_k",
)
# Fields that are counts or unbounded ratios, written as-is.
_SCALAR_FIELDS = (
    "n_trial", "ac", "an", "expected_carriers", "enrolment_ratio",
    "carrier_ratio", "required_n",
)
# Frequencies whose precision is bounded by the allele number (rule L5).
_FREQUENCY_FIELDS = ("allele_freq", "carrier_freq", "ci_low", "ci_high")


@dataclass(frozen=True)
class NumberMatch:
    """A quantity in the prose traced back to the GroupGap field it came from."""

    token: NumberToken
    group: GroupGap
    field: str


def _matches(field_value: float, token: NumberToken, is_proportion: bool) -> bool:
    """True when the token is this value written to the token's own precision."""
    if field_value is None:
        return False
    if token.is_percent and not is_proportion:
        return False
    try:
        return round(float(field_value), token.decimals) == round(token.value, token.decimals)
    except (ValueError, OverflowError):
        return False


def match_numbers(sentence: str, result: GapResult) -> List[NumberMatch]:
    """Trace each quantity in a sentence back to the GroupGap field that produced it.

    A quantity that matches no field is left alone: the prose is allowed to contain
    numbers that do not come from the result (a year, a count of trials, the 95 in
    '95% credible interval').
    """
    matches: List[NumberMatch] = []
    for token in extract_numbers(sentence):
        for gap in result.groups:
            field = _first_matching_field(gap, token)
            if field is not None:
                matches.append(NumberMatch(token, gap, field))
                break
    return matches


def _first_matching_field(gap: GroupGap, token: NumberToken) -> Optional[str]:
    for field in _PROPORTION_FIELDS:
        value = getattr(gap, field, None)
        if value is not None and _matches(value, token, is_proportion=True):
            return field
    if not token.is_percent:
        for field in _SCALAR_FIELDS:
            value = getattr(gap, field, None)
            if value is not None and _matches(value, token, is_proportion=False):
                return field
    return None


def permissible_decimals(an: Optional[int]) -> Optional[int]:
    """Decimal places a frequency may be quoted to, on the proportion scale.

    Derived, not hardcoded: an allele number of AN resolves frequencies no finer than
    1/AN, so digits beyond log10(AN) are invented. AN = 36,782 resolves about 4 decimal
    places on a proportion (two on a percentage).
    """
    if an is None or an <= 0:
        return None
    return int(math.floor(math.log10(an)))


# --------------------------------------------------------------------------- lexicons


# L1: verbs that belong to prescribing, and the modals that turn them into directives.
_PRESCRIBING_VERBS = (
    "prescribe", "prescribed", "prescribing", "dose", "dosed", "dosing",
    "administer", "administered", "administering", "give", "given", "giving",
    "switch", "switched", "switching", "avoid", "avoided", "avoiding",
    "discontinue", "discontinued", "stop", "stopped", "titrate", "titrated",
    "initiate", "initiated", "substitute", "substituted", "withhold", "withheld",
    "treat", "treated", "treating", "medicate", "prescribe-to",
)
_MODALS = (
    "should", "must", "shall", "ought to", "needs to", "need to", "has to",
    "have to", "is to be", "are to be", "do not", "don't", "never", "always",
)
_L1_PHRASES = (
    "contraindicated", "first-line", "first line therapy", "is indicated",
    "are indicated", "recommend dosing", "recommended dose", "dose adjustment",
    "dosing guidance", "clinical recommendation", "we recommend", "it is recommended",
    "standard of care", "should be prescribed", "must avoid", "switch to",
    "do not give", "withhold treatment",
)
# modal (+ up to three filler words: not, be, therefore, instead, ...) + prescribing verb
_L1_GRAMMAR = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _MODALS) + r")\b"
    r"(?:\s+\w+){0,3}?\s+"
    r"\b(" + "|".join(re.escape(v) for v in _PRESCRIBING_VERBS) + r")\b",
    re.IGNORECASE,
)
_L1_PHRASE_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _L1_PHRASES) + r")\b", re.IGNORECASE
)

# L2: a singular person as the subject of an outcome, or bare "carriers <verb>".
_PERSON_NOUNS = ("patient", "individual", "person", "carrier", "subject", "participant")
_OUTCOME_VERBS = (
    "will", "would", "experiences", "experience", "develops", "develop",
    "suffers", "suffer", "responds", "respond", "fails", "fail",
    "is at risk", "are at risk", "requires", "require", "shows", "show",
    "has reduced", "have reduced", "metabolises", "metabolizes",
)
_L2_SINGULAR = re.compile(
    r"\b(a|an|the|this|that|each|every|any|one)\s+"
    r"(?:\w+\s+){0,2}?"
    r"\b(" + "|".join(_PERSON_NOUNS) + r")\b"
    r"(?:\s+\w+){0,4}?\s+"
    r"\b(" + "|".join(re.escape(v) for v in _OUTCOME_VERBS) + r")\b",
    re.IGNORECASE,
)
_L2_BARE_PLURAL = re.compile(
    r"\b(" + "|".join(n + "s" for n in _PERSON_NOUNS) + r")\s+"
    r"\b(" + "|".join(re.escape(v) for v in _OUTCOME_VERBS) + r")\b",
    re.IGNORECASE,
)
# "carrier frequency", "expected carriers", "carrier ratio" are quantities, not people.
_L2_QUANTITY_CONTEXT = re.compile(
    r"\b(carrier|participant|patient|individual|subject)s?\s+"
    r"(frequency|frequencies|ratio|share|count|counts|rate|estimate|numbers?|total)\b"
    r"|\b(expected|observed|enrolled|mapped|pooled)\s+\w{0,12}\s*"
    r"(carriers|participants|patients|individuals|subjects)\b",
    re.IGNORECASE,
)

# L3: variant-as-cause-of-outcome.
_VARIANT_TERMS = (
    "variant", "variants", "allele", "alleles", "genotype", "genotypes",
    "polymorphism", "mutation", "snp", "haplotype", "star allele",
    "loss-of-function", "poor metaboliser", "poor metabolizer",
)
_CAUSAL_MARKERS = (
    "causes", "cause", "caused", "causing", "leads to", "lead to", "led to",
    "leading to", "results in", "result in", "resulting in", "due to",
    "because of", "responsible for", "induces", "induce", "drives", "drive",
    "brings about", "produces",
)
_OUTCOME_TERMS = (
    "risk", "event", "events", "outcome", "outcomes", "response", "non-response",
    "nonresponse", "failure", "toxicity", "mortality", "death", "deaths",
    "bleeding", "efficacy", "stroke", "thrombosis", "infarction", "harm",
    "adverse", "side effect", "side effects", "hospitalisation", "hospitalization",
    "reduced effect", "treatment failure",
)
_VARIANT_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _VARIANT_TERMS) + r")\b", re.IGNORECASE)
_CAUSAL_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _CAUSAL_MARKERS) + r")\b", re.IGNORECASE)
_OUTCOME_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _OUTCOME_TERMS) + r")\b", re.IGNORECASE)

# L4: phrases that explicitly name the limitation, so a weak number may be stated.
_QUALIFIERS = (
    "insufficient data", "insufficient", "too few alleles", "too few",
    "cannot be assessed", "not assessable", "could not be assessed",
    "provisional", "uncertain", "uncertainty", "wide credible interval",
    "wide interval", "small sample", "low allele number", "below the",
    "not established", "tier b", "illustrative", "indicative", "preliminary",
    "caution", "unreliable", "sensitivity analysis", "not a measured gap",
    "unverified", "awaiting", "subject to", "should be treated as",
    "mapping-dependent", "does not survive", "not robust", "hypothesis",
    "requires expert review", "pending", "limited", "approximate",
)
_QUALIFIER_RE = re.compile(r"\b(" + "|".join(re.escape(q) for q in _QUALIFIERS) + r")\b", re.IGNORECASE)

# L6: a number presented as the system's confidence in its own output.
_CONFIDENCE_PATTERNS = (
    r"\bconfidence\s+(?:of|score|level|rating|value)?\s*(?:of\s*)?\d+(?:\.\d+)?\s*%",
    r"\b\d+(?:\.\d+)?\s*%\s+(?:confiden\w+|certain\w*|sure|accurate|accuracy|reliab\w+)",
    r"\bconfidence\s+(?:score|rating|level)\b",
    r"\b(?:we|the system|genegap|this report)\s+(?:are|is|am)\s+\d+(?:\.\d+)?\s*%",
    r"\b(?:certainty|reliability|accuracy)\s+of\s+\d+(?:\.\d+)?\s*%",
    r"\bscore\s+of\s+\d+(?:\.\d+)?\s*(?:/|out of)\s*\d+",
    r"\b\d+(?:\.\d+)?\s*(?:/|out of)\s*\d+\s+(?:checks?\s+)?(?:passed|confidence)",
    r"\bconfidence\s*[:=]\s*\d",
)
_CONFIDENCE_RE = re.compile("|".join(_CONFIDENCE_PATTERNS), re.IGNORECASE)
# A credible or confidence *interval* on a frequency is legitimate and must not trip L6.
_INTERVAL_RE = re.compile(
    r"\b(credible interval|confidence interval|\bci\b|95%\s*(?:credible|confidence|ci)|"
    r"interval of|posterior|beta\b)",
    re.IGNORECASE,
)

# L7: absolute and totalising words.
_L7_WORDS = (
    "always", "never", "all patients", "every patient", "proves", "proven",
    "confirms", "confirmed", "guarantees", "guaranteed", "safe", "unsafe",
    "definitive", "definitively", "certainly", "undoubtedly", "conclusive",
    "no doubt", "entirely", "completely", "impossible",
)
_L7_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _L7_WORDS) + r")\b", re.IGNORECASE)

# L8: something that identifies where a number came from.
_CITATION_RE = re.compile(
    r"\bNCT\d+\b|\brs\d+\b|\bF-[0-9a-fA-F]{6,}\b|\bgnomAD\b|\bClinVar\b|"
    r"\bClinicalTrials\.gov\b|\bPMID\b|\bdoi\b|\bPubMed\b|\bEurope PMC\b|"
    r"\bCPIC\b|\bPharmGKB\b|\bsource:|\[[^\]]+\]|\bfact[_ ]id\b|\btable \d",
    re.IGNORECASE,
)


# ------------------------------------------------------------------------- the rules


def _result(rule_id: str, name: str, adjudicating: bool,
            findings: Sequence[LanguageFinding], clean_reason: str) -> RuleResult:
    """PASS when nothing was found; otherwise FAIL (adjudicating) or WARN (annotating)."""
    if findings:
        return RuleResult(
            rule_id=rule_id, name=name, adjudicating=adjudicating,
            status="FAIL" if adjudicating else "WARN",
            reason=findings[0].reason,
            findings=list(findings),
        )
    return RuleResult(rule_id=rule_id, name=name, adjudicating=adjudicating,
                      status="PASS", reason=clean_reason)


def _check_l1(sentences: Sequence[Sentence]) -> RuleResult:
    """L1: no clinical directives."""
    findings = []
    for s in sentences:
        hit = _L1_PHRASE_RE.search(s.text) or _L1_GRAMMAR.search(s.text)
        if hit:
            findings.append(LanguageFinding(
                rule_id="L1", sentence=s.text, offset=s.offset, match=hit.group(0),
                reason=(f"Clinical directive {hit.group(0)!r}. GeneGap reports population-level "
                        f"enrolment gaps and does not advise care."),
            ))
    return _result("L1", "no clinical directives", True, findings,
                   "No sentence gives prescribing or treatment direction.")


def _check_l2(sentences: Sequence[Sentence]) -> RuleResult:
    """L2: no claims about individuals."""
    findings = []
    for s in sentences:
        if _L2_QUANTITY_CONTEXT.search(s.text):
            continue
        hit = _L2_SINGULAR.search(s.text) or _L2_BARE_PLURAL.search(s.text)
        if hit:
            findings.append(LanguageFinding(
                rule_id="L2", sentence=s.text, offset=s.offset, match=hit.group(0),
                reason=(f"Claim about a person rather than a population: {hit.group(0)!r}. "
                        f"Every claim must be framed as a group or population statement."),
            ))
    return _result("L2", "no claims about individuals", True, findings,
                   "Every claim is framed at group or population level.")


def _check_l3(sentences: Sequence[Sentence]) -> RuleResult:
    """L3: no causal language for associative evidence."""
    findings = []
    for s in sentences:
        variant = _VARIANT_RE.search(s.text)
        if not variant:
            continue
        for causal in _CAUSAL_RE.finditer(s.text):
            if causal.start() <= variant.start():
                continue
            outcome = _OUTCOME_RE.search(s.text, causal.end())
            if outcome:
                findings.append(LanguageFinding(
                    rule_id="L3", sentence=s.text, offset=s.offset, match=causal.group(0),
                    reason=(f"Causal claim {causal.group(0)!r} linking {variant.group(0)!r} to "
                            f"{outcome.group(0)!r}. GeneGap observes frequency differences and "
                            f"under-enrolment; it does not establish causation."),
                ))
                break
    return _result("L3", "no causal language for associative evidence", True, findings,
                   "No sentence asserts that a variant causes an outcome.")


def _check_l4(sentences: Sequence[Sentence], result: GapResult, tier: str) -> RuleResult:
    """L4: claim strength must match tier.

    Structural, not a word list: each quantity in the prose is matched back to the
    GroupGap it came from. A number from a Tier B report, or from a group flagged
    insufficient_data, must be stated with an explicit qualifier naming the limitation.
    """
    findings = []
    tier_is_weak = _normalise_tier(tier) == "TIER B"

    for s in sentences:
        weak_reasons: List[str] = []
        matched_phrase = ""
        for m in match_numbers(s.text, result):
            if m.group.insufficient_data:
                weak_reasons.append(
                    f"{m.token.raw} is a {m.field} for group '{m.group.group}', which is "
                    f"flagged insufficient_data (an={m.group.an})"
                )
            elif tier_is_weak:
                weak_reasons.append(
                    f"{m.token.raw} is a {m.field} for group '{m.group.group}' in a TIER B report"
                )
            if weak_reasons and not matched_phrase:
                matched_phrase = m.token.raw
        if weak_reasons and not _QUALIFIER_RE.search(s.text):
            findings.append(LanguageFinding(
                rule_id="L4", sentence=s.text, offset=s.offset, match=matched_phrase,
                reason=(f"Unqualified assertion: {weak_reasons[0]}, so the sentence must name "
                        f"the limitation explicitly."),
            ))
    return _result("L4", "claim strength must match tier", True, findings,
                   f"Every number stated is supported at {_normalise_tier(tier)}.")


def _check_l5(sentences: Sequence[Sentence], result: GapResult) -> RuleResult:
    """L5: no invented precision."""
    findings = []
    for s in sentences:
        for m in match_numbers(s.text, result):
            if m.field not in _FREQUENCY_FIELDS:
                continue
            allowed = permissible_decimals(m.group.an)
            if allowed is None or m.token.decimals <= allowed:
                continue
            findings.append(LanguageFinding(
                rule_id="L5", sentence=s.text, offset=s.offset, match=m.token.raw,
                reason=(f"{m.token.raw} quotes a {m.field} to {m.token.decimals} decimal places "
                        f"on the proportion scale, but group '{m.group.group}' has an="
                        f"{m.group.an}, which resolves only {allowed}. The extra figures are "
                        f"invented precision."),
            ))
            break
    return _result("L5", "no invented precision", True, findings,
                   "No frequency is quoted to more figures than its allele number supports.")


def _check_l6(sentences: Sequence[Sentence]) -> RuleResult:
    """L6: no confidence scalars in prose."""
    findings = []
    for s in sentences:
        for hit in _CONFIDENCE_RE.finditer(s.text):
            window = s.text[max(0, hit.start() - 40):hit.end() + 40]
            if _INTERVAL_RE.search(window):
                continue            # a credible interval on a frequency is legitimate
            findings.append(LanguageFinding(
                rule_id="L6", sentence=s.text, offset=s.offset, match=hit.group(0),
                reason=(f"{hit.group(0)!r} presents a numeric confidence in GeneGap's own output. "
                        f"The tier is categorical and a ratio of passed checks is not calibrated."),
            ))
            break
    return _result("L6", "no confidence scalars in prose", True, findings,
                   "No numeric self-confidence is claimed; credible intervals are left alone.")


def _check_l7(sentences: Sequence[Sentence]) -> RuleResult:
    """L7: absolute and totalising words (warning only)."""
    findings = []
    for s in sentences:
        hit = _L7_RE.search(s.text)
        if hit:
            findings.append(LanguageFinding(
                rule_id="L7", sentence=s.text, offset=s.offset, match=hit.group(0),
                reason=f"Absolute or totalising word {hit.group(0)!r}; prefer hedged wording.",
            ))
    return _result("L7", "absolute and totalising words", False, findings,
                   "No absolute or totalising wording found.")


def _check_l8(sentences: Sequence[Sentence], result: GapResult) -> RuleResult:
    """L8: a numeric claim with no citation marker in this sentence or the next."""
    findings = []
    for i, s in enumerate(sentences):
        if not match_numbers(s.text, result):
            continue
        window = s.text
        if i + 1 < len(sentences):
            window += " " + sentences[i + 1].text
        if _CITATION_RE.search(window):
            continue
        findings.append(LanguageFinding(
            rule_id="L8", sentence=s.text, offset=s.offset, match="",
            reason=("Numeric claim with no citation marker in this sentence or the next; "
                    "every figure should name where it came from."),
        ))
    return _result("L8", "numeric claims carry a citation", False, findings,
                   "Every numeric claim has a citation marker nearby.")


# --------------------------------------------------------------------------- the API


def _normalise_tier(tier: str) -> str:
    """Accept 'TIER A', 'tier_b', 'A', 'B' or 'REJECTED' and return the canonical form."""
    t = (tier or "").strip().upper().replace("_", " ")
    if t in {"A", "TIER A"}:
        return "TIER A"
    if t in {"B", "TIER B"}:
        return "TIER B"
    if t == "REJECTED":
        return "REJECTED"
    raise ValueError(
        f"Unknown tier {tier!r}. Expected 'TIER A', 'TIER B' or 'REJECTED' "
        f"(as produced by genegap.critic.criticise)."
    )


def check_language(text: str, result: GapResult, tier: str) -> LanguageReport:
    """Check a drafted report against the language and claim-strength rules.

    Args:
        text: the drafted report prose.
        result: the GapResult the prose describes; numbers are traced back to it.
        tier: the critic's tier for that result ('TIER A', 'TIER B' or 'REJECTED').

    Returns:
        A LanguageReport. ``report.passed`` is False when any adjudicating rule failed,
        which means the report must not be issued as-is.
    """
    canonical_tier = _normalise_tier(tier)
    sentences = split_sentences(text)

    rules = {
        "L1": _check_l1(sentences),
        "L2": _check_l2(sentences),
        "L3": _check_l3(sentences),
        "L4": _check_l4(sentences, result, canonical_tier),
        "L5": _check_l5(sentences, result),
        "L6": _check_l6(sentences),
        "L7": _check_l7(sentences),
        "L8": _check_l8(sentences, result),
    }

    if any(r.status == "FAIL" for r in rules.values()):
        status: RuleStatus = "FAIL"
    elif any(r.status == "WARN" for r in rules.values()):
        status = "WARN"
    else:
        status = "PASS"

    return LanguageReport(status=status, tier=canonical_tier, rules=rules)
