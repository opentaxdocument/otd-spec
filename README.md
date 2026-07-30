
# Open Tax Document (OTD)

**An Open Standard for AI-Native Structured Tax Data**

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Status: Draft](https://img.shields.io/badge/Status-Draft-orange.svg)]()

> **This project is a public draft.** Specifications, taxonomies, and the
> reference implementation are all under active development and change
> without notice. We build in the open, including the parts that are still
> rough. Open items are listed in [Known Gaps](#known-gaps) rather than
> quietly omitted.

---

## The Problem

Every year, millions of Schedule K-1s are produced as PDFs, then re-ingested
by downstream tax preparers, compliance systems, and advisory teams. This
print-first workflow creates massive friction:

- **Ingestion failures** from unstructured overflow statements, footnotes,
  and complex attachments (Form 926, state grids, K-3 cross-references)
- **No machine-readable standard** — every firm builds proprietary parsers
  that break when formatting changes
- **Round-trip data loss** — emit to PDF, OCR back in, and hope the numbers
  match
- **No semantic context** — a dollar amount in a PDF carries no information
  about what it *means* or how it *relates* to other tax data

The industry's current approach: build better OCR. That's a band-aid on a
broken architecture.

## The Solution

OTD is an open, YAML-based standard for representing structured tax data. It
is designed so that:

1. **The document IS the schema** — every data point carries its own semantic
   identity, form location, and structural context
2. **Footnotes are first-class citizens** — Form 926, state K-1 grids, §199A
   detail, GILTI, PTEP, and other footnote types are represented as
   structured, queryable objects — not text blobs
3. **Any AI agent can reason about it** — traverse by *meaning* ("what is the
   QBI?") or by *form position* ("what's in Box 20, Code Z?")
4. **Canonical round-trip fidelity is a design requirement** — supported
   implementations must preserve information and reproduce equivalent normalized
   YAML. The current proof demonstrates this for one structured K-1 fixture,
   not for every valid OTD document or for PDF extraction.
5. **Form-production metadata is modelled** — nodes can carry form placement,
   but a general PDF or e-file renderer is not yet demonstrated.
6. **The taxonomy is a governing validation input** — the reference validator
   fails closed unless it can load an operative taxonomy matching the document.
   Remaining rule and schema gaps are listed below.


## What An OTD Document Looks Like

Excerpts below are copied verbatim from `proof/proof-emitted.otd.yaml`.
Ellipsis comments mark elided fields; nothing is reworded.

**Envelope and a scalar node.** Every value carries both its *meaning*
(`semantic.id`) and its *place on the form* (`form.location`):

```yaml
otd:
  version: '0.1'
  document_id: 77455c78-08dd-5a66-a288-0575d8de0603
  taxonomy:
    id: irs-k1-1065-2025
    version: 2025.1.0
    source: Partner's Instructions for Schedule K-1 (Form 1065), 2025

form_metadata:
  tax_year: 2025
  amended: false
  filing_status: original
  # ... fiscal-year and amendment-chain fields elided

body:
  form_id: k1-1065
  tax_year: 2025
  part_i:
    item_a:
      type: scalar
      semantic:
        id: partnership.ein
        label: Partnership's EIN
      form:
        form_id: k1-1065
        location: Part I, Item A
        box: A
      value: XX-XXXX567
```

**A coded node with an attached statement.** This is the case that breaks
PDF-based workflows. On the printed form, Box 20 Code Z says little more than
"see attached." Here the face value is `null` and the substance is a
structured, queryable statement:

```yaml
- code: Z
  semantic:
    id: other_information.section_199a
    label: Section 199A information
  value:
  statement:
    type: statement
    semantic:
      id: stmt_section_199a_detail
      label: Section 199A Detail
      classification: section_199a_detail
      role: investor_footnote
    form:
      attachment: true
    content:
      qbi: 150000.0
      w2_wages: 80000.0
      ubia: 500000.0
      sstb: false
      business_name: Greenfield Operations LLC
      section_199a_dividends:
      patron_reduction:
```

Two details worth noting. `value:` is **null, not zero** — the face box is
genuinely empty, and OTD never substitutes a plausible default for an unknown
or absent fact. And `section_199a_dividends` and `patron_reduction` are
present-but-null: declared by the taxonomy, reported as empty, distinguishable
from fields that were never extracted at all.

## What's In This Repository

```
otd-spec/
├── spec/                                 # Core specifications
│   ├── otd-spec-v0.2.yaml                # TaxNode spec (6 primitives, type system)
│   ├── otd-parser-spec.md                # Parser guide + validation profile
│   ├── otd-emitter-spec.md               # Emitter implementation guide
│   ├── otd-derivation-spec.md            # Taxonomy derivation rules
│   ├── otd-footnote-taxonomy.md          # Footnote classifications
│   └── otd-extension-registry-spec.md    # Extension governance
├── taxonomies/                           # Derived taxonomies
│   ├── irs-k1-1065-2025.yaml             # K-1 (Form 1065) 2025 — 200+ codes
│   └── irs-k3-1065-2025.yaml             # K-3 (Form 1065) 2025
├── proof/                                # Round-trip proof of concept
│   ├── otd_round_trip_proof.py           # Emitter + parser + validator + query
│   ├── proof-emitted.otd.yaml            # Example K-1 OTD document
│   └── proof-results.txt                 # Latest run output
├── skills/k1-otd/                        # Reference extraction pipeline
│   ├── SKILL.md                          # Extraction contract (PDF → OTD)
│   ├── README.md                         # Operator guide
│   ├── reference/                        # Mirrored specs + taxonomy (LOAD FIRST)
│   └── scripts/
│       ├── extract_pdf_text.py        # PDF → page text
│       ├── build_section_manifests.py            # Page classification
│       ├── assemble_otd.py            # Fragments → OTD document
│       ├── constraint_engine.py          # Taxonomy-driven validation engine
│       ├── validate_otd.py               # Validator CLI
│       ├── extract_state_grids.py        # State schedule extraction
│       └── state_grid_parsers.py
├── tests/                                # 33 tracked cases, three suites
│   ├── test_constraint_engine.py         # Engine-level regressions
│   ├── test_rectification.py             # End-to-end via production pipeline
│   ├── test_direct_documents.py          # Hand-mutated documents → validator
│   ├── check_mirrors.py                  # Enumerated mirror parity check
│   └── fixtures/hostile-k1/              # Deliberately non-conforming input
├── docs/
│   ├── OTD-Executive-Summary.md
│   └── OTD-v03-Roadmap.md
└── extensions/                           # Firm-published schema extensions
```

## Quick Start

**Requires Python 3.9 or later.**

```bash
pip install ruamel.yaml
```

### Run the round-trip proof

```bash
python proof/otd_round_trip_proof.py
```

Runs a bounded structured-data proof for one in-memory K-1 fixture:
emit YAML, parse it, exercise selected taxonomy constraints and queries, and
compare the re-emission after trailing-whitespace and final-newline
normalization. It does not exercise PDF extraction or the production assembler,
and it is not a universal conformance proof.

### Validate a document

```bash
python skills/k1-otd/scripts/validate_otd.py --input proof/proof-emitted.otd.yaml
```

The taxonomy is located automatically. If it cannot be resolved, loaded, or
matched to the document, validation fails closed. CLI exit codes are stable:

| Exit | Meaning |
|---:|---|
| `0` | Document passed the implemented conformance checks |
| `1` | Document is invalid |
| `2` | Validator, taxonomy, dependency, or configuration failure |

### Run the test suites

```bash
python -B tests/test_validator_contract.py   # 34 blocking validator contracts
python -B tests/test_constraint_engine.py    # engine-level rule regressions
python -B tests/test_rectification.py        # assembler → validator regression matrix
python -B tests/test_direct_documents.py     # direct malformed-document mutations
python -B tests/test_face_reader.py          # two repository-local PDF fixtures
python -B tests/test_workflow_contract.py     # portable extraction workflow contracts
python -B tests/test_diagnostic_renderer.py
python -B tests/check_mirrors.py             # reference/ mirror parity
```

`test_validator_contract.py` has no deferred cases. An unexpected change fails
the suite instead of disappearing into an `XFAIL`. The face-reader suite uses
the repository-local blank IRS form and approved synthetic K-1 example.

### Synthetic incomplete-aware extraction example

`examples/k1-1065-2025-synthetic/` contains an approved fictitious K-1 package
and current-source extraction evidence. Its machine-readable status and review
ledger distinguish present facts, verified absence, blanks, missing values,
unimplemented readers, unresolved sections, and the state-grid escalation.

The example intentionally does **not** contain `output.otd.yaml`: current face
evidence is not a normalized assembler fragment, and assembly, reconciliation,
and OTD conformance were not claimed.

### Render extraction diagnostics

`skills/k1-otd/scripts/render_extraction_diagnostics.py` renders each PDF page
with labeled, color-coded evidence and classification boxes. Items without
defensible geometry remain visible in a warning/error sidebar, and
`diagnostic-index.json` preserves the same result in portable machine-readable
form.

```bash
python -B skills/k1-otd/scripts/render_extraction_diagnostics.py \
  --pdf examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf \
  --face-evidence artifact-root/face_page.json \
  --page-manifest artifact-root/page_manifest.json \
  --section-manifest artifact-root/section_manifest.json \
  --text-dir artifact-root/text_blocks \
  --out artifact-root/diagnostics
```

The renderer contract is exercised by
`tests/test_diagnostic_renderer.py`.

### Reproduce the hostile fixture

```bash
python skills/k1-otd/scripts/assemble_otd.py \
  --fragments tests/fixtures/hostile-k1 --out /tmp/hostile.otd.yaml
python skills/k1-otd/scripts/validate_otd.py --input /tmp/hostile.otd.yaml
```

Assembly succeeds; validation **fails** on a missing Box 20 Code X
payment-obligation statement. See `tests/fixtures/README.md`.

## Validation Model

Parsing and validation have deliberately opposite dispositions
(`spec/otd-parser-spec.md` §4.1.1):

| Phase | Disposition | Governs |
|---|---|---|
| **Parse** | Lenient — MUST NOT reject | Unknown nodes preserved; forward compatibility |
| **Validate** | Strict — SHOULD reject | Conformance of *declared* nodes to the taxonomy |

Before rule evaluation, `validate_otd.py` now requires the OTD envelope,
legal metadata, physical body identity, and a non-empty governing taxonomy. It
binds taxonomy ID, taxonomy version, form ID, and tax year, and distinguishes an
invalid document (`1`) from a validator/configuration failure (`2`).

`constraint_engine.py` then applies the taxonomy-driven checks it currently
implements, including arithmetic/range rules, schema binding and physical
completeness, statement requirements, coded-entry uniqueness, and
capital-account integrity. `tests/test_validator_contract.py` currently runs
34 blocking cases with no deferred cases.

That green matrix is evidence for the implemented checks, not universal
conformance. Extension-registry interoperability, broad recursive taxonomy
coverage, cross-implementation parity, and extraction across additional years
and layouts remain open.

The §7.1 reference profile is proposed for promotion to normative in v0.3 so
third-party validators can implement the same dispositions.

## Design Highlights

### Six TaxNode Primitives

| Primitive | Description | Example |
|-----------|-------------|---------|
| **Scalar** | Single typed value | Box 1: Ordinary Income = $150,000 |
| **Coded** | Value qualified by letter code | Box 11, Code A: Portfolio Income |
| **Grid** | Named matrix with fixed axes | K-3 Part II FTC table |
| **RecordSet** | Variable-length entity records | K-3 Part VII PFICs |
| **Statement** | Structured overflow / attachment | Form 926, state grids |
| **Reference** | Pointer to another document | Box 16 → Schedule K-3 |

Footnotes are statement nodes with `semantic.role: investor_footnote` — not a
seventh primitive.

### Investor-Level Footnote Types

Domestic: §754 adjustments, debt allocation, §1061 carried interest, UBTI,
passive activity grouping, §199A, Form 926, NII, §163(j)

International: Subpart F, GILTI (with §861 K-3 cross-reference), PTEP (with
§959(c) annual layering), PFIC/QEF elections, ECI, FDAP withholding

Corporate: Dividends Received Deduction (§243/§245/§245A with §246A reduction)

State: PTE tax elections (with tiered entity pass-through), nonresident
withholding (with exemption status)

Every footnote has two mandatory layers: **structured fields** for
computation and **source text** for legal defensibility.

### Extension Registry

Firms can publish named schema extensions for custom footnote types.
Extensions are validated, air-gap safe (local bundling mandatory for
production), and follow a governed promotion path to the core catalog.

## For AI Coding Agents

1. **Comprehension:** Give the agent `spec/otd-spec-v0.2.yaml` and
   `spec/otd-footnote-taxonomy.md`. Ask it to summarize the six primitives
   and the footnote model.
2. **Build an emitter:** Provide `spec/otd-emitter-spec.md` +
   `taxonomies/irs-k1-1065-2025.yaml` + your source data format.
3. **Build a parser:** Provide `spec/otd-parser-spec.md` +
   `proof/proof-emitted.otd.yaml`. Read §4.1.1 and §7.1 before implementing
   validation — parse leniency and validation strictness are separate.
4. **Extract footnotes from PDFs:** Give the agent
   `spec/otd-footnote-taxonomy.md` and a real K-1 PDF package.

## Known Gaps

Honest status of open items:

| Gap | Impact |
|---|---|
| Validator coverage is broader but still bounded | The blocking contract matrix covers current trust boundaries and known counterexamples; it is not a substitute for cross-implementation conformance testing. |
| The template-fit checker is not wired into a single extraction orchestrator | Callers can invoke face extraction without first proving form/year fit. |
| Face extraction ships one 2025 grammar and two repository-local PDF fixtures | The blank IRS form and approved synthetic package make regression portable, but vendor, year, skew/rotation, scan, corruption, and hybrid AcroForm breadth is not established. |
| K-3 has a taxonomy but no proof, fixture, or implementation exercise | K-3 support is declarative only. |
| Validation rewrites an adjacent `output.confidence.json` when present | Validation has an implicit write side effect that should become opt-in. |
| The §7.1 validation profile is implementation-specific, not yet normative | A conforming third-party validator may be more permissive. |

## Why Open Standard?

- **No vendor lock-in** — any firm can implement emitters and parsers
- **AI-agent interoperability** — K-1 data produced by one system can be
  consumed by any other
- **Regulatory alignment** — ready for IRS modernization (MeF, API-first filing)
- **Network effects** — the more firms adopt, the less OCR everyone needs

## Contributing

We are seeking review and co-development from firms with partnership tax
expertise:

- **Coverage review** — Does the K-1 taxonomy cover every code your teams
  encounter?
- **Footnote schemas** — What structured fields does your team need for each
  footnote type?
- **Validation profile** — Are the §7.1 rules correct? Should they be
  normative?
- **Adoption interest** — What would your firm need to emit or consume OTD?

Open an [Issue](https://github.com/opentaxdocument/otd-spec/issues) or start a
[Discussion](https://github.com/opentaxdocument/otd-spec/discussions).

## License

Creative Commons Attribution 4.0 International (CC BY 4.0)

## Contact

Tom O'Sullivan | Crimson Tree Software | tom@crimsontreesoftware.com
