# K-1 OTD Extraction Skill

**Version:** 1.2.0
**Purpose:** Extract IRS Schedule K-1 (Form 1065) PDFs into OTD-compliant YAML documents using an inline extraction pipeline.
**Budget Ceiling:** $10.00 per extraction
**OTD Spec:** https://github.com/opentaxdocument/otd-spec

---

## 0. Conventions

| Token | Meaning |
|---|---|
| `{SKILL_ROOT}` | Directory containing this SKILL.md (`./`) |
| `{artifact_root}` | Per-run output directory (`../Artifacts/YYYYMMDD-k1-otd-slug/`) |
| `{pdf_path}` | Absolute path to the K-1 PDF being extracted |
| `{pdf_sha256}` | SHA-256 hex digest of the source PDF (output by Phase 1) |

> **SW Annotation:** `{SKILL_ROOT}` resolves to the skill's install path. Set `{artifact_root}` to a dated slug under your Artifacts directory.

---

## 1. Prerequisites

```bash
pip install pdfplumber ruamel.yaml
```

**Pre-Flight (Mandatory — Do This Before Any Other Step):**

Before reading the PDF, before writing any fragment, before reasoning about any code or box:

```
AgentCommand.IngestFileIntoContext {SKILL_ROOT}/reference/otd-spec-v0.2.yaml
AgentCommand.IngestFileIntoContext {SKILL_ROOT}/reference/irs-k1-1065-2025.yaml
AgentCommand.IngestFileIntoContext {SKILL_ROOT}/reference/otd-footnote-taxonomy.md
AgentCommand.IngestFileIntoContext {SKILL_ROOT}/reference/proof-emitted.otd.yaml
```

These four files are the ground truth. Every TaxNode you emit, every footnote classification you assign, and every field you name must be grounded in this spec — not in memory, not in prior session output, not in this SKILL.md alone.

**Failure mode to avoid:** Producing a YAML that looks plausible but uses an invented schema (`k1.whitepaper.v1.1`, custom top-level keys, flat dicts without TaxNode wrappers). This is what happens when you skip the pre-flight. The OTD spec and taxonomy are the contract.

Reference files (read-only — do not modify):
```
{SKILL_ROOT}/reference/
├── otd-spec-v0.2.yaml           # TaxNode primitives (6 types) — LOAD FIRST
├── otd-footnote-taxonomy.md     # 21 footnote classifications — LOAD FIRST
├── irs-k1-1065-2025.yaml        # 200+ K-1 codes — LOAD FIRST
├── proof-emitted.otd.yaml       # Golden reference output — LOAD FIRST
├── otd-emitter-spec.md          # Emitter implementation rules
├── otd-parser-spec.md           # Parser implementation rules
└── otd-derivation-spec.md       # Taxonomy derivation rules
```

**Authoritative Web References:**
- **IRS Schedule K-1 (1065) Instructions:** https://www.irs.gov/instructions/i1065sk1
- **OTD Specification:** https://github.com/opentaxdocument/otd-spec

*Fallback Rule:* If an extraction code (e.g., Box 11, 13, 15, or 20) is missing from the local `irs-k1-1065-2025.yaml` taxonomy, use `WebCommand` to query the official IRS instructions URL to resolve and classify it before resorting to `_unverified`.

**Pre-emptive Extraction Tips:**
1. **Annual Code Churn (IRS):** The IRS frequently introduces new codes (e.g., Box 13 Code X and Box 20 Code ZZ were added recently). Do not panic if a code is missing from the local taxonomy; trust the fallback rule and check the live instructions.
2. **Property Distributions (IRS):** If you extract **Box 19 Code C (Other Property)**, the IRS now requires Form 7217. Be highly vigilant for accompanying footnote statements detailing the property distributed and ensure they are captured.
3. **Dual-Layer Footnotes (OTD):** The OTD specification mandates that footnotes are first-class objects for legal defensibility. You must always provide both the `structured` object (even if empty `{}`) and the verbatim `source_text`. **Never summarize** the source text.

---

## 2. Pipeline Overview

