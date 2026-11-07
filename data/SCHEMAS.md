# Data handover: A writes loaders, B supplies records

`contracts.py` is the source of truth for field names and Python types.
`data/schemas.json` lists the exact fields for each file. Do not rename fields
locally: agree on a contract change with A, B, and C first.

The filenames below are agreed destinations, not datasets that already exist.
A1 creates schemas only. B5 supplies actual records; A5 implements their loaders.
M2 supplies the contents of `severity_table.json`.

## File formats

- JSON files contain one top-level array of records. Field names exactly match
  the named dataclass. JSON `null` means unknown; use real booleans, not strings.
- CSV files are UTF-8 with a header matching the manifest's `fields`, in order.
  Frequencies are numeric fractions between 0 and 1, not percentages.
- All stored dates are ISO `YYYY-MM-DD`. Loaders convert them to `datetime.date`.
- `first_available` means the first public availability of the cited supporting
  record or source version. Never substitute a retrieval date or guess a date.
  Undated records need review before historical use.
- Preserve stable source IDs and direct source URLs. Supporting text must be
  copied accurately and kept within the source's permitted usage.
- Never treat missing evidence as evidence that a drug is safe.

## Files and meaning

| File | Record in contracts.py | Contents |
| --- | --- | --- |
| `compounds.json` | `CompoundRecord` | Compound identity, structure, target genes, and provenance. `target_genes` is an array of strings. |
| `pgx.json` | `PgxAssociationRecord` | Drug/gene/allele/phenotype evidence from the chosen CPIC/PharmGKB source. An unknown allele, guideline, or assay status is `null`. Preserve the source's evidence label. |
| `frequencies.csv` | `FrequencyRecord` | One variant and ancestry group per row, with lower/point/upper frequency estimates and the source version. |
| `biomarkers.csv` | `BiomarkerRecord` | FDA biomarker reference rows used to curate evaluation. These rows are not an independent proof of allele-specific adverse effects. Evaluation-only; do not feed the benchmark answer table to the agent. |
| `evaluation_cases.json` | `EvaluationCase` | Evaluation drug, case kind, exact historical cutoff, and the evidence/rationale for choosing it. Evaluation-only metadata, not an agent evidence source. |
| `ground_truth.json` | `GroundTruthRecord` | Reviewed acceptable associations, one row per association. Only evaluation code reads this file. `source_ids` is an array of strings. |
| `severity_table.json` | `SeverityRecord` | Clinician-defined phenotype categories, rationale, and supporting source. |

## Frequency rules

`frequency_kind` must be `allele` or `carrier`; they are different measurements.
The bounds must satisfy `0 <= low <= point <= high <= 1`. If the source has only
a point estimate, B must document how bounds were derived before supplying this
row; do not silently invent precision or label an allele frequency as a carrier
frequency. Do not silently set all three values equal to imply known bounds.

C6 may multiply an appropriate carrier-frequency estimate by 1,000 in Python.
An allele frequency needs an explicit, justified conversion before that step.
Carriers are not predicted adverse events.

## Historical evidence rules

The cutoff applies to every supporting evidence edge, including associations,
compound metadata, and any related-drug evidence. `HelixTools` binds the cutoff
when constructed. The AI's per-call arguments must not override it.

`evaluation_cases.json` may name the target drug, but `ground_truth.json` and the
biomarker benchmark table must not enter the tool collection or prompts.
A record copied from today's database is not historical evidence merely because
it mentions an old paper: retain the dated supporting passage needed to audit it.

`no_known_association` means no association established in the chosen reference
set. Emissions on these cases are unverified, not automatically false positives.

## Example CSV headers

These are headers only, with no invented medical rows:

```csv
variant,gene,ancestry,frequency_kind,frequency_low,frequency_point,frequency_high,source_id,source_url,source_version,first_available
```

```csv
drug_name,gene,phenotype,source_id,source_url,first_available
```
