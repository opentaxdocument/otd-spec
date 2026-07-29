# Open Tax Document — Parser Specification v0.1

**Status:** DRAFT
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Authors:** Tom O'Sullivan, Second Wind
**Date:** 2026-04-02

---

## 1. Purpose

This document specifies how to build software that **parses** (consumes)
OTD-conformant YAML documents and produces structured, queryable data.
A parser is the inverse of an emitter — it takes an OTD document and
makes its contents available for tax computation, form rendering,
comparison, or further transformation.

This specification is written so that an AI coding agent can implement
a working parser given this document, the OTD TaxNode Specification,
and an understanding of YAML.

---

## 2. Parser Inputs

A parser requires:

1. **OTD Document** — A `.otd.yaml` file
2. **Taxonomy (optional)** — If provided, enables validation and
   type enforcement. If absent, the parser operates in
   schema-tolerant mode.

---

## 3. Parser Outputs

The parser produces a **TaxDocument** object with:

- `envelope` — The document metadata
- `body` — A tree of TaxNode objects, queryable by:
  - Semantic path (`body.part_iii.box_20.code_Z`)
  - Form path (`Part III, Box 20, Code Z`)
  - Type filtering (`all nodes where type == 'grid'`)
- `statements` — Flattened list of all statement nodes (for easy iteration)
- `references` — Flattened list of all reference nodes
- `validation_results` — If taxonomy provided, constraint evaluation results

---

## 4. Parsing Rules

### 4.1 Schema-Tolerant Parsing

**Critical design principle:** A parser MUST NOT reject a document
because it contains unknown nodes.

OTD documents may contain nodes from future taxonomy versions or
custom extensions. The parser must:

1. Parse all recognized nodes into typed TaxNode objects
2. Preserve unrecognized nodes as generic key-value maps
3. Log unrecognized nodes as informational (not errors)

This ensures forward compatibility: a parser built for 2025 taxonomies
can still consume a 2026 document, accessing all 2025-compatible nodes
while preserving the rest.


### 4.1.1 Parse Leniency vs. Validation Strictness

Schema-tolerant parsing (§4.1) and taxonomy validation (§4.7) are **two
distinct phases with opposite dispositions**. Conflating them is the most
common implementation error.

| Phase | Disposition | Governs |
|---|---|---|
| **Parse** | Lenient. MUST NOT reject. | Structural readability. Unknown nodes are preserved, not errors. |
| **Validate** | Strict. SHOULD reject. | Conformance of *declared* nodes to the taxonomy. |

The distinction that matters:

- An **unknown** node — one the taxonomy does not declare — is preserved
  verbatim and reported informationally. This is forward compatibility, and
  it is mandatory.
- A **known** node — one the taxonomy *does* declare — that carries a wrong
  type, a wrong semantic identity, or a wrong form placement is a
  conformance failure. A validator with a taxonomy in hand SHOULD reject it.

A parser that rejects unknown nodes breaks forward compatibility. A
validator that accepts malformed known nodes provides false assurance —
it certifies a document as conformant while the meaning or type of reported
tax data has silently changed. Both failures are serious; they are opposite
failures and must not be traded against each other.

**Practical consequence.** `parse()` and `validate()` are separate calls
with separate outcomes. A document may parse cleanly and fail validation.
That is the intended design, not a contradiction.

### 4.2 Type Coercion

When the parser encounters a value, it applies type coercion:

| YAML Type | Taxonomy `value_type` | Coercion |
|-----------|----------------------|----------|
| Number    | `decimal`            | Parse as decimal with full precision |
| Number    | `integer`            | Parse as integer; error if fractional |
| String    | `string`             | No coercion |
| String    | `date`               | Parse ISO 8601 date |
| String    | `datetime`           | Parse ISO 8601 datetime |
| Boolean   | `boolean`            | No coercion |
| Number    | `percentage`         | Parse as decimal; note `format: percentage` |
| Null      | any                  | Preserve as null (distinct from absent) |

**Coercion failures** are logged as warnings. The raw YAML value is
preserved alongside the warning.

### 4.3 Null vs. Absent

The parser MUST distinguish:

