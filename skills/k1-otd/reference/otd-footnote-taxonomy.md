# Open Tax Document — Investor-Level Footnote Taxonomy v0.3 (RC-1)

**Status:** RELEASE CANDIDATE
**License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
**Authors:** Tom O'Sullivan, Second Wind
**Date:** 2026-04-02
**Changelog:**
  v0.3 RC-1 — Killed `delta` amendment scope (mandate full_replacement only).
  PTEP: added `ptep_year_of_origin` and full §959(c) group enum.
  GILTI: added `allocable_expenses_in_k3` flag and §861 apportionment note.
  PFIC §1291: added `requires_historical_data` flag.
  state_pte_tax: added `source_entity_name/ein` for tiered PTE credit pass-through.
  state_nonresident_withholding: added `exemption_status` enum.
  drd: added `section_246a_reduction`.

---

## 1. Purpose and Scope

### 1.1 The Footnote Problem

A K-1 package delivered to an investor contains three tiers:

1. **Face** — The printed Schedule K-1 form (Parts I-III)
2. **Overflow Statements** — Box-level "See Statement" attachments
3. **Investor-Level Footnotes** — General per-partner disclosures not
   anchored to a specific box

Footnotes are the hardest tier: semi-structured, per-partner, legally
significant, and currently emitted only as PDF prose.

### 1.2 The OTD Approach

Every footnote has two mandatory layers:

1. **Structured Layer** — Machine-readable fields for computation and automation
2. **Source Text Layer** — Verbatim original prose for legal defensibility

`source_text` may be a single string OR an array of strings/rows to
preserve tabular structure when PDF extraction produces aligned columns.

### 1.3 Document Position

```yaml
otd: { ... }
form_metadata: { ... }
body: { ... }
statements: [ ... ]
footnotes:               # Investor-level footnotes
  - type: footnote
    ...
```

---

## 2. Footnote Node Structure

```yaml
- type: footnote
  id: "fn-001"                              # Unique within document
  classification: "{footnote_type}"         # REQUIRED
  extension_ref: null                       # Set if using Extension Registry type
  semantic:
    id: "{classification}.{document_id}"
    label: "Human-readable title"
    description: "Brief description"
  form:
    attachment: true
    attachment_sequence: 1
  cross_references:
    - "part_iii.box_20.U"
  tax_year: 2025
  supersedes_id: null                       # ID of prior footnote this replaces
  amendment_scope: "original"               # original | full_replacement ONLY
  structured:
    { ... }
  source_text: |
    Original footnote text verbatim.
  confidence: 0.95
  extraction_method: "ai_structured"        # manual | ai_structured | rule_based
```

### 2.1 Amendment Versioning

**OTD mandates immutability.** There is no partial (`delta`) amendment.

`amendment_scope` values:
- `original` — First issuance (default)
- `full_replacement` — This footnote entirely replaces the prior one

When an amended K-1 modifies a footnote:
- Set `amendment_scope: "full_replacement"`
- Set `supersedes_id` to the `id` of the prior footnote being replaced
- The new footnote is complete and self-contained — no merging required

**Why no `delta`?** YAML array merging semantics are undefined without
RFC 6902-style patch operations. Partial merges on nested arrays (e.g.,
`cfcs[]` in GILTI, `ptep_by_cfc[]`) are non-deterministic and will corrupt
legal tax data. Storage is cheap; data corruption is catastrophic.

**Orphaned `supersedes_id`:** If a parser encounters a `supersedes_id`
that does not resolve to a known footnote (e.g., amended K-1 received
without the original), the parser MUST:
1. Emit a warning: "supersedes_id {id} not resolved — treating as original"
2. Process the footnote as authoritative (do not reject)
3. Flag the document for reconciliation review

### 2.2 Footnote Chain Reconstruction

To reconstruct the authoritative state of a footnote across amendments:
1. Collect all footnotes with the same classification from all document versions
2. Build the supersession chain by following `supersedes_id` links
3. The footnote with no `supersedes_id` pointing TO it is the current version
4. All superseded footnotes are preserved in the audit trail

---

## 3. Footnote Classification Catalog

---

### 3.1 section_754_adjustment

**Purpose:** §754/§743(b) basis adjustment disclosure.
**Cross-references:** Box 20, Code U

```yaml
structured:
  adjustment_type: "743b"                   # 743b | 734b | 754_election
  triggering_event: "secondary_market_transfer"
  triggering_date: "2025-03-15"
  total_adjustment_amount: 250000.00
  asset_adjustments:
    - asset_description: "Real estate portfolio"
      asset_class: "real_property"
      adjustment_amount: 180000.00
      remaining_recovery_period: 27.5
      annual_amortization: 6545.45
  partner_allocable_share: 250000.00
  cumulative_prior_adjustments: 0.00
```