```
PDF → Phase 1 (Python) → Phase 2 (Python) → Phase 3 (Python/Inline AI)
    → Phase 4 (Python) → Phase 5 (Adversary w/ Patching) → output.otd.yaml
```

**Core Rules:**
1. Extract PDF text ONCE (Phase 1). The agent reads from disk — never re-ingest the PDF.
2. Never guess. Use `_unverified` markers. The data must be correct or explicitly flagged.
3. Self-Correction: Every JSON output MUST include an `"_unmapped_source_data": []` array containing any verbatim text not extracted.
3. Use inline sequential turns (Read + Write in one block), followed by context suppression.

---

## 3. Directory Layout

Create `{artifact_root}` before starting:

```
{artifact_root}/
├── text_blocks/
│   ├── page_index.json          # SHA-256, page count, type hints
│   └── page_01.txt ... page_NN.txt
├── fragments/
│   ├── page_manifest.json       # Phase 2 output
│   ├── face_page.json           # Section A
│   ├── overflow_statements.json # Section B
│   ├── footnotes_a.json         # Section C
│   ├── footnotes_b.json         # Section D
│   └── state_schedules.json     # Section E
├── output.otd.yaml              # Final OTD document
├── output.confidence.json       # Confidence manifest
├── adversarial_review.json      # Adversary audit report
├── adversarial_patch.json       # Adversary automated corrections
└── RUN_SUMMARY.json             # Run manifest
```

---

## 4. Phase 1 — PDF Text Extraction

**Method:** Deterministic Python — zero AI tokens.

```bash
python {SKILL_ROOT}/scripts/phase1_extract_text.py \
  --pdf "{pdf_path}" \
  --out "{artifact_root}"
```

**Verify:** `{artifact_root}/text_blocks/page_index.json` exists and `total_pages > 0`.

---

## 5. Phase 2 — Page Manifest

**Method:** Deterministic Python — zero AI tokens.

```bash
python {SKILL_ROOT}/scripts/phase2_classify.py \
  --index "{artifact_root}/text_blocks/page_index.json" \
  --out "{artifact_root}/fragments/page_manifest.json"

python {SKILL_ROOT}/scripts/build_mini_taxonomy.py \
  --text-dir "{artifact_root}/text_blocks" \
  --master-taxonomy "{SKILL_ROOT}/reference/irs-k1-1065-2025.yaml" \
  --out "{artifact_root}/fragments/mini_taxonomy.yaml"
```

**Verify:** `page_manifest.json` and `mini_taxonomy.yaml` exist.

---

## 6. Phase 3 — Inline Section Extraction

**Method:** Hybrid. Use deterministic Python for rigid grids (Face Page, State Grids). Fallback to main agent extraction if Python fails. Use main agent for unstructured footnotes.

**Why inline:** Sub-agent delegation adds multi-turn overhead (start → pause → resume → wait) and timeout exposure for bounded extraction tasks where source data is already on disk from Phase 1. The main agent reads and writes each fragment in a single command block.

**Context compaction (mandatory):** After each fragment turn, suppress the page-text read envelopes to keep context lean. Use the dynamically generated `{artifact_root}/fragments/mini_taxonomy.yaml` instead of the full IRS taxonomy to save tokens.

**Turn discipline:**
- Read all source pages for the section.
- Reason over the content and produce the output JSON.
- Write the fragment JSON file.
- After the turn completes: suppress the page-text read envelopes.

**Execute 5 turns sequentially: A → B → C → D → E.**

> **SW Annotation:** Run each section as a single `<Run>` block with multiple `TextFileCommand.ReadFile` reads followed immediately by one `TextFileCommand.SaveFile` write. After each turn, issue `[Context] suppress <MessageID>` on the read results.

---

### Turn A: Face Page
**Method:** Inline AI turn (Main Agent).
**Input:** `page_01.txt`
**Output:** `{artifact_root}/fragments/face_page.json`

**Extraction Strategy:** Read the page and process it systematically by section to ensure no fields are dropped. Extract Part I, then Part II (paying close attention to the Capital Account block), and finally Part III. Ensure every field defined in the schema below is actively searched for and populated.

