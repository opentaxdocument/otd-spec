# Open Tax Document (OTD) — Executive Summary

**An Open Standard for AI-Native Tax Document Representation**

**Version:** 0.2 RC-1 | **Date:** April 2, 2026
**Authors:** Tom O'Sullivan, Crimson Tree Software
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Status:** Public draft seeking multi-firm review; not conformance-certified
**Review update:** September 21, 2026

---

## The Problem

Every year, millions of Schedule K-1s are produced as PDFs, then
re-ingested by downstream tax preparers, compliance systems, and
advisory teams. This print-first workflow creates repeated extraction,
reconciliation, and review work across the partnership tax ecosystem:

- **Ingestion failures** from unstructured overflow statements, footnotes,
  and complex attachments (Form 926, state grids, K-3 cross-references)
- **Fragmented interchange**: downstream workflows often rely on proprietary
  parsers that break when formatting changes
- **Round-trip data loss** — emit to PDF, re-key or OCR back in, pray
  the numbers match
- **No semantic context** — a dollar amount in a PDF carries no information
  about what it *means*, where it *came from*, or how it *relates* to
  other tax data

The industry's current approach: build better OCR. That's a band-aid
on a broken architecture.

## The Solution: Open Tax Document (OTD)

OTD is an open, AI-native standard for representing structured tax data
in YAML. It is designed so that:

1. **The document carries its context**: reported nodes carry semantic
   identity, form location, and structure. A separately versioned taxonomy
   supplies the governing declarations and validation rules.

2. **Footnotes and overflow are first-class citizens** — Form 926, state
   K-1 grids, §199A detail, and any complex attachment are represented as
   structured, queryable objects — not text blobs appended to a PDF.

3. **Designed for software, people, and AI tools**: an LLM, a rules engine, or a
   human reviewer can traverse the document by *meaning* ("what is the
   QBI?") or by *form position* ("what's in Box 20, Code Z?").

4. **Canonical round-trip fidelity is a design requirement** — an
   implementation should preserve information across parse and re-emission
   under a declared normalization profile. The bundled proof demonstrates
   normalized equivalence for one structured K-1 fixture, not universal
   conformance or PDF extraction.

5. **Form-production metadata is modelled** — nodes can carry box, line, code,
   and column context intended to support renderers. A general printed-form or
   e-file production engine is not yet demonstrated.

## What's In This Package

| Document | Purpose |
|----------|---------|
| `spec/otd-spec-v0.2.yaml` | **Core TaxNode specification** — the six TaxNode primitives (scalar, coded, grid, recordset, statement, reference), document envelope, and real examples |
| `taxonomies/irs-k1-1065-2025.yaml` | **Draft K-1 taxonomy** — broad 2025 box/code coverage derived from IRS instructions and still awaiting practitioner validation |
| `spec/otd-derivation-spec.md` | **Taxonomy derivation process**: AI-assisted drafting from IRS instructions, followed by qualified professional review and versioned publication |
| `spec/otd-emitter-spec.md` | **Emitter guide** — how to build software that produces OTD documents |
| `spec/otd-parser-spec.md` | **Parser guide** — how to build software that consumes OTD documents |
| `proof/otd_round_trip_proof.py` | **Bounded proof-of-concept** — starts from an in-memory structured K-1 fixture; it does not test PDF extraction or the production assembler |
| `proof/proof-emitted.otd.yaml` | **Example output** — a realistic synthetic K-1 OTD document with §199A, Form 926, and multi-code boxes |
| `proof/proof-results.txt` | **Proof snapshot** — five bounded operations passed, including normalized serialization equivalence |

### Current Assurance Boundary

The tracked validator matrix has 34 blocking cases and no deferred cases.
PDF extraction regression coverage uses two repository-local 2025 K-1 fixtures:
an official blank IRS form and an approved synthetic 27-page package. The
diagnostic renderer produces color-coded page evidence and a portable index.

The synthetic PDF is a stress fixture with mixed-year supplements and
inconsistent figures, not a model tax return. Its bounded demonstration
checks extraction fidelity, not the tax correctness of the source package.

The project is suitable for draft design and controlled human-reviewed
experiments, but this bounded corpus does not certify unattended extraction,
all preparer layouts, or general OTD conformance.

## Key Design Decisions

### Dual Identity System
Every data point has two identities:
- **Semantic** — what it means (`ordinary_business_income`)
- **Form** — where it prints (`Part III, Box 1`)

This lets consuming software locate data by meaning or form position.
It does not certify an AI system's tax conclusions or provide a demonstrated
general PDF/e-file renderer.

### Making "Other" Discoverable
In coded K-1 boxes, Code ZZ ("Other") is a catch-all that often produces
unstructured PDF text. In OTD, Code ZZ entries carry a **mandatory
classification** field, turning the catch-all into a discoverable,
queryable structured object.

### Ontology-Derivable, Not Ontology-Dependent
OTD separates the reusable document model from form- and year-specific
taxonomies. Derivation rules help maintainers draft those taxonomies from
IRS instructions. New forms, years, and legislation require renewed source
review, professional validation, and versioned publication, not just an
unreviewed AI rerun.

### Portable Structure, Explicit Value Preservation
OTD is YAML-native and defines projection conventions for other structured
formats. Implementations must preserve exact financial values, ordering,
nulls, and unknown extension content. The reference tools use Decimal-aware
YAML and JSON handling; a general XML implementation is not demonstrated.

## What We're Asking

We are seeking **review and co-development** from firms with deep
partnership tax expertise. Specifically:

1. **Coverage review** — Does the K-1 taxonomy capture every code your
   teams encounter in practice? What edge cases are missing?

2. **Statement taxonomy** — What are the most common complex footnotes
   and attachments you see? (Form 926, state grids, FIRPTA notices,
   §754 adjustments, etc.) The draft catalog contains 20 concrete types
   plus `custom`; we need practitioners to test their field sufficiency.

3. **Adoption interest** — Would your firm's compliance tools benefit
   from emitting and/or consuming OTD? What would adoption look like?

4. **K-3 implementation stress test** — A draft K-3 taxonomy exists, but
   it has no proof, fixture, or validator exercise. Implementation evidence is
   the next milestone.

## Why Open Standard?

- **No vendor lock-in** — any firm can implement emitters and parsers
- **Interoperability by design**: shared semantics and versioned taxonomies
  aim to reduce integration work; independent implementation parity remains a goal.
- **Structured-data alignment**: a potential input to future integrations,
  not an IRS filing format, approval, endorsement, or certification
- **Network effects** — the more firms adopt, the less OCR everyone needs

## Contact

Tom O'Sullivan | Crimson Tree Software
tom@crimsontreesoftware.com

---

*"The best time to standardize tax data was twenty years ago. The second
best time is now."*
