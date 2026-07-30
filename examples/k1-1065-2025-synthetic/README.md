# Synthetic 2025 K-1 bounded PDF-to-OTD demonstration

This directory contains an approved, fictitious Schedule K-1 package for
portable extraction tests, human-review diagnostics, and a source-dependent
bounded OTD demonstration.

## Source

- File: [`source/synthetic-k1.pdf`](source/synthetic-k1.pdf)
- Pages: 27
- SHA-256:
  `db6e9764726d0268e6288774b3ffc508faa829f9b5cee770e26262e26ed75fac`
- Privacy: all names, addresses, and identifiers are synthetic. The package is
  intentionally suitable for publication and regression testing.

## Claim boundary

This is a successful **bounded PDF-to-OTD** example for profile
`k1-1065-2025-face-and-numeric-details/1.0`. The generated OTD contains resolved
face facts, taxonomy-safe numeric detail totals, and one transparent
taxonomy-declared derivation. It passes the production assembler, validator,
and supported-profile reconciliation gate.

It is not a complete-document extraction. Supplemental statements, state
grids, classification-dependent facts, and four unresolved logical sections
are excluded and recorded in `disposition-ledger.json`; they are never
defaulted or inferred. The ledger also distinguishes detail sections whose
safe numeric total was consumed while their components remain excluded.

At the current parser revision, the face artifact reports:

- 39 present fields;
- 9 verified-absent fields;
- 4 blank fields;
- 1 field absent from the form revision;
- 0 unresolved fields;
- 0 unimplemented readers;
- 0 present fields without bounding-box evidence.

The page/section classifier still records four explicit review outcomes, and
state-grid extraction escalates because the current deterministic worker needs
layout evidence beyond its implemented contract. Those outcomes are successful
fail-closed behavior, not fabricated completion.

## Run the bounded demonstration

Run from the repository root:

```bash
python -B examples/k1-1065-2025-synthetic/run_demo.py \
  --out artifact-root/synthetic-demo \
  --created 2026-07-30T00:00:00Z
```

The runner reads the PDF, grammar, taxonomy, and repository tools. It does not
read the committed `evidence/`, `expected/`, `diagnostics/`, or prior
demonstration output. Successful publication produces:

- `output.otd.yaml` and `output.confidence.json`;
- `projection-manifest.json` and `disposition-ledger.json`;
- `reconciliation.json`;
- five explicit assembler fragments under `fragments/`;
- `extraction-status.json`, with the bounded claim boundary;
- `run-manifest.json`, with source, grammar, taxonomy, orchestrator, and
  executed-tool hashes.

The committed [`demonstration/`](demonstration/) directory is a reproducible
snapshot generated with the pinned creation time above. The contract test runs
twice from renamed PDF copies, compares deterministic outputs, and verifies
that a different PDF renamed `synthetic-k1.pdf` cannot bypass source-byte
identity.

## Reproduce extraction evidence

Run from the repository root:

```bash
python -B skills/k1-otd/scripts/grammar_validator.py \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml

python -B skills/k1-otd/scripts/extract_pdf_text.py \
  --pdf examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf \
  --out artifact-root

python -B skills/k1-otd/scripts/template_match.py \
  --pdf examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf \
  --grammar skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --outdir artifact-root/template-fit

python -B skills/k1-otd/scripts/build_section_manifests.py \
  --index artifact-root/text_blocks/page_index.json \
  --text-dir artifact-root/text_blocks \
  --out artifact-root/page_manifest.json \
  --section-out artifact-root/section_manifest.json

python -B skills/k1-otd/scripts/face_reader.py \
  examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf \
  skills/k1-otd/grammars/k1-1065-2025.grammar.yaml \
  --out artifact-root/face_page.json
```

`face_page.json` is an evidence envelope, not an assembler fragment. The
bounded runner passes it through `project_extraction_evidence.py`; feeding the
raw envelope directly to `assemble_otd.py` remains a hard error.

## Render review diagnostics

