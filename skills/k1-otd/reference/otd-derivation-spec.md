# Open Tax Document — Taxonomy Derivation Specification v0.1

**Status:** DRAFT
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Authors:** Tom O'Sullivan, Second Wind
**Date:** 2026-04-02

---

## 1. Purpose

This document specifies how to derive an OTD taxonomy from any IRS form's
instructions. A taxonomy is the structural blueprint that defines which
TaxNodes exist for a given form, their types, their relationships, and
their validation constraints.

**The goal is zero-curation derivation.** Given the text of an IRS
instruction document (PDF or HTML), a reasonably capable AI coding agent
should be able to produce a complete, correct OTD taxonomy without human
intervention. Human review is recommended but not required.

---

## 2. Taxonomy Identity

Every taxonomy carries a unique identity:

```yaml
taxonomy:
  id: "irs-k1-1065-2025"          # Pattern: {authority}-{form}-{parent}-{year}
  version: "2025.1.0"             # SemVer: {tax_year}.{revision}.{patch}
  form_number: "Schedule K-1"
  parent_form: "Form 1065"
  tax_year: 2025
  authority: "irs"                 # irs | state-{code} | foreign-{country}
  source:
    title: "Partner's Instructions for Schedule K-1 (Form 1065), 2025"
    url: "https://www.irs.gov/instructions/i1065sk1"
    retrieved: "2026-04-02"
  derived_by:
    agent: "Second Wind"
    method: "otd-derivation-spec-v0.1"
```

### 2.1 Naming Convention

| Component     | Pattern                        | Example              |
|---------------|--------------------------------|----------------------|
| Authority     | `irs` or `state-{code}`        | `irs`, `state-ny`    |
| Form          | lowercase, hyphens             | `k1`, `k3`, `926`    |
| Parent        | parent form number             | `1065`, `1120s`      |
| Year          | 4-digit tax year               | `2025`               |
| Full ID       | `{authority}-{form}-{parent}-{year}` | `irs-k1-1065-2025` |

### 2.2 Versioning

Taxonomies use semantic versioning within a tax year:

- **Major** = tax year (2025, 2026, ...)
- **Minor** = structural revision (new codes, retired codes, new parts)
- **Patch** = corrections to an existing derivation

Example: `2025.1.0` → first derivation; `2025.1.1` → typo fix; `2025.2.0` → mid-year legislation adds new codes.

---

## 3. Derivation Pipeline

The derivation process has six ordered stages. Each stage produces
intermediate artifacts that feed the next.

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Stage 1    │───▶│  Stage 2    │───▶│  Stage 3    │
│  Form ID    │    │  Structure  │    │  Code Enum  │
│             │    │  Tree       │    │             │
└─────────────┘    └─────────────┘    └─────────────┘
                                            │
┌─────────────┐    ┌─────────────┐          │
│  Stage 6    │◀───│  Stage 5    │◀─────────┘
│  Validation │    │  Cross-Ref  │    ┌─────────────┐
│  Constraints│    │  Mapping    │◀───│  Stage 4    │
└─────────────┘    └─────────────┘    │  Type Class │
                                      └─────────────┘
```

### Stage 1 — Form Identification

**Input:** IRS instruction document (text)
**Output:** Form metadata block

Extract:
- Form number and title
- Parent form (if schedule)
- Tax year
- Revision indicators ("What's New" section)
- Related forms mentioned

**Rules:**
- The title block of IRS instructions always contains the form number, year, and parent.
- "What's New" sections identify year-over-year changes — these become `change_log` entries in the taxonomy.
- "Reminders" sections may introduce new reporting requirements.

### Stage 2 — Structure Tree Extraction

**Input:** Instruction document text
**Output:** Hierarchical node tree (Parts → Sections → Lines → Sub-items)

Extract the physical structure of the form:

```yaml
structure:
  - part: "I"
    title: "Information About the Partnership"
    items:
      - item: "A"
        label: "Partnership's name, address, city, state, and ZIP code"
        type_hint: "identity"
      - item: "B"
        label: "Partnership's employer identification number"
        type_hint: "identifier"
  - part: "III"
    title: "Partner's Share of Current Year Income, Deductions, Credits, and Other Items"
    boxes:
      - box: 1
        label: "Ordinary business income (loss)"
        type_hint: "scalar"
      - box: 11
        label: "Other income (loss)"
        type_hint: "coded"
        codes: []   # Populated in Stage 3