**Extraction schema:**
```json
{
  "form_metadata": {"tax_year": null, "fiscal_year": false, "amended": false, "final": false, "filing_status": "original"},
  "part_i": {
    "partnership_name": null, "partnership_ein": null, "_partnership_ein_masked": false,
    "irs_center": null, "publicly_traded": false
  },
  "part_ii": {
    "partner_name": null, "partner_tin": null, "_partner_tin_masked": false,
    "entity_type": null, "general_or_limited": null, "domestic_or_foreign": null,
    "share_percentages": { "profit_beginning": null, "profit_ending": null, "loss_beginning": null, "loss_ending": null, "capital_beginning": null, "capital_ending": null },
    "liabilities": { "nonrecourse_beginning": null, "nonrecourse_ending": null, "qualified_nonrecourse_beginning": null, "qualified_nonrecourse_ending": null, "recourse_beginning": null, "recourse_ending": null },
    "capital_account": { "beginning": null, "contributions": null, "current_year_net": null, "other_increase_decrease": null, "withdrawals": null, "ending": null, "basis_method": "tax|gaap|section_704b|other" }
  },
  "part_iii_face": {
    "box_1": null, "box_2": null, "box_3": null, "box_4a": null, "box_4b": null, "box_4c": null,
    "box_5": null, "box_6a": null, "box_6b": null, "box_7": null, "box_8": null, "box_9a": null,
    "box_9c": null, "box_10": null, "box_12": null
  },
  "_unmapped_source_data": []
}
```
**Layout Note:** Part III is a two-column table. Assign values by column position, not row order.
**Masking Rule:** Masked TIN/EIN (e.g., ***-**-1600) → extract as-is, set `_masked: true`. Do NOT use `_unverified` for intentional masking.
**Basis Method:** PDF text layer is unreliable for checkboxes. However, if the basis method is explicitly stated as text (e.g., 'Tax Basis') in the OCR, extract that value and omit the `_unverified` marker. Otherwise, mark `_unverified`.

---

### Turn B: Overflow Statements
**Pages:** Manifest section `overflow_statements` PLUS `page_01.txt`
**Output:** `{artifact_root}/fragments/overflow_statements.json`
**⚠️ Note:** Overflow JSON can reach 15KB+ (37+ coded entries).

**Extraction schema:**
```json
{
  "part_iii_overflow": {
    "box_11": [{"code": "A", "label": "Other portfolio income", "amount": 0.00, "statement_ref": "Stmt N"}],
    "box_13": [], "box_14": [], "box_15": [], "box_17": [], "box_18": [], "box_19": [],
    "box_20": [{"code": "Z", "label": "Section 199A information", "amount": null, "classification": "section_199a_detail"}]
  },
  "_unmapped_source_data": []
}
```
Box 20 REQUIRED — set "classification" to one of:
`section_199a_detail | form_926_transfer_to_foreign_corp | carried_interest_section_1061 | section_163j_business_interest | passive_activity_grouping | ubti_unrelated_business_taxable_income | debt_allocation | section_754_adjustment | nii_net_investment_income | unclassified_requires_review`

**Face-Page Merge Rule (Non-Negotiable):** You MUST also read `page_01.txt` (the face page) and merge any coded values found in Part III of the face page (e.g., 11A, 13A) into the respective arrays here. Do not assume the overflow statement is exhaustive.

---

### Turn C: Footnotes Section A
**Pages:** First half of manifest `footnote` type sections
**Output:** `{artifact_root}/fragments/footnotes_a.json`
**Escalate:** If any footnote confidence < 0.75, add `"_escalate": true` with specific reason.

**Extraction schema:**
```json
[
  {
    "id": "stmt-001",
    "classification": "section_199a_detail",
    "label": "Section 199A Qualified Business Income",
    "content": {
      "qbi": 150000.00,
      "w2_wages": 80000.00,
      "ubia": 500000.00
    },
    "source_text": "verbatim text from PDF (first 500 chars if long)",
    "confidence": 0.90,
    "cross_references": ["box_20_Z"],
    "extraction_method": "ai_structured",
    "_unmapped_source_data": []
  }
]
```

