---
name: k1-otd
description: Extract, inspect, assemble, repair, and validate Schedule K-1 (Form 1065) packages as Open Tax Document YAML with the repository's staged 2025 grammar-based toolkit. Use when a user provides a K-1 PDF or extraction artifacts, asks for OTD K-1 output, needs package-page classification, face/detail reconciliation, taxonomy validation, or repair of invalid K-1 OTD. Current deterministic face support is 2025-only; ambiguous layouts and unsupported documents require explicit review.
---

# K-1 OTD operating contract

Use this skill for Schedule K-1 (Form 1065) extraction and OTD validation work.

The implementation is a staged toolkit. Deterministic scripts handle text
extraction, section classification, grammar/geometry-based face reading,
projection, assembly, reconciliation, and validation. A one-command path exists
only for the approved bounded synthetic profile. Overflow statements, footnotes,
unsupported layouts, and ambiguous evidence still require agent judgment.

Read [README.md](README.md) for architecture, setup, script descriptions, evidence, and limitations.

## Scope

### Use this skill when

- A user provides a Schedule K-1 (Form 1065) PDF or package.
- A user asks to convert K-1 content to OTD YAML.
- Existing K-1 extraction fragments need assembly or repair.
- An OTD K-1 document needs taxonomy validation.
- Package pages or logical sections need classification.
- Face values must be reconciled to line-item details.
- A K-1 grammar or template fit needs inspection.

### Do not imply

- general-purpose one-command PDF-to-OTD orchestration beyond a declared profile;
- general support for every preparer, form year, scan, rotation, or skew;
- broad K-3 extraction coverage;
- that warnings are equivalent to conformance;
- that the two-document face-reader regression corpus proves generality.

## Required inputs

Obtain or establish:

- source PDF path;
- writable artifact root;
- source PDF SHA-256;
- expected document family and tax year;
- governing taxonomy path;
- intended grammar path.

Default current assets:

```text
Grammar:  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml
Taxonomy: taxonomies/irs-k1-1065-2025.yaml
```

If the document is not a 2025 Schedule K-1 (Form 1065), stop and state that the current deterministic face grammar does not establish support.

## Artifact discipline

- Keep the source PDF immutable.
- Write all generated text, fragments, reports, logs, and outputs beneath one artifact root.
- Record every script invocation and exit code.
- Preserve source text and evidence for extracted statements.
- Never put credentials, taxpayer identifiers beyond the supplied source, or unrelated files into logs.
- Treat generated fragments as reviewable evidence, not disposable scratch.

Recommended shape:

```text
artifact-root/
├── source/
├── text_blocks/
├── fragments/
├── template-fit/
├── reports/
├── output.otd.yaml
└── output.confidence.json
```

## Mandatory workflow

### 1. Intake and source integrity

1. Confirm the PDF exists and is the intended taxpayer/package.
2. Compute and record SHA-256.
3. Identify tax year, form family, page count, and whether a usable text/geometry layer exists.
4. If image-only, rotated, skewed, or otherwise unsupported, stop for an explicit OCR/normalization plan. Preserve provenance.

### 2. Extract text

```bash
python skills/k1-otd/scripts/extract_pdf_text.py \
  --pdf path/to/package.pdf \
  --out artifact-root
```

Expected outputs include `text_blocks/page_NN.txt` and `text_blocks/page_index.json`.

### 3. Validate and fit the grammar

```bash
python skills/k1-otd/scripts/grammar_validator.py \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml
```

```bash
python skills/k1-otd/scripts/template_match.py \
  --pdf path/to/package.pdf \
  --grammar skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --outdir artifact-root/template-fit
```

Inspect `template_fit_report.json`. The fit gate is standalone and returns success after writing a report; its return code is not the fit verdict.

Proceed only when:

- the document is evaluable;
- there is no ambiguous tie;
- one grammar has verdict `match`.

Stop for `partial`, `mismatch`, `unverified`, no recommendation, or ambiguity.

### 4. Classify logical sections

```bash
python skills/k1-otd/scripts/build_section_manifests.py \
  --index artifact-root/text_blocks/page_index.json \
  --text-dir artifact-root/text_blocks \
  --out artifact-root/fragments/page_manifest.json \
  --section-out artifact-root/fragments/section_manifest.json
```

Logical-section classification is the supported path. Review every unresolved section before continuing.

### 5. Produce extraction fragments

Face page:

```bash
python skills/k1-otd/scripts/face_reader.py \
  path/to/package.pdf \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --out artifact-root/evidence/face_page.json
```

`evidence/face_page.json` is the face reader's evidence envelope, not the
normalized `face_page.json` consumed by `assemble_otd.py`. The
`project_extraction_evidence.py` bridge supports the declared synthetic
face-and-safe-numeric-detail profile and emits projection and omission
manifests with all five assembler fragments.

For any other package, keep raw face evidence under `evidence/` and stop before
assembly unless a supported profile or separately reviewed normalized
projection exists. Never feed the raw envelope directly to the assembler; its
hard rejection of that shape is an intentional no-silent-data-loss boundary.

State schedules, when present:

```bash
python skills/k1-otd/scripts/extract_state_grids.py \
  --input-dir artifact-root/text_blocks \
  --manifest artifact-root/fragments/page_manifest.json \
  --out artifact-root/fragments/state_schedules.json
```