- **Absent node:** Not present in the YAML → the parser's TaxDocument
  tree does not contain this node. Queries for it return "not reported."
- **Null node:** Present with `value: null` → the parser's TaxDocument
  tree contains the node with a null value. Queries return "reported as
  having no value."

This distinction is critical for compliance (e.g., a zero-value Box 2
means no rental activity; an absent Box 2 means rental activity was
not applicable).

### 4.4 Node Traversal

The parser builds a tree that supports multiple traversal modes:

**Semantic traversal (for tax logic):**
```
document.get_by_semantic("ordinary_business_income")
→ TaxNode { type: scalar, value: 150000.00 }
```

**Form traversal (for rendering):**
```
document.get_by_form("Part III", "Box 1")
→ TaxNode { type: scalar, value: 150000.00 }
```

**Type-filtered traversal (for batch processing):**
```
document.get_all_by_type("statement")
→ [Statement1, Statement2, ...]
```

**Deep traversal (for footnote/attachment enumeration):**
```
document.get_all_statements(recursive=true)
→ [top-level statements + statements nested within coded entries]
```

### 4.5 Statement Flattening

Statements can be nested at multiple levels:
- Top-level `statements` array
- Inline within coded entries (Box 20 Code ZZ)
- Nested within other statements (Form 926 inside a footnote)

The parser MUST provide a **flattened view** that enumerates all
statements regardless of nesting depth, while preserving parent
references:

```
FlatStatement {
  semantic_id: "form_926_attachment"
  classification: "irs_form_926"
  parent_path: "body.part_iii.box_20.entries[code=ZZ]"
  depth: 2
  content: { ... }
}
```

### 4.6 Reference Resolution

When the parser encounters a `reference` node:

- **Intra-document references:** Resolve immediately by following
  `node_path` within the same document
- **Inter-document references:** Do NOT resolve automatically.
  Instead, record the reference in the `references` list with
  enough information for the caller to resolve it:
  ```
  UnresolvedReference {
    source_path: "body.part_iii.box_16"
    target_document_id: "k3-partner-001"
    target_taxonomy_id: "irs-k3-1065-2025"
    target_node_path: "/"
  }
  ```

The caller may then load the referenced document and link the trees.


### 4.7 Validation (When Taxonomy Provided)

#### Null/Absent Algebra for Constraint Evaluation

Before evaluating any constraint, parsers MUST apply these rules to
operand values:

| Node State | In Sum/Arithmetic Constraint | In Range Constraint | In Required Constraint |
|------------|------------------------------|---------------------|------------------------|
| **Absent** (not in document) | Treated as **0.00** | Constraint is **SKIPPED** | **FAILS** if `required: true` |
| **Null** (present, value: null) | Treated as **0.00** | Treated as **0.00** (may fail range) | Passes (node is present) |
| **Zero** (value: 0.00) | Treated as **0.00** | Treated as **0.00** | Passes |

**Rationale:**
- An absent operand in a sum constraint is treated as 0 so that partial
  K-1s (e.g., partner with only income, no deductions) do not generate
  spurious arithmetic failures.
- An absent operand in a range constraint is skipped entirely because
  the range is meaningless if the value wasn't reported.
- A null operand is treated as 0 in arithmetic (it was reported as
  having no value, which is arithmetically zero).
- Required constraint failures for absent nodes are always errors
  regardless of the above.


#### Constraint Evaluation

If a taxonomy is supplied, the parser evaluates all constraints after
building the tree:
1. **Structural validation:** Every node in the document maps to a
   taxonomy entry (unrecognized nodes generate info-level messages)
2. **Type validation:** Node values conform to their declared types
3. **Required field validation:** All `required: true` nodes are present
4. **Constraint evaluation:** Sum, range, and cross-reference constraints
   are checked against actual values
5. **ZZ classification check:** All Code ZZ entries have a `classification`

Results are returned as a list:
```
ValidationResult {
  node_path: "body.part_iii.box_4c"
  constraint_id: "box_4c_sum"
  severity: "error"
  expected: 200000.00
  actual: 199999.50
  message: "Box 4c (199,999.50) does not equal Box 4a + 4b (200,000.00); difference: 0.50"
}
```

---

## 5. Query Interface