---

### 3.2 firpta_withholding

**Purpose:** FIRPTA withholding and USRPI disclosure.
**Cross-references:** Box 20; K-3 Part I

```yaml
structured:
  withholding_required: true
  withholding_rate: 0.21
  usrpi_amount: 500000.00
  withholding_amount: 105000.00
  form_8288_filed: true
  form_8288_date: "2025-04-15"
  usrpi_description: "LP interest in Greenfield Denver Holdings LLC"
  treaty_exemption: false
  treaty_country: null
  partner_is_foreign: true
  partner_country: "DE"
```

---

### 3.3 debt_allocation

**Purpose:** Partnership debt allocation to partners.
**Cross-references:** Part II, Item K1

```yaml
structured:
  methodology: "regulations_1752"
  recourse_total: 10000000.00
  nonrecourse_total: 25000000.00
  qualified_nonrecourse_total: 5000000.00
  partner_allocations:
    recourse: 950000.00
    nonrecourse: 2375000.00
    qualified_nonrecourse: 475000.00
  minimum_gain_chargeback_applicable: true
  partner_minimum_gain: 0.00
  allocation_basis: "ending_capital_percentage"
  by_activity:                              # OPTIONAL
    - activity: "Denver Portfolio"
      recourse: 500000.00
      nonrecourse: 1500000.00
```

---

### 3.4 carried_interest_section_1061

**Purpose:** §1061 carried interest recharacterization.
**Cross-references:** Box 9a; Box 20, Code AM

```yaml
structured:
  applies_to_partner: true
  is_api_holder: true
  holding_period_years: 2.5
  one_year_gain: 150000.00
  three_year_gain: 85000.00
  recharacterized_amount: 65000.00
  capital_interest_exclusion: 0.00
```

---

### 3.5 ubti_unrelated_business_taxable_income

**Purpose:** UBTI for tax-exempt partners.
**Cross-references:** Box 20, Code V

```yaml
structured:
  total_ubti: 45000.00
  by_activity:
    - activity_description: "Leveraged real estate"
      ubti_amount: 30000.00
      debt_financed: true
      acquisition_indebtedness: 5000000.00
      debt_financed_percentage: 0.60
  form_990t_required: true
  silo_applicable: true
  silo_buckets:
    - bucket_id: "1"
      activity: "Leveraged real estate"
      amount: 30000.00
```

---

### 3.6 passive_activity_grouping

**Purpose:** Passive activity grouping under Reg. §1.469-4.
**Cross-references:** Box 22; Box 23

```yaml
structured:
  grouping_election_made: true
  activities:
    - activity_id: "ACT-1"
      activity_name: "Denver Real Estate Portfolio"
      activity_type: "rental_real_estate"
      material_participation: false
      at_risk_applicable: true
      at_risk_amount: 750000.00
      passive_loss: -25000.00
```

---

### 3.7 state_apportionment

**Purpose:** Multi-state income apportionment.

```yaml
structured:
  apportionment_method: "combined_sales_factor"
  states:
    - state_code: "NY"
      apportionment_percentage: 0.35
      ordinary_income: 52500.00
      rental_income: -8750.00
      state_modifications:
        - description: "NY addition — bonus depreciation"
          amount: 12000.00
          adjustment_category: "bonus_depreciation_decoupling"
      composite_return_eligible: true
```

**`adjustment_category` enum:**
`bonus_depreciation_decoupling` | `section_179_decoupling` |
`interest_income_exclusion` | `irc_conformity_difference` |
`net_operating_loss_difference` | `other`

---

### 3.8 section_199a_detail

**Purpose:** §199A QBI deduction detail.
**Cross-references:** Box 20, Code Z

```yaml
structured:
  activities:
    - activity_name: "Greenfield Operations LLC"
      sstb: false
      qbi: 150000.00
      w2_wages: 80000.00
      ubia: 500000.00
  aggregation_election: false
  aggregation_explanation: null             # REQUIRED when aggregation_election: true
  total_combined_qbi: 150000.00
  total_combined_w2_wages: 80000.00
  total_combined_ubia: 2500000.00
```

---

### 3.9 form_926_transfer_to_foreign_corp

**Purpose:** Form 926 — Transfer to Foreign Corporation.
**Cross-references:** Box 20, Code ZZ