**Rule:** You MUST extract meaningful key-value pairs from the `source_text` and populate them into the `content` dictionary. Do not leave `content` empty unless the text has absolutely no extractable variables. Use descriptive snake_case keys for the parsed data.

**Semantic Classification Rule:** 
1. Consult `reference/otd-footnote-taxonomy.md` for the standard footnote classes. Base your `classification` on the semantic meaning of the body text, not just the header.
2. If the footnote matches a standard class (e.g., `drd_dividends_received_deduction`, `state_apportionment`), use it and extract its required key fields.
3. If the footnote does NOT match any standard class, set `"classification": "custom"` and format the `content` with `"custom_classification": "short_descriptive_name"` and `"custom_fields": { ... }`.
4. Do NOT use `unclassified_requires_review` simply because a header is novel. Read the body and classify.

---

### Turn D: Footnotes Section B
**Pages:** Second half of manifest `footnote` type sections.
**Output:** `{artifact_root}/fragments/footnotes_b.json`
Same extraction schema as Turn C.
Use the Semantic Classification Rule defined in Turn C.

---

### Turn E: State and Activity Schedules
**Method:** Deterministic Python (`scripts/extract_state_grids.py`).
**Fallback:** If script fails on complex layouts, execute as Inline AI turn.
**Pages:** `state_schedules` and `activity_schedule` manifest sections. (if AI fallback)
**Output:** `{artifact_root}/fragments/state_schedules.json`

**Extraction schema:**
```json
{
  "activity_schedule": {
    "activities": [{"name": "Activity Name", "box_1": 0.00, "box_2": null}]
  },
  "state_grids": [
    {
      "grid_type": "state_source_income|eci|ubti|qualified_ubti|nonqualified_ubti",
      "page": 14,
      "jurisdictions": [{"j": "NY", "line_1": null, "line_5": null, "line_9a": null}]
    }
  ],
  "state_tax_summary": [
    {"j": "NY", "source_income": null, "withholding": null, "composite": null}
  ],
  "_unmapped_source_data": []
}
```

---

## 7. Phase 4 — OTD Assembly

**Method:** Deterministic Python — zero AI tokens.

```bash
# Assemble
python {SKILL_ROOT}/scripts/phase4_assemble.py \
  --fragments "{artifact_root}/fragments" \
  --out "{artifact_root}/output.otd.yaml" \
  --sha256 "{pdf_sha256}"

# Validate
python {SKILL_ROOT}/scripts/validate_otd.py \
  --input "{artifact_root}/output.otd.yaml"
```

---

## 8. Phase 5 — Adversarial Review

**Method:** Separate agent sequence with a critical posture. **Must use a frontier model** (e.g., Gemini 3.1 Pro or Claude 4.6) for deep reasoning and cross-referencing across the full document assembly.

**Adversary Tasks & Patching:**
1. SPOT CHECK: Verify 10 randomly selected values in the OTD against source text.
2. UNVERIFIED AUDIT: Are existing ⚠️ markers justified? Are MORE needed?
3. CODE VALIDATION: Do all box_11/13/15/20 codes exist in the taxonomy?
4. CAPITAL CHECK: beginning + contributions + current_year_net + other + withdrawals = ending (≤$1 OK).
5. COVERAGE: Any items visible in source text that are missing from the OTD?

If the Adversary finds missing or incorrect data, it MUST emit an `adversarial_directives.json` containing a simple array of corrections (e.g., `[{"field": "Part III Box 1", "correct_value": 389675, "reason": "Missed by script"}]`).

**Phase 5b — Agent Translation:**
The Main Agent reads `adversarial_directives.json` and translates those simple instructions into a strict deep-merge dictionary format, saving it as `adversarial_patch.json`. (Do NOT use JSON Patch array syntax; the script expects a nested dictionary matching the OTD schema).

**Phase 5c — Apply Patch:**
```bash
python {SKILL_ROOT}/scripts/apply_adversary_patch.py --input "{artifact_root}/output.otd.yaml" --patch "{artifact_root}/adversarial_patch.json"
```

---

## 9. Uncertainty Protocol (Non-Negotiable)

**NEVER GUESS.** Flag uncertainty in the data itself.

