# Open Tax Document — Emitter Specification v0.1

**Status:** DRAFT
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Authors:** Tom O'Sullivan, Second Wind
**Date:** 2026-04-02

---

## 1. Purpose

This document specifies how to build software that **emits** (produces)
OTD-conformant YAML documents from source tax data. An emitter takes
structured data from any source (tax engine, database, spreadsheet, API)
and produces a valid OTD document.

This specification is written so that an AI coding agent can implement
a working emitter given this document, the OTD TaxNode Specification,
and a derived taxonomy for the target form.

---

## 2. Emitter Inputs

An emitter requires:

1. **Source Data** — The tax data to be represented. Format varies:
   - Database query results (SQL, API response)
   - Spreadsheet/CSV exports
   - Tax engine output (XML, JSON, proprietary)
   - Prior OTD documents (for round-trip or migration)

2. **Taxonomy** — The OTD taxonomy for the target form and year
   (e.g., `otd-k1-taxonomy-2025.yaml`)

3. **Configuration** — Emitter settings:
   - Redaction policy (`full`, `partial`, `none`)
   - Omit-when-empty behavior
   - Producer identity
   - Document ID generation strategy

---

## 3. Emitter Output

A single `.otd.yaml` file conforming to the OTD TaxNode Specification.

---

## 4. Emission Rules

### 4.1 Envelope

Every emitted document MUST begin with the `otd:` envelope block:

```yaml
otd:
  version: "0.1"
  document_id: "{generated_uuid}"
  created: "{iso_8601_now}"
  producer:
    name: "{configured_producer_name}"
    version: "{configured_producer_version}"
  taxonomy:
    id: "{taxonomy.id}"
    version: "{taxonomy.version}"
    source: "{taxonomy.source.title}"
```


### 4.1b Form Metadata

Every emitted document that represents a filed or fileable tax form
MUST include a `form_metadata` block immediately after the envelope:

```yaml
form_metadata:
  tax_year: 2025
  fiscal_year: false
  fiscal_year_begin: null
  fiscal_year_end: null
  amended: false
  final: false
  supersedes_document_id: null
  filing_status: "original"
  form_revision_date: "2025"
```

**Rules:**
- `tax_year` is REQUIRED
- `fiscal_year` is REQUIRED (default `false`)
- `fiscal_year_begin` and `fiscal_year_end` are REQUIRED when
  `fiscal_year: true`; otherwise null
- `amended` and `final` are REQUIRED (default `false`)
- `filing_status` is REQUIRED; must be one of:
  `original`, `amended`, `superseded`, `void`
- `supersedes_document_id` is REQUIRED when `filing_status` is
  `amended` or `superseded`; otherwise null

### 4.2 Node Emission

For each node in the taxonomy, the emitter checks whether source data
exists for that node.

**Omit-When-Empty Rule:**
- If source data is absent AND the node is not `required` in the taxonomy → **OMIT** the node entirely
- If source data is absent AND the node IS `required` → emit with `value: null`
- If source data is present with a zero value → **EMIT** the node (zero is semantically different from absent)

**Node Structure:**
Every emitted node MUST include:
- `type` — the TaxNode primitive type
- `semantic` block with at least `id` and `label`
- `form` block with at least `location`
- `value` (or `entries`, `columns`/`rows`, `records`, `content`, `target` depending on type)

### 4.3 Type-Specific Rules

#### Scalar
```yaml
box_1:
  type: scalar
  semantic: { id: "{taxonomy.semantic_id}", label: "{taxonomy.label}" }
  form: { form_id: "{form}", location: "{taxonomy.form_location}", box: {n} }
  value: {source_value}
  currency: "USD"                    # Include when value_type is decimal/currency
```

#### Coded
Emit an `entries` list. Each entry corresponds to a code with source data:
```yaml
box_11:
  type: coded
  semantic: { id: "{taxonomy.semantic_id}", label: "{taxonomy.label}" }
  form: { form_id: "{form}", location: "{taxonomy.form_location}", box: {n} }
  entries:
    - code: "A"
      semantic: { id: "{code.semantic_id}", label: "{code.label}" }
      value: {source_value}
    # Only emit codes that have data (unless required)
```

**Code ZZ Entries:**
When emitting a Code ZZ entry, the `classification` field in `semantic`
is REQUIRED. The emitter must either:
1. Map the source data to a known classification, or
2. Use classification `"unclassified"` and attach the raw data as a statement

#### Grid
```yaml
grid_node:
  type: grid
  semantic: { ... }
  form: { ... }
  columns:
    - { key: "...", label: "...", form_column: "..." }
  rows:
    - key: "..."
      label: "..."
      form_line: {n}
      values:
        col_key_1: {value}
        col_key_2: {value}
  currency: "USD"
```

**Grid Emission Rules:**
- Column definitions are always emitted in full (from taxonomy)
- Rows are emitted only if at least one cell has a non-null value
- Null cells within an emitted row are represented as `0` (not omitted)
  because grid position is semantically meaningful
