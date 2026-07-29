# K-1 OTD Toolkit

A staged toolkit for extracting, structuring, assembling, and validating IRS Schedule K-1 (Form 1065) data as Open Tax Document (OTD) YAML.

This directory contains the K-1-specific operational layer of the [Open Tax Document project](../../README.md): grammar-based face extraction, deterministic package classification, fragment utilities, taxonomy-driven validation, and agent guidance for the parts that still require judgment.

## Current status

| Area | Current state |
|---|---|
| Document family | Schedule K-1 (Form 1065) |
| Deterministic face grammar | Tax year 2025 |
| Workflow | Staged; no single end-to-end orchestrator |
| Face extraction | Grammar and geometry based |
| Package classification | Deterministic logical-section classification |
| Overflow and footnotes | Agent-guided extraction with deterministic validation |
| Assembly | Five named JSON fragments into OTD YAML |
| Validation | Fail-closed, taxonomy-driven validator |
| Contract evidence | 33 blocking validator cases; no deferred cases |
| PDF regression corpus | Two machine-local 2025 fixtures |
| Standard maturity | Public draft; not universal conformance certification |

The toolkit is useful for controlled, evidence-backed K-1 work. It is not a claim of broad support across all vendors, years, scanned documents, rotations, or K-3 packages.

## What the toolkit does

- Extracts each PDF page's text and basic metadata.
- Segments pages into logical sections and classifies their roles.
- Validates a 2025 K-1 face grammar.
- Checks whether a grammar fits a candidate PDF before face values are trusted.
- Reads face-page fields and checkboxes into evidence envelopes.
- Extracts selected state grids and line-item detail tables.
- Supports agent-reviewed overflow statements and footnotes.
- Assembles five canonical fragments into OTD YAML plus a confidence manifest.
- Validates document structure, taxonomy identity, constraint paths, nested types, closed enums, statement classifications, references, and no-fabrication rules.
- Reconciles face values to printed line-item details when those details are available.

## What it does not do

- It does not provide a single-command PDF-to-OTD pipeline.
- It does not automatically resolve ambiguous or unsupported layouts.
- It does not certify all preparers, form years, scanned PDFs, rotations, or skew.
- It does not provide broad K-3 extraction coverage.
- It does not make warnings disappear. Every warning and `_unverified` marker requires disposition.
- It does not make the standalone template-fit gate part of face extraction automatically; operators must run and inspect it.

## Repository layout

```text
skills/k1-otd/
├── README.md
├── SKILL.md
├── grammars/
│   ├── GRAMMAR-FORMAT.md
│   └── k1-1065-2025.grammar.yaml
├── signatures/
│   └── page-signatures.yaml
├── reference/
│   └── bundled OTD specifications, taxonomy, and proof fixture
└── scripts/
    └── extraction, classification, assembly, reconciliation, and validation tools
```

The governing K-1 taxonomy is `taxonomies/irs-k1-1065-2025.yaml`.

## Requirements

- Python 3.9 or later
- `pdfplumber`
- `PyYAML`
- `ruamel.yaml`

Install the observed third-party dependencies:

```bash
python -m pip install pdfplumber PyYAML ruamel.yaml
```

Run commands from the repository root. Keep the source PDF immutable and put generated files under a dedicated artifact directory.

## Canonical staged workflow

The examples below use `work/` as the artifact directory.

### 1. Preserve the source and extract page text

Record the source PDF's SHA-256 before extraction. Then run:

```bash
python skills/k1-otd/scripts/phase1_extract_text.py \
  --pdf path/to/package.pdf \
  --out work
```

This writes page text files beneath `work/text_blocks/` and a `page_index.json`.

Image-only PDFs need an explicit OCR step outside this script. Preserve OCR provenance and do not present OCR text as native PDF evidence.

### 2. Validate and fit the face grammar

Validate the grammar itself:

```bash
python skills/k1-otd/scripts/grammar_validator.py \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml
```

Run the standalone fit gate:

```bash
python skills/k1-otd/scripts/template_match.py \
  --pdf path/to/package.pdf \
  --grammar skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --outdir work/template-fit
```

Inspect `work/template-fit/template_fit_report.json`.

Do not rely on the process return code as the fit decision. Proceed only when the report is evaluable, has no ambiguous tie, and identifies a unique matching grammar. Treat `partial`, `mismatch`, `unverified`, and ambiguous ties as stop-and-review outcomes.

### 3. Classify logical sections

```bash
python skills/k1-otd/scripts/phase2_classify.py \
  --index work/text_blocks/page_index.json \
  --text-dir work/text_blocks \
  --out work/fragments/page_manifest.json \
  --section-out work/fragments/section_manifest.json
```

The normal path uses deterministic section-level classification. Do not use `--legacy-heuristics` except to reproduce historical behavior. Any unresolved section requires review before extraction continues.

### 4. Build extraction fragments

Read the face page:

```bash
python skills/k1-otd/scripts/face_reader.py \
  path/to/package.pdf \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  > work/fragments/face_page.json
```

Extract state grids when present:

```bash
python skills/k1-otd/scripts/extract_state_grids.py \
  --input-dir work/text_blocks \
  --manifest work/fragments/page_manifest.json \
  --out work/fragments/state_schedules.json
```

Build line-item details when the package contains printed detail tables:

```bash
python skills/k1-otd/scripts/build_line_item_details.py \
  --pages work/text_blocks \
  --grammar skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --out work/fragments/line_item_details.json
```

