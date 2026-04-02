# Open Tax Document — Extension Registry Specification v0.2 (RC-1)

**Status:** RELEASE CANDIDATE
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Authors:** Tom O'Sullivan, Second Wind
**Date:** 2026-04-02
**Changelog:** v0.2 RC-1 — Air-gap: local bundling is primary, HTTP is optional convenience only.
  Added `legacy_aliases` for promoted-to-core type transitions.
  Clarified namespace collision resolution for competing firm schemas.

---

## 1. Purpose

The OTD `custom` footnote classification is a necessary escape hatch.
Without governance, `custom_fields` becomes an unvalidated garbage dump.

The Extension Registry allows organizations to publish **named schema
extensions** — formal definitions of firm-specific footnote types that
parsers can load and validate against.

---

## 2. Extension File Format

`otd-{org-slug}-extensions-{version}.yaml`

Examples:
- `otd-rsm-extensions-2025.1.0.yaml`
- `otd-am-extensions-2025.1.0.yaml`

```yaml
otd_extension:
  org: "rsm"
  version: "2025.1.0"
  license: "CC BY 4.0"
  description: "RSM-specific footnote types"

footnote_extensions:
  - classification: "rsm_basis_roll_forward"
    label: "RSM Partner Basis Roll-Forward Schedule"
    legacy_aliases: []                      # Populated when promoted to core
    schema:
      fields:
        - field: "opening_basis"
          type: decimal
          required: true
        - field: "closing_basis"
          type: decimal
          required: true
    validation:
      - type: sum
        target: "closing_basis"
        operands: ["opening_basis", "income_items", "loss_items", "distribution_items"]
        tolerance: 1.00
        severity: error
```

---

## 3. Using Extension Types in OTD Documents

```yaml
footnotes:
  - type: footnote
    id: "fn-007"
    classification: "rsm_basis_roll_forward"
    extension_ref: "otd-rsm-extensions-2025.1.0.yaml"  # REQUIRED for extension types
    structured:
      opening_basis: 1000000.00
      closing_basis: 1025000.00
    source_text: |
      Partner Tax Basis Roll-Forward Schedule ...
```

---

## 4. Parser Behavior with Extensions

### 4.1 Extension Loading — Local First (Air-Gap Safe)

**Production systems MUST NOT rely on dynamic HTTP fetching during tax
processing.** Enterprise tax engines operate in air-gapped or firewalled
environments where outbound HTTP will fail silently, destroying schema
validation without warning.

**Required implementation:**

1. **Primary:** Load extension files from a local directory bundle.
   Parsers MUST support a configurable `extension_directory` path.
   Extensions present locally take precedence over all other sources.

2. **Secondary (optional):** If not found locally and the environment
   permits outbound HTTP, attempt to fetch from the registry URL.

3. **Fallback:** If not found by any means, treat the footnote as
   schema-tolerant `custom` and emit a warning. Do NOT fail silently —
   log the missing extension reference clearly.

**Deployment guidance for enterprise environments:**
- Bundle all required extension YAML files alongside the OTD parser
- Include extension files in your CI/CD deployment artifacts
- Never assume network availability during batch tax processing runs

### 4.2 Extension Registry URL (Reference Only)

For convenience in development and non-production environments:
```
https://opentaxdocument.org/extensions/{org-slug}/{version}/{filename}
```

### 4.3 Legacy Aliases — Smooth Core Promotion

When a custom extension type is promoted to the core catalog, the
promoted core classification MUST include a `legacy_aliases` list:

```yaml
# In the core catalog entry after promotion:
- classification: "basis_roll_forward"       # New core name
  legacy_aliases:
    - "rsm_basis_roll_forward"               # Old firm-specific name
    - "am_basis"                             # Competing firm name
  promoted_from_version: "2026.1.0"
```

Parsers MUST treat documents using any alias as equivalent to the
canonical core classification. Early adopters are NOT penalized for
using the pre-promotion name — their existing documents remain valid.

### 4.4 Namespace Collision Resolution

When two firms independently publish schemas for the same concept
(e.g., both RSM and A&M publish a `basis_roll_forward` type with
different field sets):

1. Both are valid in their respective namespaces (`rsm_basis_roll_forward`
   vs. `am_basis_roll_forward`)
2. Neither firm's schema overwrites the other's
3. The promotion process (§5) produces a unified core schema that
   is a superset of both, with all firm-specific names as aliases
4. Fields present in one firm's schema but not the other are marked
   `required: false` in the unified core schema

---

## 5. Promotion Path — Custom to Core

### 5.1 Promotion Criteria

A custom extension is a candidate for promotion to the core catalog when:
- **Adoption threshold:** Three or more independent organizations adopt
  the same classification (same name or demonstrably equivalent intent)
- **Stability:** The schema has been stable for at least one full tax year
- **Coverage:** The type addresses a compliance need not already met
  by a core classification

### 5.2 Promotion Process

1. Any organization may submit a promotion request via the OTD repository
2. Maintainers identify all known implementations and collect schemas
3. A unified schema is drafted (superset with aliases for all prior names)
4. 30-day public comment period
5. Merged into next minor version of the taxonomy
6. Prior extension names preserved as `legacy_aliases`

### 5.3 Promotion Does Not Break Existing Documents

Documents using a pre-promotion extension name are permanently valid.
Parsers that load the promoted core taxonomy will resolve the alias
transparently. No migration required.

---

## 6. Security Model

- Extension files are **declarative only** — field definitions, types, labels
- Parsers MUST NOT execute code from extension files
- No functions, scripts, or executable logic permitted
- Extension schemas may define: field names, types, required/optional,
  validation constraints (sum, range, enum), and labels
- Extensions from unknown organizations should be treated with caution
  in production; enterprise deployments should whitelist permitted extensions

---

## 7. Core vs. Extension Decision Guide

| Use Core When | Use Extension When |
|---------------|--------------------|
| Type is in the published catalog | Type is firm-specific or experimental |
| IRS instructions define the disclosure | Internal methodology governs it |
| Multiple firms encounter this type | Only your firm produces this type |
| Behavior is well-understood | Behavior varies by firm |