Line-item details, when present:

```bash
python skills/k1-otd/scripts/build_line_item_details.py \
  --pages artifact-root/text_blocks \
  --grammar skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --out artifact-root/fragments/line_item_details.json
```

Produce overflow and footnote fragments through evidence-backed review of the classified source sections. Preserve source text, form location, classifications, and uncertainty.

Before assembly, a separately reviewed normalized fragment directory must contain:

```text
face_page.json
overflow_statements.json
footnotes_a.json
footnotes_b.json
state_schedules.json
```

### 6. Assemble

```bash
python skills/k1-otd/scripts/assemble_otd.py \
  --fragments artifact-root/fragments \
  --out artifact-root/output.otd.yaml \
  --sha256 SOURCE_PDF_SHA256
```

Require both the OTD YAML and confidence manifest.

### 7. Validate

```bash
python skills/k1-otd/scripts/validate_otd.py \
  --input artifact-root/output.otd.yaml \
  --taxonomy taxonomies/irs-k1-1065-2025.yaml
```

Interpret exit codes exactly:

- `0`: passed implemented conformance checks;
- `1`: invalid document;
- `2`: validator or taxonomy could not operate safely.

Never report RC `2` as a document failure or success. Repair configuration/taxonomy issues, then rerun.

### 8. Reconcile details

If `line_item_details.json` exists:

```bash
python skills/k1-otd/scripts/reconcile_line_item_details.py \
  --otd artifact-root/output.otd.yaml \
  --details artifact-root/fragments/line_item_details.json \
  --out artifact-root/reports/line-item-reconciliation.json \
  --posture hard_error
```

Resolve mismatches against source evidence.

### 9. Review warnings and deliver

Create a review ledger for:

- unresolved sections;
- validator warnings;
- `_unverified` markers;
- custom or review-required statement classifications;
- excluded source rows;
- reconciliation exceptions;
- unsupported content.

Do not deliver until every item has a disposition.

## Non-negotiable invariants

- Never guess.
- Unknown is not false and not zero.
- An unobserved fact is `null` plus a non-empty `_unverified` reason.
- Intentional masking uses `_masked: true`.
- `custom` statements require non-empty `content.custom_classification`.
- `unclassified_requires_review` requires non-empty `_unverified`.
- Statements require `form.attachment: true`.
- Box 16 checkbox state and K-3 target/notification references must be consistent.
- Known taxonomy nodes are strict; unknown extension nodes are preserved.
- Never remove source data merely to make validation pass.
- Never bypass a taxonomy/compiler RC `2`.
- Template-fit ambiguity is a stop condition.

## Acceptance gates

For a document delivery:

- source hash recorded;
- grammar valid;
- unique template match supported by evidence;
- no unresolved section without review;
- five assembler fragments present;
- assembly successful;
- validator RC `0`;
- reconciliation passed when applicable;
- warnings and `_unverified` items dispositioned;
- output and confidence manifest retained.

For repository changes, run:

```bash
python -B tests/test_validator_contract.py
python -B tests/test_constraint_engine.py
python -B tests/test_direct_documents.py
python -B tests/test_rectification.py
python -B tests/test_face_reader.py
python -B tests/check_mirrors.py
```

The current contract matrix has 34 blocking cases and zero deferred cases.

`tests/test_face_reader.py` uses two repository-local 2025 PDFs: the official blank IRS form and the approved synthetic package. A missing fixture is a test failure, never a skipped pass.

## Stop and escalate when

- the form year or family does not match available grammar/taxonomy support;
- template fit is not a unique `match`;
- the PDF lacks usable evidence and no approved OCR plan exists;
- section classification is unresolved;
- a source code or statement cannot be classified without guessing;
- a taxonomy fails compilation or identity checks;
- reconciliation does not tie;
- warnings or `_unverified` facts cannot be dispositioned;
- requested claims exceed the evidence.

## Portable example and diagnostics

- [Synthetic 2025 K-1 example](../../examples/k1-1065-2025-synthetic/README.md)
  contains the approved fictitious source and its reproducible extraction claim.
- [Blank IRS K-1 fixture](../../tests/fixtures/pdf/irs-k1-1065-2025-blank.pdf)
  proves abstention and verified absence without machine-local dependencies.
- `scripts/render_extraction_diagnostics.py` renders labeled, color-coded page
  evidence plus a portable diagnostic index.
- `../../tests/test_workflow_contract.py` and
  `../../tests/test_diagnostic_renderer.py` enforce the current workflow and
  renderer contracts.

## References

Consult only as needed:

- [README.md](README.md) — setup, architecture, script map, limits
- [grammars/GRAMMAR-FORMAT.md](grammars/GRAMMAR-FORMAT.md) — grammar contract
- [reference/otd-parser-spec.md](reference/otd-parser-spec.md) — parser and validation rules
- [reference/otd-emitter-spec.md](reference/otd-emitter-spec.md) — authoring rules
- [reference/otd-footnote-taxonomy.md](reference/otd-footnote-taxonomy.md) — statement classifications
- `taxonomies/irs-k1-1065-2025.yaml` — governing K-1 taxonomy