```

**Rules:**
- IRS instructions use consistent heading patterns:
  - "Part I.", "Part II.", etc. for major sections
  - "Box N." for K-1 line items
  - "Line N." for numbered lines within parts (K-3, 1065)
  - "Code X." for lettered sub-items within a box
- The TOC/outline at the top of IRS instruction pages provides the
  definitive structural hierarchy.
- Items that say "See attached statement" or "See separate instructions"
  indicate the node may have Statement children.

### Stage 3 — Code Enumeration

**Input:** Structure tree + instruction text
**Output:** Complete code catalog for each coded box

For each box identified as `coded` in Stage 2, extract all valid codes:

```yaml
box_11_codes:
  - code: "A"
    label: "Other portfolio income (loss)"
    description: "..."
    value_type: "decimal"
  - code: "B"
    label: "Involuntary conversions"
    value_type: "decimal"
  # ... through ...
  - code: "ZZ"
    label: "Other"
    value_type: "any"
    requires_classification: true    # ZZ codes MUST carry classification
```

**Rules:**
- Codes appear in instruction text as "Code A.", "Code B.", etc.
- Some codes are reserved/placeholder ("Codes T through X" means T, U, V, W, X are reserved for future use or are intentionally grouped).
- Code ZZ is always the catch-all. It MUST be flagged as `requires_classification: true`.
- The description following each code defines the `label` and `description` fields.
- Value types are inferred from context:
  - Dollar amounts → `decimal` with `currency: USD`
  - Percentages → `decimal` with `format: percentage`
  - Dates → `date`
  - Descriptive text → `string`
  - Complex structures → `null` value with `statement` attachment

### Stage 4 — Type Classification

**Input:** Structure tree with codes
**Output:** Each leaf node classified as scalar, coded, grid, recordset, statement, or reference

Classification rules (in priority order):

| Signal in Instructions | Assigned Type |
|------------------------|---------------|
| Single value, one box, no codes | **scalar** |
| Box with multiple lettered codes | **coded** |
| Tabular data with fixed row/column headers | **grid** |
| Variable-length list of entities (e.g., "for each PFIC...") | **recordset** |
| "See attached statement" / "See supplemental schedule" | **statement** |
| "See Schedule K-3" / "reported on Form X" | **reference** |
| Catch-all code (ZZ) with complex description | **coded** + **statement** child |

**Ambiguity resolution:**
- When a node could be either grid or recordset, ask: "Are the rows
  predetermined by the form?" If yes → grid. If no → recordset.
- When a node could be scalar or coded, check if the instructions
  describe sub-codes. If they do → coded. If not → scalar.

### Stage 5 — Cross-Reference Mapping

**Input:** Classified node tree
**Output:** Reference edges between nodes and between forms

Extract relationships:

```yaml
cross_references:
  - source: "k1.part_iii.box_16"
    target_form: "k3-1065"
    target_path: "/"
    relationship: "detail"          # This node's detail is in the target
  - source: "k1.part_iii.box_20.code_Z"
    target_form: null               # Same document
    target_path: "statements.section_199a"
    relationship: "overflow"
  - source: "k3.part_ii"
    target_form: "1116"
    target_path: null               # External form, not OTD
    relationship: "feeds"           # This data feeds the target form
