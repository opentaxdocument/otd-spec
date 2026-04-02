# Open Tax Document — v0.3 Roadmap

**Status:** Draft for partner review
**Date:** 2026-04-02
**Authors:** Tom O'Sullivan, Crimson Tree Software

---

## What Was Accomplished in v0.2

The core OTD standard (TaxNode spec, K-1 taxonomy, emitter/parser specs,
derivation spec) is structurally sound. Two adversarial review passes by
an independent AI analyst (Gemini Pro) confirmed:

- All 9 critical/high/medium findings from round 1 were remediated
- The round-trip proof-of-concept passes all 5 phases (byte-identical)
- The footnote taxonomy is a strong conceptual addition

---

## Open Items for v0.3 (Seeking Partner Input)

### Footnote Taxonomy Expansion

The following footnote types need schemas developed with real practitioner input:

| Classification | Priority | Input Needed From |
|----------------|----------|------------------|
| `subpart_f_inclusion` | HIGH | International tax practitioners |
| `gilti_inclusion` | HIGH | International tax practitioners |
| `ptep_distribution` | HIGH | International tax practitioners |
| `pfic_qef_election` | HIGH | International fund practitioners |
| `eci_effectively_connected_income` | HIGH | Foreign partner practitioners |
| `fdap_income_withholding` | HIGH | Foreign partner practitioners |
| `drd_dividends_received_deduction` | HIGH | Corporate partner practitioners |
| `state_pte_tax` | HIGH | State tax practitioners |
| `state_nonresident_withholding` | HIGH | State tax practitioners |

**Key question for each type:** What structured fields does your team
actually need to complete the partner's return? What fields are reliably
present in real K-1 packages vs. buried in supplements?

### Schema Refinements (Lower Priority)

| Item | Description |
|------|-------------|
| `debt_allocation.by_activity` | Make optional; require only aggregate totals |
| `state_modifications.adjustment_category` | Add enum for CA/NY/IL decoupling types |
| `section_199a_detail.aggregation_explanation` | Add narrative field |
| `form_metadata.amendment_type` | Add `full_replacement` vs. `delta` distinction |
| `source_text` format | Allow array of strings for table preservation |
| Confidence scoring heuristics | Define standard rules for confidence [0-1] |
| `mef_tag` node annotations | Annotate key taxonomy nodes with IRS MeF XML tags |

### Extension Registry

The Extension Registry spec (`otd-extension-registry-spec.md`) defines
how firms can publish custom footnote type schemas. Before v1.0, we need:

1. A hosted registry URL (opentaxdocument.org)
2. A submission and review process
3. At least one reference extension from a partner firm

---

## Questions for CPA Firms' Review

1. **Footnote coverage**: Which investor-level footnotes do you see most
   frequently in complex PE/RE/hedge fund K-1 packages that are NOT in
   our current 12-type catalog?

2. **Footnote schemas**: For the types you see most often, what are the
   critical structured fields your compliance software needs?

3. **State footnotes**: With PTE elections active in 30+ states, what does
   a `state_pte_tax` footnote schema need to capture for your team?

4. **International**: Are Subpart F, GILTI, and PTEP the right three
   international footnote types, or are there others (e.g., §965 transition
   tax, CFC attribution, BEAT)? What does your team need in the structured
   layer?

5. **Adoption path**: What would your team need to begin emitting or
   consuming OTD documents for K-1 packages? What's the first workflow
   that would benefit?

---

## Proposed v0.3 Timeline

| Milestone | Target |
|-----------|--------|
| Partner review kickoff (RSM, A&M) | +2 weeks |
| Footnote schema workshops | +4 weeks |
| v0.3 draft incorporating feedback | +6 weeks |
| v0.3 adversarial review | +7 weeks |
| v0.3 public release | +8 weeks |
