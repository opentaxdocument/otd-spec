
# Open Tax Document — Roadmap

**Status:** Draft — public, actively changing
**Updated:** 2026-07-28
**Authors:** Tom O'Sullivan, Crimson Tree Software

> This project is built in the open. This roadmap states what is actually
> implemented, what is partially implemented, and what is known to be broken.
> Items are marked from evidence in the tree, not from intent.

---

## Where The Project Stands

The core standard — TaxNode specification, K-1 and K-3 taxonomies, and the
emitter, parser, and derivation specifications — is structurally complete and
has survived repeated adversarial review.

The significant change since the last roadmap is that **validation is now
taxonomy-driven rather than hand-coded**. `skills/k1-otd/scripts/constraint_engine.py`
reads its rules from the taxonomy YAML, so a rule added to the taxonomy takes
effect on the next run without new Python. It enforces:

- **Schema binding** — a node the taxonomy declares must match its declared
  type, semantic identity, form placement, and value type
- **Physical completeness** — every declared field must be present
- **Constraints** — `sum`, `range`, `required_field`, `conditional_required`,
  with the null/absent algebra of parser-spec §4.7
- **Statement schemas** — required classification and record fields
- **Coded-entry uniqueness** — no duplicate codes within a box
- **Capital-account integrity** — completeness, alias ambiguity, continuity

Test coverage is **33 tracked cases across three suites**, plus a mirror-parity
checker and a deliberately non-conforming fixture at `tests/fixtures/hostile-k1/`.

### Review History

The standard has been through repeated independent adversarial review. The
most recent round comprised **four passes**, each returning NO-GO with
evidence, each remediated:

| Pass | Principal findings | Outcome |
|---|---|---|
| 1 | Production assembler emitted IRS-nonconforming Box 16, Item M, Item K3 | Remediated |
| 2 | Validation was presence-only; content never checked | Remediated |
| 3 | Range rules failed open; shipped sum rules were inert; mirror staleness | Remediated |
| 4 | No taxonomy-schema binding; a misspelled rule path silently disabled its rule | Binding remediated; path preflight **open** |

Earlier rounds against v0.2 were conducted by a different reviewer. The
attribution in the previous version of this roadmap was out of date.

**The most useful defects were found by the mechanism, not by the author.**
The constraint engine caught a proof emitter that suppressed reported zeros,
a statement missing taxonomy-required fields, a malformed condition string,
and a semantic identifier that disagreed with its own taxonomy — none of
which had a hand-written check.

---

## Known Gaps

Open, in rough order of severity.

### A misspelled path in a taxonomy constraint silently disables that rule

A rule referencing `part_iii.box_6a_typo` resolves as *absent* rather than
*undeclared*, so it is skipped rather than reported. A spelling error can
therefore disable a validation rule with no error, warning, or failure.

This directly undercuts the generic-engine promise. Fix in progress: preflight
every constraint path against the **taxonomy** before validating documents —
declared-but-absent skips legitimately, undeclared is a hard error.

### §4.7 `required` semantics are not reconciled

Parser-spec §4.7 states that a present-null node **passes** a required
constraint, because the node is present. The engine currently treats a null
value as missing. The likely resolution is a declarative `non_null: true` on
constraints that genuinely require a value, rather than silently redefining
generic required semantics. Under review.

### K-3 has no implementation coverage

`taxonomies/irs-k3-1065-2025.yaml` is present and structurally derived, but
there is no K-3 proof, no fixture, and no validation exercise. K-3 support is
declarative only.

### The validation profile is not normative

Four rules in the reference implementation are not derivable from the taxonomy
alone: duplicate-code rejection, mandatory `semantic.role` on statements,
capital-account completeness, and the not-yet-observed sentinel rule. They are
documented in `spec/otd-parser-spec.md` §7.1 so a third-party implementation
can match them, but they are **not yet normative** — a conforming third-party
validator may be more permissive than ours. Proposed for promotion in v0.3.

