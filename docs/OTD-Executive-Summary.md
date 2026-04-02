# Open Tax Document (OTD) — Executive Summary

**An Open Standard for AI-Native Tax Document Representation**

**Version:** 0.1 DRAFT | **Date:** April 2, 2026
**Authors:** Tom O'Sullivan, Crimson Tree Software
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Status:** Seeking multi-firm review and adoption

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

4. **Round-trip fidelity is guaranteed** — emit a K-1 as OTD YAML,
   parse it back, re-emit it: byte-identical output. Zero data loss.

5. **Form production is built in** — every node carries enough metadata
   to reconstruct the physical printed form (box, line, code, column),
   not just the data.

## What's In This Package

| Document | Purpose |
|----------|---------|
| `otd-spec-v0.1.yaml` | **Core specification** — the six TaxNode primitives (scalar, coded, grid, recordset, statement, reference) with real examples |
| `otd-k1-taxonomy-2025.yaml` | **Complete K-1 taxonomy** — every box, every code (200+), every cross-reference, derived from 2025 IRS instructions |
| `otd-derivation-spec.md` | **Taxonomy derivation rules** — how to generate a taxonomy for *any* IRS form from its instructions, making the standard self-perpetuating |
| `otd-emitter-spec.md` | **Emitter guide** — how to build software that produces OTD documents |
| `otd-parser-spec.md` | **Parser guide** — how to build software that consumes OTD documents |
| `otd_round_trip_proof.py` | **Working proof-of-concept** — Python script that emits, parses, validates, queries, and round-trips a realistic K-1 |
| `proof-emitted.otd.yaml` | **Example output** — a realistic K-1 OTD document with §199A, Form 926, and multi-code boxes |
| `proof-results.txt` | **Test results** — all five phases passed, including byte-identical round-trip |

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

4. **K-3 stress test** — We have scouted the K-3 instructions and
   designed the grid and recordset primitives for it. A K-3 taxonomy
   derivation is the next milestone.

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
