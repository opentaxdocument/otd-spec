# Open Tax Document (OTD)

**An Open Standard for AI-Native Structured Tax Data**

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Status: Release Candidate](https://img.shields.io/badge/Status-RC--1-blue.svg)]()

---

## The Problem

Every year, millions of Schedule K-1s are produced as PDFs, then re-ingested by downstream tax preparers, compliance systems, and advisory teams. This print-first workflow creates massive friction:

- **Ingestion failures** from unstructured overflow statements, footnotes, and complex attachments (Form 926, state grids, K-3 cross-references)
- **No machine-readable standard** — every firm builds proprietary parsers that break when formatting changes
- **Round-trip data loss** — emit to PDF, OCR back in, and hope the numbers match
- **No semantic context** — a dollar amount in a PDF carries no information about what it *means* or how it *relates* to other tax data

The industry's current approach: build better OCR. That's a band-aid on a broken architecture.

## The Solution

OTD is an open, YAML-based standard for representing structured tax data. It is designed so that:

1. **The document IS the schema** — every data point carries its own semantic identity, form location, and structural context
2. **Footnotes are first-class citizens** — Form 926, state K-1 grids, §199A detail, GILTI, PTEP, and 21 other footnote types are represented as structured, queryable objects — not text blobs
3. **Any AI agent can reason about it** — traverse by *meaning* ("what is the QBI?") or by *form position* ("what's in Box 20, Code Z?")
4. **Round-trip fidelity is guaranteed** — emit → parse → re-emit produces byte-identical output
5. **Form production is built in** — every node carries enough metadata to reconstruct the physical form

## What's In This Repository

```
otd-spec/
├── spec/                              # Core specifications
│   ├── otd-spec-v0.2.yaml            # TaxNode specification (6 primitives, type system)
│   ├── otd-derivation-spec.md        # Taxonomy derivation rules
│   ├── otd-emitter-spec.md           # Emitter implementation guide
│   ├── otd-parser-spec.md            # Parser implementation guide
│   ├── otd-footnote-taxonomy.md      # 21 footnote classifications
│   └── otd-extension-registry-spec.md # Extension governance
├── taxonomies/                        # Derived taxonomies
│   └── irs-k1-1065-2025.yaml         # K-1 (Form 1065) 2025 — 200+ codes
├── proof/                             # Working proof-of-concept
│   ├── otd_round_trip_proof.py        # Python proof (emitter + parser + validator)
│   ├── proof-emitted.otd.yaml        # Example K-1 OTD document
│   └── proof-results.txt             # Test results (all 5 phases green)
├── docs/                              # Stakeholder documents
│   ├── OTD-Executive-Summary.md      # One-page overview
│   └── OTD-v03-Roadmap.md           # Partner review roadmap
└── extensions/                        # Firm-published schema extensions
    └── .gitkeep
```

## Quick Start

### Run the Proof-of-Concept

```bash
pip install ruamel.yaml
python proof/otd_round_trip_proof.py
```

This emits a realistic K-1 as OTD YAML, parses it back, validates against taxonomy constraints, demonstrates the query interface, and confirms byte-identical round-trip fidelity. All five phases pass.

### For AI Coding Agents

1. **Comprehension:** Give the agent `spec/otd-spec-v0.2.yaml` and `spec/otd-footnote-taxonomy.md`. Ask it to summarize the six primitives and the footnote model.
2. **Build an emitter:** Provide `spec/otd-emitter-spec.md` + `taxonomies/irs-k1-1065-2025.yaml` + your source data format.
3. **Build a parser:** Provide `spec/otd-parser-spec.md` + `proof/proof-emitted.otd.yaml`.
4. **Extract footnotes from PDFs:** Give the agent `spec/otd-footnote-taxonomy.md` and a real K-1 PDF package. The 21 classification schemas and confidence scoring heuristics guide the extraction.

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

### 21 Investor-Level Footnote Types

Domestic: §754 adjustments, debt allocation, §1061 carried interest, UBTI, passive activity grouping, §199A, Form 926, NII, §163(j)

International: Subpart F, GILTI (with §861 K-3 cross-reference), PTEP (with §959(c) annual layering), PFIC/QEF elections, ECI, FDAP withholding

Corporate: Dividends Received Deduction (§243/§245/§245A with §246A reduction)

State: PTE tax elections (with tiered entity pass-through), nonresident withholding (with exemption status)

Every footnote has two mandatory layers: **structured fields** for computation and **source text** for legal defensibility.

### Extension Registry

Firms can publish named schema extensions for custom footnote types. Extensions are validated, air-gap safe (local bundling mandatory for production), and follow a governed promotion path to the core catalog.

## Why Open Standard?

- **No vendor lock-in** — any firm can implement emitters and parsers
- **AI-agent interoperability** — K-1 data produced by one system can be consumed by any other
- **Regulatory alignment** — ready for IRS modernization (MeF, API-first filing)
- **Network effects** — the more firms adopt, the less OCR everyone needs

## Contributing

We are seeking review and co-development from firms with partnership tax expertise:

- **Coverage review** — Does the K-1 taxonomy cover every code your teams encounter?
- **Footnote schemas** — What structured fields does your team need for each footnote type?
- **Adoption interest** — What would your firm need to emit or consume OTD?

Open an [Issue](https://github.com/opentaxdocument/otd-spec/issues) or start a [Discussion](https://github.com/opentaxdocument/otd-spec/discussions).

## License

Creative Commons Attribution 4.0 International (CC BY 4.0)

## Contact

Tom O'Sullivan | Crimson Tree Software | tom@crimsontreesoftware.com
