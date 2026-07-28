
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
4. **Round-trip fidelity is guaranteed** — emit → parse → re-emit produces
   byte-identical output
5. **Form production is built in** — every node carries enough metadata to
   reconstruct the physical form
6. **The taxonomy is normative, not advisory** — validation reads its rules
   from the taxonomy file itself, so a new rule takes effect without new code


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
│       ├── phase1_extract_text.py        # PDF → page text
│       ├── phase2_classify.py            # Page classification
│       ├── phase4_assemble.py            # Fragments → OTD document
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

Emits a realistic K-1 as OTD YAML, parses it back, validates against
taxonomy constraints, demonstrates the query interface, and confirms
byte-identical round-trip fidelity.

### Validate a document

```bash
python skills/k1-otd/scripts/validate_otd.py --input proof/proof-emitted.otd.yaml
```

The taxonomy is located automatically. If it cannot be resolved, validation
**fails** rather than reporting success with rules unenforced.

### Run the test suites

```bash
python tests/test_constraint_engine.py    # engine-level regressions
python tests/test_rectification.py        # end-to-end through the pipeline
python tests/test_direct_documents.py     # malformed documents → validator
python tests/check_mirrors.py             # reference/ mirror parity
```

### Reproduce the hostile fixture

```bash
python skills/k1-otd/scripts/phase4_assemble.py \
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

`constraint_engine.py` reads its rules from the taxonomy YAML rather than
hard-coding them, and enforces:

- **Schema binding** — declared nodes must match their declared type,
  semantic identity, form placement, and value type
- **Physical completeness** — every taxonomy-declared field must be present
- **Constraints** — `sum`, `range`, `required_field`, `conditional_required`,
  with the null/absent algebra of §4.7
- **Statement schemas** — required classification and record fields
- **Coded-entry uniqueness** — no duplicate codes within a box
- **Capital-account integrity** — completeness, alias ambiguity, continuity

Four rules go beyond what the taxonomy can express on its own. They are
documented in `spec/otd-parser-spec.md` §7.1 so third-party implementations
can match, and are proposed for promotion to normative in v0.3.

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
| A misspelled path in a taxonomy constraint resolves as "absent" and silently skips that rule | A typo can disable a rule without any error. Fix in progress: preflight every rule path against the taxonomy; undeclared → error. |
| §4.7's `required` column (null counts as present) is not reconciled with the engine's `required_field` behaviour | Present-null may be treated as missing. Under review. |
| K-3 taxonomy is present but has no proof, fixtures, or validation coverage | K-3 support is declarative only. |
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