```yaml
structured:
  transferor_name: "ABC Partners LP"
  transferee_name: "XYZ Holdings GmbH"
  transferee_country: "DE"
  transfer_date: "2025-06-15"
  form_926_filed: true
  property_transferred:
    - description: "Patent portfolio"
      fmv: 5000000.00
      adjusted_basis: 500000.00
      gain_recognized: 4500000.00
      section_367_applies: true
```

---

### 3.10 nii_net_investment_income

**Purpose:** §1411 NII detail.
**Cross-references:** Box 20, Code Y

```yaml
structured:
  total_nii: 195300.00
  by_category:
    - category: "interest_dividends_royalties"
      gross_income: 55500.00
      allocable_deductions: -8200.00
      net: 47300.00
    - category: "net_gain_disposition"
      gross_income: 148000.00
      net: 148000.00
```

---

### 3.11 section_163j_business_interest

**Purpose:** §163(j) business interest limitation.
**Cross-references:** Box 13 Code K; Box 20 Codes N, AE, AF

```yaml
structured:
  allocable_business_interest_income: 18500.00
  allocable_adjusted_taxable_income: 425000.00
  allocable_business_interest_expense: 22000.00
  excess_business_interest_expense: 3500.00
  excess_taxable_income: 0.00
  real_property_trade_or_business_election: false
```

---

### 3.12 subpart_f_inclusion

**Purpose:** Subpart F income inclusion (§951).
**Cross-references:** K-3 Part VI; Box 20

```yaml
structured:
  total_subpart_f_inclusion: 125000.00
  cfcs:
    - cfc_name: "Offshore Holdings Ltd"
      cfc_country: "KY"
      ein_or_reference: "CFC-001"
      subpart_f_income: 75000.00
      fphci: 75000.00
      fbcsi: 0.00
      fbcsvi: 0.00
      partner_ownership_percentage: 0.15
      partner_allocable_amount: 75000.00
  section_960_deemed_paid_credit_available: true
  ptep_generated: true
  form_5471_attached: true
```

---

### 3.13 gilti_inclusion

**Purpose:** GILTI §951A inclusion.
**Cross-references:** K-3 Part VI; Box 20

**Note on §861 Expense Apportionment:** The data required to apportion
partner-level expenses (interest, R&D, stewardship) against GILTI under
§861 is carried in the K-3 (Part II, Part III). Parsers should resolve
the K-3 reference from Box 16 for full §861 apportionment capability.

```yaml
structured:
  total_gilti_inclusion: 85000.00
  tested_income: 200000.00
  net_deemed_tangible_income_return: 50000.00
  qualified_business_asset_investment: 500000.00
  partner_allocable_gilti: 85000.00
  high_tax_exclusion_elected: false
  section_250_deduction_eligible: true
  section_250_deduction_amount: 42500.00
  foreign_tax_credit_basket: "gilti"
  deemed_paid_taxes: 15000.00
  effective_foreign_tax_rate: 0.12
  allocable_expenses_in_k3: true            # §861 apportionment data in K-3 reference
  cfcs:
    - cfc_name: "Offshore Holdings Ltd"
      cfc_country: "KY"
      tested_income: 120000.00
      tested_loss: 0.00
      qbai: 300000.00
```

---

### 3.14 ptep_distribution

**Purpose:** Previously Taxed Earnings and Profits (PTEP) distributions.
**Cross-references:** K-3 Part V; Box 6a; Box 20

**§959(c) Group Enum** (matches IRS official groups):
`section_959c1_previously_taxed` |
`section_965a_ptep` |
`section_965b_ptep` |
`section_951a_ptep` |
`section_245a_ptep` |
`section_959c2_previously_taxed` |
`section_956_ptep` |
`section_956a_ptep` |
`reclassified_section_965a_ptep` |
`reclassified_section_965b_ptep`

```yaml
structured:
  total_ptep_distributed: 50000.00
  ptep_by_cfc:
    - cfc_name: "Offshore Holdings Ltd"
      cfc_country: "KY"
      distribution_date: "2025-09-15"
      ptep_group: "section_951a_ptep"       # Must use §959(c) enum above
      ptep_year_of_origin: 2023             # REQUIRED — year PTEP was generated
      gross_distribution: 50000.00
      ptep_amount: 48000.00
      dividend_amount: 2000.00
      section_986c_gain_loss: 0.00          # Currency gain/loss on PTEP
      foreign_currency: "USD"
      exchange_rate_at_distribution: 1.0
      exchange_rate_at_inclusion: 1.0       # Rate when PTEP was originally included
  excluded_from_income: true
```