```bash
python -B skills/k1-otd/scripts/render_extraction_diagnostics.py \
  --pdf examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf \
  --face-evidence artifact-root/face_page.json \
  --page-manifest artifact-root/page_manifest.json \
  --section-manifest artifact-root/section_manifest.json \
  --text-dir artifact-root/text_blocks \
  --out artifact-root/diagnostics
```

The renderer emits one labeled, color-coded PNG per page plus
`diagnostic-index.json`. Evidence without defensible geometry remains visible in
the page sidebar.

## Included evidence package

The committed `evidence/` package preserves the earlier extraction-only
snapshot and remains self-contained and reviewable:

- [`evidence/source-sha256.txt`](evidence/source-sha256.txt) — immutable source identity
- [`evidence/commands.json`](evidence/commands.json) — capability-based reproduction commands
- [`evidence/extraction-status.json`](evidence/extraction-status.json) — final status counts and claim boundaries
- [`evidence/template-fit-report.json`](evidence/template-fit-report.json) — unique grammar-fit evidence
- [`evidence/page-index.json`](evidence/page-index.json) — portable per-page text index
- [`evidence/page-manifest.json`](evidence/page-manifest.json) — page projection
- [`evidence/section-manifest.json`](evidence/section-manifest.json) — 95 logical-section records
- [`evidence/face-page.json`](evidence/face-page.json) — evidence-enveloped face facts
- [`evidence/line-item-details.json`](evidence/line-item-details.json) — 21 matched detail blocks
- [`evidence/state-grid-attempt.json`](evidence/state-grid-attempt.json) — explicit state-grid escalation
- [`evidence/review-ledger.json`](evidence/review-ledger.json) — unresolved-section and escalation dispositions
- [`expected/expected-face-facts.json`](expected/expected-face-facts.json) — manually verified regression subset
- [`diagnostics/diagnostic-index.json`](diagnostics/diagnostic-index.json) — portable annotation index

The bounded OTD snapshot adds:

- [`demonstration/output.otd.yaml`](demonstration/output.otd.yaml);
- [`demonstration/output.confidence.json`](demonstration/output.confidence.json);
- [`demonstration/projection-manifest.json`](demonstration/projection-manifest.json);
- [`demonstration/disposition-ledger.json`](demonstration/disposition-ledger.json);
- [`demonstration/reconciliation.json`](demonstration/reconciliation.json);
- [`demonstration/extraction-status.json`](demonstration/extraction-status.json);
- [`demonstration/run-manifest.json`](demonstration/run-manifest.json);
- [`demonstration/fragments/`](demonstration/fragments/) — all five assembler inputs.

Annotated page images:

- [`page_001.png`](diagnostics/page_001.png), [`page_002.png`](diagnostics/page_002.png), [`page_003.png`](diagnostics/page_003.png), [`page_004.png`](diagnostics/page_004.png), [`page_005.png`](diagnostics/page_005.png), [`page_006.png`](diagnostics/page_006.png), [`page_007.png`](diagnostics/page_007.png), [`page_008.png`](diagnostics/page_008.png), [`page_009.png`](diagnostics/page_009.png)
- [`page_010.png`](diagnostics/page_010.png), [`page_011.png`](diagnostics/page_011.png), [`page_012.png`](diagnostics/page_012.png), [`page_013.png`](diagnostics/page_013.png), [`page_014.png`](diagnostics/page_014.png), [`page_015.png`](diagnostics/page_015.png), [`page_016.png`](diagnostics/page_016.png), [`page_017.png`](diagnostics/page_017.png), [`page_018.png`](diagnostics/page_018.png)
- [`page_019.png`](diagnostics/page_019.png), [`page_020.png`](diagnostics/page_020.png), [`page_021.png`](diagnostics/page_021.png), [`page_022.png`](diagnostics/page_022.png), [`page_023.png`](diagnostics/page_023.png), [`page_024.png`](diagnostics/page_024.png), [`page_025.png`](diagnostics/page_025.png), [`page_026.png`](diagnostics/page_026.png), [`page_027.png`](diagnostics/page_027.png)

## Focused contracts

```bash
python -B tests/test_face_reader.py
python -B tests/test_workflow_contract.py
python -B tests/test_diagnostic_renderer.py
python -B tests/test_synthetic_pdf_to_otd.py
```