- Country-specific grids repeat the grid structure with a `country_code` field

#### RecordSet
```yaml
recordset_node:
  type: recordset
  semantic: { ... }
  form: { ... }
  record_schema:
    - { field: "...", type: "...", label: "...", form_column: "..." }
  records:
    - { field_1: value, field_2: value, ... }
```

**RecordSet Emission Rules:**
- `record_schema` is always emitted in full (from taxonomy)
- Records are emitted in the order they appear in source data
- Nullable fields may be `null`; non-nullable fields must have a value
- Empty recordsets (no records) may be omitted entirely

#### Statement
```yaml
statement_node:
  type: statement
  semantic:
    id: "..."
    label: "..."
    classification: "..."            # REQUIRED — what kind of statement
  form:
    attachment: true
  parent_ref: "..."                  # Semantic path to the parent node
  content:
    # Nested TaxNodes — the structure depends on the classification
```

**Statement Emission Rules:**
- `classification` is REQUIRED and must be a recognized category
  or a custom string
- `content` may contain any valid TaxNode tree
- Statements referenced from a coded entry (e.g., Box 20 Code ZZ)
  are emitted inline under that entry
- Standalone statements (not tied to a specific box) go in the
  top-level `statements` array
- For complex attachments (Form 926, state grids, K-3), the
  emitter should use the appropriate taxonomy to structure the content

#### Reference
```yaml
reference_node:
  type: reference
  semantic: { ... }
  form: { ... }
  target:
    document_type: "otd"
    taxonomy_id: "..."
    document_id: "..."               # If pointing to a separate OTD file
    node_path: "/"                   # XPath-like path within target
```

**Reference Emission Rules:**
- If the target is a separate OTD document, `document_id` is REQUIRED
- If the target is within the same document, `document_id` is omitted
  and `node_path` points to the local node
- References to non-OTD forms (e.g., "feeds Form 1116") use
  `document_type: "external"` and `taxonomy_id` identifies the form

### 4.4 Redaction

The emitter MUST apply redaction according to the configured policy
before writing any output:

| Policy    | EIN              | SSN              | Names   | Addresses |
|-----------|------------------|------------------|---------|-----------|
| `none`    | Full value       | Full value       | Full    | Full      |
| `partial` | XX-XXXX{last3}   | XXX-XX-{last4}   | Full    | Full      |
| `full`    | XX-XXXXXXX       | XXX-XX-XXXX      | Masked  | Masked    |

Redacted fields are listed in the `redaction.fields_redacted` array
in the document envelope.

### 4.5 Validation Before Emit

Before writing the final document, the emitter SHOULD:

1. Validate all nodes against the taxonomy (type correctness, required fields)
2. Evaluate all constraints from the taxonomy
3. Flag violations as warnings or errors in the document metadata
4. Emit the document even if warnings exist (but not if errors exist,
   unless `force_emit: true` is configured)

### 4.6 Encoding and Format

- **Encoding:** UTF-8 (no BOM)
- **Line endings:** LF (`\n`)
- **Indentation:** 2 spaces (YAML standard)
- **Decimal precision:** At least 2 decimal places for currency values
- **Date format:** ISO 8601 (`YYYY-MM-DD`)
- **DateTime format:** ISO 8601 with timezone (`YYYY-MM-DDTHH:MM:SSZ`)

---

## 5. Multi-Document Emission

A single tax return often produces multiple OTD documents:

- One K-1 per partner
- One K-3 per partner (if applicable)
- Shared statements (e.g., partnership-level footnotes)

The emitter should support batch emission with cross-referencing:

```
output/
  otd-k1-partner-001.otd.yaml
  otd-k1-partner-002.otd.yaml
  otd-k3-partner-001.otd.yaml
  otd-k3-partner-002.otd.yaml
  manifest.otd.yaml                  # Lists all documents and their relationships
```

The manifest includes:
```yaml
manifest:
  partnership: "Greenfield Capital Partners LP"
  tax_year: 2025
  documents:
    - id: "k1-partner-001"
      type: "k1-1065"
      partner: "Jane Doe"
      file: "otd-k1-partner-001.otd.yaml"
    - id: "k3-partner-001"
      type: "k3-1065"
      partner: "Jane Doe"
      file: "otd-k3-partner-001.otd.yaml"
      references:
        - from: "k1-partner-001"
          node: "part_iii.box_16"
```

---

## 6. Implementer's Checklist

An emitter implementation is considered complete when it can:

- [ ] Produce a valid OTD document from source data + taxonomy
- [ ] Apply omit-when-empty rules correctly
- [ ] Handle all six TaxNode types
- [ ] Apply redaction policy
- [ ] Emit Code ZZ entries with required classification
- [ ] Nest statements within coded entries
- [ ] Produce standalone statements in the statements array
- [ ] Generate cross-document references
- [ ] Validate against taxonomy constraints before emission
- [ ] Produce a manifest for multi-document batches
- [ ] Round-trip: emit → parse → re-emit produces identical YAML