| Situation | Required action |
|---|---|
| Text unclear/cut off | `"_unverified": "⚠️ HUMAN REVIEW REQUIRED: [exact location/reason]"` |
| Code not in taxonomy | Check IRS web instructions. If still unknown: `"_unverified": "⚠️ HUMAN REVIEW REQUIRED: code [X] not in taxonomy"` |
| Footnote type unclear | `"classification": "unclassified_requires_review"` + `_unverified` |
| Capital doesn't balance | `"_capital_delta": 1234.56` — preserve discrepancy, never hide it |
| TIN/EIN is masked | `"_masked": true` alongside value — do NOT use `_unverified` |

---

## 10. Architecture Notes & Lessons Learned

### Inline Extraction Turn Discipline
Each fragment section is one main-agent turn — reads and write in the same block. This eliminates sub-agent overhead entirely: no start/pause/resume cycles, no timeout risk, no wake signal latency. Context stays lean via post-turn suppression.

Do not issue a </Stop> command or similar stop sequence until full extraction, review, and remediation is complete.

### Fragment Size Reference
| Section | Pages | Typical Output Size | Notes |
|---|---|---|---|
| A — Face Page | 1 | ~2KB | Fast write |
| B — Overflow | 4 | 15–20KB | Slow write — JSON volume |
| C — Footnotes A | 6 | 6–8KB | Normal |
| D — Footnotes B | 6 | 4–6KB | Normal |
| E — State/Activity | 2 | 30–40KB | Large grid — slow write |

### When to Use AI Workers vs. Python Scripts
| Task | Method | Why |
|---|---|---|
| Page classification (Phase 2) | **Python** | Deterministic — no reasoning value |
| Face page / Overflow / Footnotes / State | **Main agent inline** | Layout judgment, semantic classification |
| OTD assembly & Validation (Phase 4) | **Python** | Structural merge, rule checks |
| Adversarial review | **Swarm or SubAgent** | Skeptical reasoning over full document |


## State Schedule Parsing & Parser Library
State schedule grids (e.g., 50-state income, withholding, and composite summaries) vary drastically depending on the K-1 issuer and the OCR engine's output. They may appear as pipe-delimited (`|`), space-aligned columns, or contain split/wrapping column headers (e.g., `ALABAM | A`).

**Mandatory Approach:**
1. **Analyze First:** Always inspect the actual text block format of the state schedule before writing your extraction script. Do not assume a uniform or previously seen structure.
2. **Build a Parser Library:** Do not discard successful grid parsing logic after a run. Abstract your successful parsing strategies into reusable functions and save them to `D:/SecondWind/Skills/k1-otd/scripts/state_grid_parsers.py`.
3. **Reuse First:** When encountering a state schedule, review `state_grid_parsers.py` first to see if an existing strategy (e.g., pipe-delimited row alignment, regex-based spacing) can cleanly parse the current format before writing a custom parser from scratch.


## Final Step: Preparer's Summary
After the K-1 extraction is complete, subjected to adversarial review, and mathematically reconciled (zero-variance tie-out), you must generate a final "Preparer's Summary" artifact (e.g., `preparers_summary.md`). 

This summary acts as a professional handover to the human tax preparer and must:
1. **Identify Critical Tax Matters:** Highlight any data triggering mandatory supplemental filings (e.g., Reportable Transactions for Form 8886, Foreign Transfers for Form 926, General Business Credits for Form 3800, QSBS/Section 1202 gains).
2. **Highlight Complex Adjustments:** Point out off-face or complex allocations (e.g., Section 163(j) EBIE, Section 743(b)/(e) EIP rules, Section 59(e)(2) expenditures, Foreign Tax details).
3. **Summarize SALT Footprint:** Detail the scale of the state footprint and call out significant withholding or composite payments that must be claimed.
4. **Action-Oriented:** Format the output clearly so the tax professional knows exactly what out-of-the-ordinary actions are required to clear the return.


## Mandatory Quality Gates
Before declaring the extraction complete and generating the Preparer's Summary, you must pass two internal quality gates:

1. **Adversarial Self-Review:** Assume the persona of a "Senior Technical Auditor." Actively search your extracted fragments for:
   - *Silent data truncation* (e.g., did a 50-state grid drop rows due to OCR or pipe-character formatting?).
   - *Ignored fallback protocols* (e.g., did you mark an item `_unverified` without executing a `WebCommand` search of IRS instructions?).
   - *Orphaned footnote data* (e.g., QSBS gains or 1231 adjustments buried in text that belong in the aggregate income totals).

2. **Zero-Variance Tie-Out:** You must programmatically prove your extraction's fidelity. Write a script to reconcile the Current Year Net Income (Part II, Box L) against the net sum of all extracted Part III items (Face + Overflow + Footnote adjustments). You cannot proceed to final delivery until the calculated variance is exactly 0.00.



## OTD Output Contract (Schema v0.2; Envelope version 0.1)

The primary deliverable for every K-1 extraction is `output.otd.yaml` — a document conforming to the current Open Tax Document (OTD) TaxNode/reference schema while using document envelope version `0.1`. It is produced by `phase4_assemble.py` and validated by `validate_otd.py`.

**You must never hand-write this file.** Run the pipeline.

---

### What the Assembler Produces

The assembler takes 5 fragment files from `fragments/` and emits a single, structured YAML document with the following top-level shape:

```yaml
otd:                          # §1 Document Envelope — NOT tax data
  version: "0.1"
  document_id: "{uuid}"
  created: "{iso_datetime}"
  producer: { name: ..., version: ... }
  taxonomy:
    id: "irs-k1-1065-2025"
    version: "2025.1.0"
  source_document:
    sha256: "{pdf_sha256}"

form_metadata:                # §1b Legal/administrative identifiers — REQUIRED
  tax_year: 2022
  fiscal_year: false
  fiscal_year_begin: null
  fiscal_year_end: null
  amended: true
  final: true
  supersedes_document_id: null  # Set if prior OTD document ID is known
  filing_status: "amended"      # ENUM: original | amended | superseded | void
  form_revision_date: "2025"

body:                          # Tax data tree — TaxNode primitives only
  form_id: "k1-1065"
  part_i: { ... }              # Scalar nodes (partnership identity)
  part_ii: { ... }             # Scalar nodes (partner identity + capital account)
  part_iii:                    # Scalar + Coded + Reference nodes
    box_1: { type: scalar, semantic: {id, label}, form: {form_id, location, box}, value: ... }
    box_11: { type: coded, ..., entries: [{code, label, amount, classification}, ...] }
    box_16: { type: reference, ..., target: {taxonomy_id: "irs-k3-1065-2025", node_path: /} }

statements:                    # All footnotes, overflows, and schedules
  - type: statement
    semantic: { id, label, classification: "<21-type catalog or 'custom'>" }
    form: { attachment: true, attachment_sequence: N }
    content: { ... }           # Structured fields per otd-footnote-taxonomy.md
    source_text: "..."         # VERBATIM — never summarize
    confidence: 0.80
    extraction_method: "ai_structured"

redaction:                     # Emitted only when PII is masked
  policy: "partial"
  fields_redacted: [ "body.part_i.partnership_ein", "body.part_ii.partner_tin" ]
```

---

### Six TaxNode Types (Only These Are Valid)

| Type | Usage | Required Fields |
|---|---|---|
| `scalar` | Single value (box 1, capital account) | `type`, `semantic.id`, `semantic.label`, `form.location`, `value` |
| `coded` | Multi-code boxes (11, 13, 15, 17–20) | `type`, `semantic`, `form`, `entries[]` |
| `grid` | Fixed-axis matrix (K-3 Part II) | `type`, `semantic`, `form`, `columns[]`, `rows[]` |
| `recordset` | Variable entity list (PFICs, Form 926) | `type`, `semantic`, `form`, `record_schema[]`, `records[]` |
| `statement` | Footnote / overflow / attachment | `type`, `semantic.classification`, `form.attachment`, `content`, `source_text` |
| `reference` | Cross-form pointer (Box 16 → K-3) | `type`, `semantic`, `form`, `target.taxonomy_id`, `target.node_path` |