---

### 3.15 pfic_qef_election

**Purpose:** PFIC/QEF election details.
**Cross-references:** K-3 Part VII

```yaml
structured:
  pfics:
    - pfic_name: "Luxembourg Holdco SARL"
      pfic_country: "LU"
      reference_id: "PFIC-001"
      election_type: "qef"                  # qef | mark_to_market | section_1291 | none
      qef_ordinary_earnings: 12000.00
      qef_net_capital_gain: 8000.00
      fmv_beginning: 1000000.00
      fmv_ending: 1150000.00
      form_8621_required: true
    - pfic_name: "Cayman Fund Ltd"
      pfic_country: "KY"
      election_type: "section_1291"
      distributions_received: 5000.00
      requires_historical_data: true        # Prior 3yr distribution history needed
      holding_period_start: "2021-03-15"    # For Form 8621 Part V throwback calc
      form_8621_required: true
```

---

### 3.16 eci_effectively_connected_income

**Purpose:** ECI for foreign partners in US trade or business.
**Cross-references:** K-3 Part X; Box 1; Box 20

```yaml
structured:
  total_eci: 150000.00
  partner_is_foreign: true
  partner_country: "DE"
  eci_by_activity:
    - activity: "Denver real estate operations"
      eci_net: 125000.00
  withholding_required: true
  withholding_rate: 0.37
  form_8804_filed: true
  form_8805_filed: true
  treaty_exemption: false
  branch_profits_tax_applicable: false
```

---

### 3.17 fdap_income_withholding

**Purpose:** FDAP §1441/§1442 withholding for foreign partners.
**Cross-references:** K-3 Part X; Box 5; Box 6a; Box 7

```yaml
structured:
  total_fdap: 55500.00
  fdap_by_category:
    - category: "interest"
      gross_amount: 18500.00
      withholding_rate: 0.30
      treaty_rate: null
      treaty_country: null
      net_after_withholding: 12950.00
      form_1042s_code: "01"
    - category: "dividends"
      gross_amount: 32000.00
      withholding_rate: 0.15
      treaty_rate: 0.15
      treaty_country: "DE"
      net_after_withholding: 27200.00
      form_1042s_code: "06"
  form_1042_filed: true
  chapter_3_withholding: 42650.00
  chapter_4_fatca_withholding: 0.00
  w8_form_on_file: "w8ben"
```

---

### 3.18 drd_dividends_received_deduction

**Purpose:** §243/§245/§245A DRD for corporate partners.
**Cross-references:** Box 6a; Box 6b; Box 20

```yaml
structured:
  total_eligible_dividends: 32000.00
  drd_by_section:
    - section: "243"
      gross_dividends: 20000.00
      drd_percentage: 0.65
      drd_amount: 13000.00
      ownership_percentage: 0.25
    - section: "245a"
      gross_dividends: 12000.00
      drd_percentage: 1.00
      drd_amount: 12000.00
      ownership_percentage: 0.15
      ptep_component: 0.00
  total_drd: 25000.00
  hybrid_dividend_taint: 0.00
  extraordinary_disposition_account: 0.00
  section_246a_reduction: 0.00             # Reduction for debt-financed portfolio stock
```

---

### 3.19 state_pte_tax

**Purpose:** Pass-Through Entity tax election credits.
**Cross-references:** Box 13; Box 18

```yaml
structured:
  elections:
    - state_code: "NY"
      election_made: true
      election_year: 2025
      pte_tax_paid: 45000.00
      partner_credit_amount: 45000.00
      credit_type: "direct_credit"
      addback_required: false
      state_form: "IT-653"
      source_entity_name: null             # Name of lower-tier entity that paid tax
      source_entity_ein: null              # EIN of lower-tier entity (tiered structures)
    - state_code: "CA"
      election_made: true
      pte_tax_paid: 11250.00
      partner_credit_amount: 11250.00
      credit_type: "direct_credit"
      state_form: "FTB 3804-CR"
      source_entity_name: "Greenfield CA Operations LP"
      source_entity_ein: "XX-XXXXXXX"     # Passed-through from lower-tier partnership
  total_pte_tax_paid: 56250.00
  total_partner_credit: 56250.00
```

---

### 3.20 state_nonresident_withholding

**Purpose:** Nonresident state withholding.
**Cross-references:** Box 13; Standalone

