# Open Tax Document (OTD) — Executive Summary

**An Open Standard for AI-Native Tax Document Representation**

**Version:** 0.2 RC-1 | **Date:** April 2, 2026
**Authors:** Tom O'Sullivan, Crimson Tree Software
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Status:** Public draft seeking multi-firm review; not conformance-certified

---

## The Problem

Every year, millions of Schedule K-1s are produced as PDFs, then
re-ingested by downstream tax preparers, compliance systems, and
advisory teams. This print-first workflow creates a $400M+ annual
friction cost across the partnership tax ecosystem:

- **Ingestion failures** from unstructured overflow statements, footnotes,
  and complex attachments (Form 926, state grids, K-3 cross-references)
- **No machine-readable standard** — every firm builds proprietary
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

1. **The document IS the schema** — every data point carries its own
   semantic identity, form location, and structural context. No external
   lookup required.

2. **Footnotes and overflow are first-class citizens** — Form 926, state
   K-1 grids, §199A detail, and any complex attachment are represented as
   structured, queryable objects — not text blobs appended to a PDF.

3. **Any AI agent can reason about it** — an LLM, a rules engine, or a
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
| `spec/otd-derivation-spec.md` | **Taxonomy derivation rules** — how to generate a taxonomy for *any* IRS form from its instructions, making the standard self-perpetuating |
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

The project is suitable for draft design and controlled human-reviewed
experiments, but this bounded corpus does not certify unattended extraction,
all preparer layouts, or general OTD conformance.

## Key Design Decisions

### Dual Identity System
Every data point has two identities:
- **Semantic** — what it means (`ordinary_business_income`)
- **Form** — where it prints (`Part III, Box 1`)

This allows AI agents to reason about tax law using semantic paths while
form-production engines use form paths to render PDFs and e-file XML.

### The "ZZ Problem" — Solved
Every K-1 box's Code ZZ ("Other") is a catch-all that currently produces
unstructured PDF text. In OTD, Code ZZ entries carry a **mandatory
classification** field, turning the catch-all into a discoverable,
queryable structured object.

### Ontology-Derivable, Not Ontology-Dependent
We don't ship a fixed ontology. We ship derivation rules. Given any IRS
form's instructions, the rules produce a taxonomy. New forms, new tax
years, and new legislation are handled by re-running derivation — not by
updating a monolithic schema.

### Projections Are Trivial
OTD is YAML-native, but projects cleanly to JSON, XML, and any other
structured format. The YAML carries the semantics; the projection is
just syntax.

## What We're Asking

We are seeking **review and co-development** from firms with deep
partnership tax expertise. Specifically:

1. **Coverage review** — Does the K-1 taxonomy capture every code your
   teams encounter in practice? What edge cases are missing?

2. **Statement taxonomy** — What are the most common complex footnotes
   and attachments you see? (Form 926, state grids, FIRPTA notices,
   §754 adjustments, etc.) We want to build statement taxonomies for
   the top 20.

3. **Adoption interest** — Would your firm's compliance tools benefit
   from emitting and/or consuming OTD? What would adoption look like?

4. **K-3 implementation stress test** — A draft K-3 taxonomy exists, but
   it has no proof, fixture, or validator exercise. Implementation evidence is
   the next milestone.

## Why Open Standard?

- **No vendor lock-in** — any firm can implement emitters and parsers
- **AI-agent interoperability** — K-1 data produced by one system can
  be consumed by any other system that speaks OTD
- **Regulatory alignment** — IRS modernization efforts (MeF, API-first
  filing) will eventually require structured data; OTD is ready
- **Network effects** — the more firms adopt, the less OCR everyone needs

## Contact

Tom O'Sullivan | Crimson Tree Software
tom@crimsontreesoftware.com

---

*"The best time to standardize tax data was twenty years ago. The second
best time is now."*