Overflow statements and footnotes are not fully automated. Extract them from the classified source sections with explicit evidence and preserve source text. Never infer a value solely because it is plausible.

### 5. Satisfy the assembler fragment contract

`phase4_assemble.py` reads these five files from the fragment directory:

```text
face_page.json
overflow_statements.json
footnotes_a.json
footnotes_b.json
state_schedules.json
```

Use empty lists or empty structured objects only where the fragment contract permits them. Do not omit a fragment silently.

### 6. Assemble OTD YAML

```bash
python skills/k1-otd/scripts/phase4_assemble.py \
  --fragments work/fragments \
  --out work/output.otd.yaml \
  --sha256 SOURCE_PDF_SHA256
```

The assembler writes `work/output.otd.yaml` and a sibling confidence manifest, normally `work/output.confidence.json`.

### 7. Validate against the governing taxonomy

```bash
python skills/k1-otd/scripts/validate_otd.py \
  --input work/output.otd.yaml \
  --taxonomy taxonomies/irs-k1-1065-2025.yaml
```

Exit codes are stable:

| Code | Meaning |
|---:|---|
| `0` | The document passed the implemented conformance checks |
| `1` | The document is invalid |
| `2` | The validator or governing taxonomy could not operate safely |

A missing, malformed, empty, non-operative, or identity-mismatched taxonomy is a validator/configuration failure, not a successful validation.

### 8. Reconcile face and detail values

When `line_item_details.json` exists, run the hard-error reconciliation gate:

```bash
python skills/k1-otd/scripts/reconcile_line_item_details.py \
  --otd work/output.otd.yaml \
  --details work/fragments/line_item_details.json \
  --out work/line-item-reconciliation.json \
  --posture hard_error
```

Investigate every mismatch against the source PDF. Do not waive a mismatch by changing the expected output.

## Truth-preservation rules

These rules are non-negotiable:

1. Never guess.
2. An unobserved fact is `null` plus a non-empty `_unverified` explanation—not `false`, `0`, or an invented value.
3. Intentional masking uses `_masked: true`, not `_unverified`.
4. A `custom` statement classification requires non-empty `content.custom_classification`.
5. `unclassified_requires_review` requires a non-empty `_unverified` marker.
6. Statement nodes must be attached with `form.attachment: true`.
7. Box 16 and K-3 references must agree with the observed checkbox state.
8. Unknown extension nodes are preserved for forward compatibility; known taxonomy nodes remain strict.
9. Every warning, unresolved section, and `_unverified` marker must appear in the review ledger.

## Validation coverage

Run the repository test programs from the repository root:

```bash
python -B tests/test_validator_contract.py
python -B tests/test_constraint_engine.py
python -B tests/test_direct_documents.py
python -B tests/test_rectification.py
python -B tests/test_face_reader.py
python -B tests/check_mirrors.py
```

At the current repository revision, the validator contract matrix contains 33 blocking cases and no deferred cases.

`tests/test_face_reader.py` is intentionally not portable: it depends on two machine-local PDFs, a blank IRS 2025 form and one flattened preparer document. That suite is a regression net for confirmed examples, not proof of general vendor or form-year support.

## Important scripts

| Script | Role |
|---|---|
| `phase1_extract_text.py` | Per-page text extraction and page index |
| `phase2_classify.py` | Logical-section classification and manifests |
| `grammar_validator.py` | Grammar structure and reader validation |
| `template_match.py` | Standalone grammar-fit evidence gate |
| `face_reader.py` | Grammar-driven face-page extraction |
| `extract_state_grids.py` | State schedule grid extraction |
| `build_line_item_details.py` | Printed line-item detail fragment |
| `reconcile_line_item_details.py` | Face-versus-detail reconciliation |
| `phase4_assemble.py` | Five-fragment OTD assembly |
| `constraint_engine.py` | Taxonomy compilation and generic constraints |
| `validate_otd.py` | Document and taxonomy validation |

Additional scripts support diagnostics, section analysis, mini-taxonomy generation, and reviewed patch application. Inspect each script's `--help` or source contract before use.

## Review and delivery checklist

Before delivering an OTD document:

- Source PDF and SHA-256 are recorded.
- Grammar syntax passes.
- Template-fit evidence supports a unique match.
- No classified section is unresolved without review.
- All five assembler fragments exist and are structurally valid.
- Assembly completes and writes the confidence manifest.
- Validator exits `0` against the intended taxonomy.
- Reconciliation passes when detail tables exist.
- Every warning and `_unverified` item is reviewed and documented.
- No source value was invented, silently dropped, or converted from unknown to false/zero.
- Output artifacts and logs are retained.

## Limits and next work

The highest-value next steps are:

- build portable, redistributable PDF fixtures;
- add grammars for additional form years and preparer layouts;
- integrate the template-fit gate into an explicit orchestrator;
- add OCR and rotation/skew handling with provenance;
- broaden overflow, footnote, state, and K-3 corpus coverage;
- add cross-implementation OTD conformance tests.

## References

- Agent operating contract: [SKILL.md](SKILL.md)
- Grammar format: [grammars/GRAMMAR-FORMAT.md](grammars/GRAMMAR-FORMAT.md)
- Parser specification: [reference/otd-parser-spec.md](reference/otd-parser-spec.md)
- Emitter specification: [reference/otd-emitter-spec.md](reference/otd-emitter-spec.md)
- Footnote taxonomy: [reference/otd-footnote-taxonomy.md](reference/otd-footnote-taxonomy.md)
- License: [../../LICENSE](../../LICENSE)