A conformant parser SHOULD expose at minimum:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_envelope()` | Envelope | Document metadata |
| `get_node(semantic_id)` | TaxNode or null | By semantic identifier |
| `get_by_form(location)` | TaxNode or null | By form location string |
| `get_by_box(box, code?)` | TaxNode or list | By box number and optional code |
| `get_all_by_type(type)` | List[TaxNode] | All nodes of given type |
| `get_statements()` | List[Statement] | All statements, flattened |
| `get_references()` | List[Reference] | All references |
| `get_value(semantic_id)` | typed value | Shortcut: get node → return value |
| `validate(taxonomy)` | List[ValidationResult] | Run constraint checks |
| `to_dict()` | Dict | Full document as nested dictionary |
| `to_json()` | String | JSON projection |
| `to_xml()` | String | XML projection |

---

## 6. Projection Rules

### 6.1 JSON Projection

YAML → JSON is a direct structural mapping. The only transformations:
- YAML anchors/aliases are resolved to concrete values
- Comments are stripped (JSON has no comment syntax)
- File extension: `.otd.json`

### 6.2 XML Projection

YAML → XML follows these conventions:
- The root element is `<otd>`
- Map keys become element names
- Lists become repeated elements with a wrapper
- Attributes are NOT used (all data is elements)
- Namespace: `xmlns:otd="https://opentaxdocument.org/v0.1"`
- File extension: `.otd.xml`

---

## 7. Error Handling


Behaviour depends on whether a taxonomy is available. Parsing without a
taxonomy is lenient by necessity — there is nothing to check against.
Validation with a taxonomy is strict by design (§4.1.1).

| Condition | Parse only (no taxonomy) | Validate (taxonomy provided) |
|-----------|--------------------------|------------------------------|
| Invalid YAML syntax | **Error.** Abort parse, return error with line/column | **Error.** |
| Missing `otd:` envelope | **Error.** Document is not OTD-conformant | **Error.** |
| Missing `body` | **Error.** Document has no tax data | **Error.** |
| Unknown node type (not declared by taxonomy) | **Warning.** Preserve as generic map | **Warning.** Preserve; forward compatibility (§4.1) |
| Type coercion failure on an **undeclared** node | **Warning.** Preserve raw value | **Warning.** Preserve raw value |
| Type coercion failure on a **declared** node | **Warning.** Preserve raw value | **Error.** Declared `value_type` not satisfied |
| Declared node missing `type`, `semantic.id`, or `form` | **Warning.** Preserve as generic map | **Error.** Node does not bind to its declaration |
| Declared node with mismatched `semantic.id` or form placement | n/a — undetectable | **Error.** Identity does not match declaration |
| Missing required node | **Warning** | **Error** |
| Declared field absent from document | n/a — undetectable | **Error.** Physical completeness (§4.7) |
| Constraint violation | n/a — undetectable | **Error** or **Warning** per constraint severity |
| Constraint references an undeclared path | n/a | **Error.** Malformed rule; MUST NOT silently skip |
| Redacted value encountered | **Info.** Flag as redacted; do not attempt to reconstruct | **Info.** |
| Taxonomy cannot be resolved | n/a | **Error.** A validator that cannot load its schema has not validated anything |

**On the last row.** An unresolvable taxonomy MUST NOT degrade to a warning
with a success exit status. Silently reporting "rules were not enforced"
while returning success is a fail-open channel: the caller believes the
document was validated when nothing was checked.

---


## 7.1 Validation Profile — Rules Beyond the Base Specification

The reference implementation (`skills/k1-otd/scripts/constraint_engine.py`)
enforces four rules that are **not derivable from the taxonomy alone**. They
are documented here so a third-party implementation can match its behaviour
rather than discover the divergence in production.

Status: **DRAFT.** These are proposed for promotion to normative in v0.3.
Implementer feedback is specifically invited.

### 7.1.1 Duplicate coded entries are rejected

A coded box MUST NOT contain two entries for the same code. On the physical
Schedule K-1 a code appears once per box; multiplicity belongs in an attached
statement, not in repeated face entries.

*Rationale beyond form fidelity:* path resolution (`part_iii.box_20.X`)
returns the first match. A second entry for the same code is unreachable by
every path-based check, so a non-conforming entry could hide behind a
conforming one.

### 7.1.2 Statement nodes carry `semantic.role`

Every statement node MUST declare `semantic.role`. Footnote statements use
`investor_footnote` (§2.6 of the TaxNode specification). This makes footnote
enumeration a structural query rather than a classification heuristic.

### 7.1.3 Capital accounts are complete or explicitly quarantined

Item L MUST carry all six components (beginning, contributions,
current-year increase/decrease, other increase/decrease, withdrawals,
ending), or be marked `_unverified`. A partial capital account cannot be
checked for continuity, and silently skipping the check reports success
on unverifiable arithmetic.

Where both `current_year_increase_decrease` and the legacy
`current_year_net` alias are present, the document is rejected as
ambiguous rather than summing both.

### 7.1.4 Not-yet-observed values are `null`, never a plausible default

A field that was not observed in the source MUST be emitted as `null` with
an `_unverified` marker. It MUST NOT be emitted as `false`, `0`, or any
other plausible-looking default.

This is the single most consequential rule in the profile. Writing `false`
for an unread checkbox converts an unknown fact into an **incorrect negative
assertion** on a tax document — for example, asserting that a partner has no
payment obligations when the form was simply never read.

Extraction pipelines that use a sentinel string (the reference pipeline uses
`"UNKNOWN"`) MUST resolve it to `null` plus a marker before emission, never
pass it through as a value.

---

## 8. Round-Trip Canonicalization Requirement

For documents within an implementation's supported profile, the canonical
quality test is:

```
parse(document) → TaxDocument
emit(TaxDocument) → document'
normalize(document) == normalize(document')
```

This is normalized serialization equivalence, not a claim that the raw source
bytes remain identical before normalization.


Normalization includes:
- Consistent key ordering: **taxonomy-defined sequence** within each level.
  Keys appear in the order declared in the taxonomy file, not alphabetically.
  For nodes not in the taxonomy, keys follow original emission order.
  **Alphabetical ordering is explicitly prohibited** — it destroys human
  readability and produces unintuitive diffs (e.g., `box_1` → `box_10`
  → `box_11` → `box_2` instead of natural form sequence).
- Consistent decimal formatting (exactly 2 decimal places for currency values)
- Consistent date formatting (ISO 8601)
- Consistent indentation (2 spaces)

If canonical round-trip fidelity fails for a supported valid document,
either the emitter, parser, or normalization implementation has a bug.

---

## 9. Implementer's Checklist

A parser implementation is considered complete when it can:

- [ ] Parse any valid OTD document without errors
- [ ] Preserve unknown nodes without rejection
- [ ] Distinguish null from absent
- [ ] Build a queryable tree supporting semantic and form traversal
- [ ] Flatten nested statements with parent references
- [ ] Record unresolved inter-document references
- [ ] Apply type coercion with graceful fallback
- [ ] Validate against a taxonomy when provided
- [ ] Project to JSON and XML
- [ ] Pass the round-trip test with any emitter

---

## 10. Implementation Recommendations

### 10.1 Language Recommendations

| Language   | YAML Library         | Notes |
|------------|---------------------|-------|
| Python     | `ruamel.yaml`       | Preserves comments and ordering; best for round-trip |
| TypeScript | `yaml` (npm)        | Good for web-based parsers |
| C#         | `YamlDotNet`        | .NET ecosystem integration |
| Go         | `gopkg.in/yaml.v3`  | Performance-oriented |
| Rust       | `serde_yaml`        | Zero-copy parsing |

### 10.2 Testing Strategy

1. **Golden file tests:** Parse known-good OTD documents, verify tree structure
2. **Round-trip tests:** Parse → emit → compare byte-for-byte
3. **Stress tests:** K-3 Part II (full 54-row × 6-column grid), K-3 Part VII (10+ PFIC records)
4. **Edge cases:**
   - Document with only Part I (no income data)
   - All Code ZZ entries
   - Maximum nesting depth (statement within statement within coded entry)
   - Redacted document with all PII masked
5. **Cross-parser tests:** Verify that documents produced by one emitter are
   correctly parsed by a different parser implementation