```

**Relationship types:**
- `detail`: target contains expanded detail for this node
- `overflow`: target is a statement/schedule attached to this node
- `feeds`: this node's data is used to complete the target form
- `reconciles`: this node must match a corresponding node elsewhere
- `supersedes`: this node replaces the target in certain conditions

### Stage 6 — Validation Constraint Extraction

**Input:** Instruction text + classified nodes
**Output:** Constraint rules

IRS instructions contain implicit and explicit validation rules:

```yaml
constraints:
  - id: "box_4c_sum"
    type: "sum"
    target: "part_iii.box_4c"
    operands: ["part_iii.box_4a", "part_iii.box_4b"]
    tolerance: 0.01
    severity: "error"
    source: "Box 4c equals the sum of 4a and 4b"

  - id: "box_1_sign"
    type: "sign_convention"
    target: "part_iii.box_1"
    rule: "positive_is_income, negative_is_loss"
    severity: "warning"

  - id: "box_20_zz_classification"
    type: "required_field"
    target: "part_iii.box_20.code_ZZ.semantic.classification"
    condition: "when code is ZZ"
    severity: "error"
    source: "OTD specification §2.3"
```

**Extraction signals:**
- "equals the sum of" → sum constraint
- "cannot exceed" → range constraint
- "must be reported" → required constraint
- "if X, then Y" → conditional required
- Sign language: "Enter as a positive number" / "losses are shown in parentheses"

---

## 4. Taxonomy Output Format

The final taxonomy is a YAML file that serves as the schema for OTD
documents of that form type:

```yaml
# otd-k1-taxonomy-2025.yaml (excerpt)
taxonomy:
  id: "irs-k1-1065-2025"
  version: "2025.1.0"
  # ... identity block from §2 ...

nodes:
  part_i:
    title: "Information About the Partnership"
    children:
      partnership_name:
        type: scalar
        semantic_id: "partnership.name"
        label: "Partnership's Name"
        value_type: string
        form_location: "Part I, Item A"
        required: true
      # ...

  part_iii:
    title: "Partner's Share of Current Year Income, Deductions, Credits, and Other Items"
    children:
      box_1:
        type: scalar
        semantic_id: "ordinary_business_income"
        label: "Ordinary Business Income (Loss)"
        value_type: decimal
        currency: USD
        form_location: "Part III, Box 1"
        box: 1
        sign_convention: "positive_is_income"

      box_11:
        type: coded
        semantic_id: "other_income"
        label: "Other Income (Loss)"
        form_location: "Part III, Box 11"
        box: 11
        codes:
          A:
            semantic_id: "other_income.portfolio"
            label: "Other portfolio income (loss)"
            value_type: decimal
          # ... all codes ...
          ZZ:
            semantic_id: "other_income.other"
            label: "Other"
            value_type: any
            requires_classification: true
            may_attach_statement: true

      box_20:
        type: coded
        semantic_id: "other_information"
        label: "Other Information"
        form_location: "Part III, Box 20"
        box: 20
        codes:
          Z:
            semantic_id: "other_information.section_199a"
            label: "Section 199A information"
            value_type: null
            requires_statement: true
            statement_classification: "section_199a"
          ZZ:
            semantic_id: "other_information.other"
            label: "Other"
            value_type: any
            requires_classification: true
            may_attach_statement: true

cross_references:
  # ... from Stage 5 ...

constraints:
  # ... from Stage 6 ...

change_log:
  - version: "2025.1.0"
    date: "2025-01-01"
    changes:
      - "Box 13, Code X: expanded to include sound recording production expenses"
      - "Box 19: distribution codes separated by category"
      - "Box 20, Code ZZ: added section 1062 farmland gain installment"