**Any other node shape is a spec violation.** The validator enforces this.

---

### Footnote Classification

Every `statement` node's `semantic.classification` MUST be one of the 21 canonical types in `reference/otd-footnote-taxonomy.md`, or `"custom"` (with `content.custom_classification` set).

Key mappings for common K-1 footnotes:

| Footnote Content | Classification |
|---|---|
| §754/§743(b) basis adjustments | `section_754_adjustment` |
| FIRPTA / USRPI disposition | `firpta_withholding` |
| Debt allocation by activity | `debt_allocation` |
| §1061 carried interest | `carried_interest_section_1061` |
| UBTI for tax-exempt partners | `ubti_unrelated_business_taxable_income` |
| Multi-state income apportionment | `state_apportionment` |
| §199A QBI information | `section_199a_detail` |
| Form 926 foreign transfers | `form_926_transfer_to_foreign_corp` |
| §163(j) interest limitation | `section_163j_business_interest` |
| ECI effectively connected income | `eci_effectively_connected_income` |
| NII §1411 detail | `nii_net_investment_income` |
| PTE tax credits | `state_pte_tax` |
| Passive activity grouping | `passive_activity_grouping` |
| Any other | `custom` + `content.custom_classification: "snake_case_name"` |

---

### What the Validator Checks

Run `validate_otd.py --input output.otd.yaml` after every assembly. It enforces:

1. Required envelope keys (`otd.version`, `document_id`, `created`, `producer`, `taxonomy`)
2. Required `form_metadata` keys (`tax_year`, `fiscal_year`, `amended`, `final`, `filing_status`)
3. Required `body` keys (`form_id`, `part_i`, `part_ii`, `part_iii`)
4. All node `type` values are in the valid set of six primitives
5. `filing_status` is in the spec enum (`original | amended | superseded | void`)
6. If `filing_status` is `amended` or `superseded`, warns when `supersedes_document_id` is null
7. Capital account arithmetic (beginning + contributions + net + other − withdrawals ≈ ending, ≤$1 tolerance)
8. Any `unclassified_requires_review` statement nodes (warnings)
9. Any `_unverified` markers surviving into the final document (warnings)

**Target:** 0 errors, 0 warnings. The one acceptable warning is `supersedes_document_id is null` on amended K-1s where the prior OTD document ID is genuinely unknown.

---

### Fragment Shape Expected by the Assembler

The assembler reads from `fragments/` and expects these shapes:

| File | Expected Shape |
|---|---|
| `face_page.json` | `{form_metadata: {}, part_i: {}, part_ii: {}, part_iii_face: {box_1: ..., ...}, _unmapped_source_data: []}` |
| `overflow_statements.json` | `{part_iii_overflow: {box_11: [{code, label, amount, classification}], box_13: [...], ...}}` |
| `footnotes_a.json` | `[{id, classification, label, content: {}, source_text, confidence, cross_references, extraction_method}]` |
| `footnotes_b.json` | Same as footnotes_a.json |
| `state_schedules.json` | `{activity_schedule: {activities: []}, state_grids: [], state_tax_summary: [{j, source_income, withholding, composite, ubti}]}` |

**Important:** If your fragment shape differs from the above (e.g., from an ad-hoc inline extraction), run `scripts/reshape_fragments.py` before invoking the assembler. See `scripts/` for the canonical reshape utility.

---

### Reference Files (Read-Only)

```
reference/
├── otd-spec-v0.2.yaml           # Six TaxNode primitives + document envelope spec
├── otd-emitter-spec.md          # Emitter implementation rules
├── otd-parser-spec.md           # Parser implementation rules
├── otd-derivation-spec.md       # How taxonomies are derived from IRS instructions
├── otd-footnote-taxonomy.md     # 21 footnote classifications with structured schemas
├── irs-k1-1065-2025.yaml        # 200+ K-1 box/code definitions
└── proof-emitted.otd.yaml       # Golden reference: minimal K-1 OTD example
```

**Load `reference/otd-spec-v0.2.yaml` and `reference/irs-k1-1065-2025.yaml` before reasoning about any OTD conformance question.** Do not rely on memory — always go back to the spec.