---

## Open Items for v0.3

### Footnote Schemas — Practitioner Validation

The catalog now contains **21 classifications** (20 concrete plus `custom`).
The types previously listed as needing authoring are all present:

| Domain | Classifications |
|---|---|
| Domestic | §754 adjustment, FIRPTA withholding, debt allocation, §1061 carried interest, UBTI, passive activity grouping, state apportionment, §199A detail, Form 926, NII, §163(j) |
| International | Subpart F, GILTI, PTEP distribution, PFIC/QEF election, ECI, FDAP withholding |
| Corporate | Dividends Received Deduction |
| State | PTE tax, nonresident withholding |

**What is still needed is not authoring — it is validation.** The structured
fields were chosen from the IRS instructions and our own reading of what a
preparer needs. They have not been checked against what practitioners
actually receive in real K-1 packages.

The question for each type: *do these fields let your team complete the
partner's return, and are they reliably present in real packages — or buried
in supplements we haven't modelled?*

### Schema Refinements — Verified Status

| Item | Status |
|---|---|
| `debt_allocation.by_activity` optional | **Done** — marked OPTIONAL; aggregate totals sufficient |
| `state_modifications.adjustment_category` enum | **Done** — enum defined for decoupling types |
| `section_199a_detail.aggregation_explanation` | **Done** — narrative field, required when `aggregation_election: true` |
| `form_metadata.amendment_type` | **Resolved by removal** — `delta` scope dropped; `full_replacement` only |
| `source_text` array form | **Done** — single string or array of rows, preserving table structure |
| `mef_tag` node annotations | **Partial** — the convention is documented in the taxonomy header, but individual nodes are not yet annotated |
| Confidence scoring heuristics | **Open** — no standard rules defined for the [0-1] range |

### Extension Registry

The registry specification defines how firms publish custom footnote schemas.
Still required before v1.0:

1. A hosted registry URL
2. A submission and review process
3. At least one reference extension from a partner firm

---

## Questions for Reviewers

1. **Footnote coverage** — which investor-level footnotes appear in complex
   PE, real estate, or hedge fund K-1 packages that are *not* among the 21?

2. **Field sufficiency** — for the types you see most, do the structured
   fields cover what your compliance software needs, or is something missing?

3. **State footnotes** — with PTE elections active in 30+ states, does the
   `state_pte_tax` schema capture what your team needs, including tiered
   pass-through?

4. **International** — are Subpart F, GILTI, PTEP, PFIC, ECI, and FDAP the
   right set, or are §965 transition tax, CFC attribution, or BEAT also
   needed?

5. **Validation profile** — are the four §7.1 rules correct? Should they be
   normative in v0.3, or is one of them too strict for real documents?

6. **Adoption path** — what would your team need to begin emitting or
   consuming OTD for K-1 packages, and which workflow benefits first?

---

## Path to v0.3

Sequenced by dependency rather than by date. A public draft with dates that
slip is worse than one that states its order honestly.

| # | Milestone | Gate |
|---|---|---|
| 1 | Close the constraint-path preflight gap | A misspelled rule path fails loudly |
| 2 | Reconcile §4.7 `required` / null semantics | Spec and implementation agree |
| 3 | Practitioner review of footnote field sufficiency | Feedback from firms handling complex packages |
| 4 | Promote the §7.1 validation profile to normative | Or document why each rule stays implementation-specific |
| 5 | K-3 proof and validation coverage | K-3 exercised, not merely declared |
| 6 | Independent adversarial review of v0.3 | GO verdict |
| 7 | v0.3 public release | — |

---

## Contributing

Open an [Issue](https://github.com/opentaxdocument/otd-spec/issues) or start a
[Discussion](https://github.com/opentaxdocument/otd-spec/discussions).

Contradictions between the specification and the reference implementation are
especially welcome — several of the most valuable findings so far have been
exactly that.