```

---

## 5. Derivation Quality Gates

A derived taxonomy is considered complete when:

| Gate | Criteria |
|------|----------|
| **Coverage** | Every Part, Box/Line, and Code mentioned in the instructions has a corresponding node |
| **Type Fidelity** | Every node's type matches the structural pattern in the instructions |
| **Code Completeness** | Every lettered code in every coded box is enumerated, including reserved ranges |
| **Cross-Reference Integrity** | Every "See Form X" or "reported on Schedule Y" is captured as a reference edge |
| **Constraint Presence** | At least one constraint per box that has a documented arithmetic relationship |
| **ZZ Classification** | All Code ZZ entries are flagged `requires_classification: true` |
| **Change Log** | "What's New" items are captured |

### 5.1 Automated Validation

A derivation validator should check:
1. No duplicate `semantic_id` values within a taxonomy
2. All `cross_references` point to valid node paths or declared external forms
3. All `constraints` reference valid node paths
4. All `coded` nodes have at least one code entry
5. All `grid` nodes have both `columns` and `rows` defined
6. All `recordset` nodes have a `record_schema`

---

## 6. Year-Over-Year Derivation

When deriving a new tax year's taxonomy from the prior year:

1. Start from the previous year's taxonomy as a baseline
2. Apply changes from the "What's New" section
3. Re-run Stages 2-6 against the new year's instructions
4. Diff the result against the baseline
5. Produce a `change_log` entry documenting additions, removals, and modifications

This process should be deterministic: two agents running the same
derivation against the same instructions should produce structurally
identical taxonomies (though `description` text may vary).

---

## 7. Extension: State and Foreign Taxonomies

The same derivation pipeline applies to:

- **State K-1 schedules**: Authority = `state-{code}`, parent form varies by state
- **Foreign equivalents**: Authority = `foreign-{country-code}`
- **Related federal forms**: Form 926, 8865, 5471, etc.

State taxonomies may reference federal taxonomy nodes for apportionment
of federal amounts (e.g., "State Column A = Federal Box 1 × Apportionment %").

---


## 8. Governance and Canonical Authority

**CRITICAL PRINCIPLE:** The published taxonomy YAML file in the official
OTD repository is the **sole, immutable, canonical source of truth** for
any given form and tax year. It is NOT acceptable for individual firms
to independently derive taxonomies using the AI pipeline and treat the
output as authoritative.

**The derivation pipeline is a toolchain, not a governance model.**

### 8.1 Taxonomy Lifecycle

1. **Draft:** A maintainer (human or AI-assisted) runs the derivation
   pipeline against the IRS instructions to produce a draft taxonomy.
2. **Review:** The draft is reviewed by qualified tax professionals for
   coverage, accuracy, and edge-case handling.
3. **Publish:** The reviewed taxonomy is committed to the official OTD
   repository with a version number and changelog.
4. **Adopt:** Firms adopt the published taxonomy by version reference.
5. **Amend:** If errors are found, a patch version is published
   (e.g., 2025.1.1) with a changelog entry.

### 8.2 Version Pinning

OTD documents MUST reference a specific taxonomy version in their
envelope. Parsers MUST validate against the referenced version, not
the latest available version. This ensures that a document produced
against taxonomy 2025.1.0 is always parseable even after 2025.2.0
is published.

### 8.3 AI Derivation as Tooling

The derivation specification (§3) describes how to use AI coding agents
to accelerate taxonomy production. This is a productivity tool for
maintainers, analogous to code generation — the output is a draft that
requires human review and formal publication before it carries authority.

Two agents running the same derivation may produce structurally similar
but not byte-identical taxonomies. This is expected and acceptable
because the derivation output is a **draft**, not the canonical artifact.

## 9. Implementer's Note

This specification is designed to be implementable by an AI coding agent
(e.g., OpenAI Codex, Anthropic Claude Code, Google Jules) given:

1. The text of the IRS instructions
2. This derivation specification
3. The OTD TaxNode specification (otd-spec-v0.1.yaml)

The agent should be able to produce a complete taxonomy YAML file as a
**draft for review**. The recommended approach is:

1. Ingest the instruction text
2. Apply Stages 1-6 sequentially
3. Emit the taxonomy YAML
4. Run the automated validation (§5.1)
5. Report coverage metrics
6. Submit for human review and publication

Expected time for a capable agent: 15-30 minutes for a K-1 taxonomy,
45-90 minutes for a K-3 taxonomy (due to the grid complexity in Parts II-III
and the recordset complexity in Parts V-VII).