```yaml
structured:
  withholdings:
    - state_code: "CA"
      withholding_required: true
      withholding_rate: 0.0733
      gross_income_subject: 81500.00
      withholding_amount: 5973.95
      exemption_status: "none"             # none | composite_election | waiver_on_file
      state_form: "CA 592-PTE"
      payment_date: "2025-09-15"
    - state_code: "NY"
      withholding_required: true
      withholding_amount: 0.00
      exemption_status: "composite_election"  # Explains zero withholding
      state_form: "IT-2658"
  total_withholding: 5973.95
```

---

### 3.21 custom

**Purpose:** Catch-all. See Extension Registry for promoting to named schema.

```yaml
structured:
  custom_classification: "eip_election_notice"  # REQUIRED
  custom_fields:
    election_type: "Early Inclusion Period"
    election_code: "6031(b)"
```

---

## 4. Footnote Extraction Guide

### 4.1 For AI Agents

1. Identify footnote boundaries
2. Classify against catalog or assign `"custom"`
3. Populate structured fields; use `null` for absent fields
4. Preserve source text verbatim
5. Assign `cross_references` to relevant K-1 nodes
6. Rate confidence per §4.2 heuristics
7. Set `extraction_method: "ai_structured"`

### 4.2 Confidence Scoring Heuristics

| Score | Method | Criteria |
|-------|--------|----------|
| 1.00 | `rule_based` | Exact regex/pattern match |
| 0.90 | `ai_structured` | Schema validates; all required fields populated |
| 0.80 | `ai_structured` | Schema validates; some optional fields null |
| 0.70 | `ai_structured` | Schema validates; aggregate totals only |
| 0.60 | `ai_structured` | Schema validates; significant fields inferred |
| < 0.60 | `ai_structured` | Schema fails or critical fields missing — human review required |

---

## 5. Validation Rules

| Rule | Severity | Description |
|------|----------|-------------|
| `classification_required` | Error | Every footnote must have a classification |
| `source_text_required` | Error | Every footnote must have non-empty source text |
| `structured_required` | Error | Every footnote must have a structured block |
| `custom_classification_required` | Error | Custom footnotes must have `custom_classification` |
| `no_delta_amendment` | Error | `amendment_scope: delta` is prohibited |
| `supersedes_id_resolves` | Warning | Orphaned supersedes_id — treat as original, flag for review |
| `id_unique` | Error | Footnote IDs unique within document |
| `aggregation_explanation_required` | Error | Required when `aggregation_election: true` |
| `ptep_year_of_origin_required` | Error | Required on every `ptep_by_cfc` entry |

---

## 6. Classification Quick Reference

| Classification | Trigger | Key Fields |
|----------------|---------|------------|
| `section_754_adjustment` | §754 election/transfer | `adjustment_amount`, `asset_adjustments` |
| `firpta_withholding` | Foreign partners / USRPI | `withholding_amount`, `usrpi_amount` |
| `debt_allocation` | Complex liabilities | `partner_allocations`, `methodology` |
| `carried_interest_section_1061` | Carried interest | `recharacterized_amount` |
| `ubti_unrelated_business_taxable_income` | Tax-exempt investors | `total_ubti`, `silo_buckets` |
| `passive_activity_grouping` | Multi-activity | `activities[]` |
| `state_apportionment` | Multi-state | `states[].apportionment_percentage` |
| `section_199a_detail` | QBI deduction | `qbi`, `w2_wages`, `ubia` |
| `form_926_transfer_to_foreign_corp` | Foreign corp transfer | `gain_recognized` |
| `nii_net_investment_income` | §1411 surtax | `total_nii` |
| `section_163j_business_interest` | §163(j) | `excess_business_interest_expense` |
| `subpart_f_inclusion` | CFC / §951 | `cfcs[]`, `total_subpart_f_inclusion` |
| `gilti_inclusion` | §951A GILTI | `total_gilti_inclusion`, `allocable_expenses_in_k3` |
| `ptep_distribution` | PTEP distributions | `ptep_by_cfc[].ptep_year_of_origin`, `ptep_group` |
| `pfic_qef_election` | PFIC interests | `pfics[]`, `election_type` |
| `eci_effectively_connected_income` | Foreign partner ECI | `total_eci`, `withholding_rate` |
| `fdap_income_withholding` | §1441 FDAP | `fdap_by_category[]`, `chapter_3_withholding` |
| `drd_dividends_received_deduction` | Corporate partners | `total_drd`, `section_246a_reduction` |
| `state_pte_tax` | PTE elections | `elections[]`, `source_entity_name` |
| `state_nonresident_withholding` | NR withholding | `withholdings[]`, `exemption_status` |
| `custom` | Anything else | `custom_classification`, `custom_fields` |
