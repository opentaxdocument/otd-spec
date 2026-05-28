# K-1 OTD Extraction Skill

**Version:** 1.2.0
**Skill Pack for:** [Second Wind](https://github.com/crimsontreesoftware/second-wind) AI Runtime
**OTD Spec:** [opentaxdocument/otd-spec](https://github.com/opentaxdocument/otd-spec)
**License:** CC BY 4.0

---

## What This Is

A complete, production-tested pipeline for extracting IRS Schedule K-1 (Form 1065) PDFs into [OTD-compliant](../../spec/otd-spec-v0.2.yaml) YAML documents.

This skill was validated against a 19-page, 2022 amended K-1 for a complex fund-of-funds partnership with:
- 87 coded entries across 8 Part III boxes
- 31 structured footnote nodes
- 48-jurisdiction SALT footprint (state source income, ECI, UBTI grids)
- 4-activity schedule
- §743(e) EIP election, QSBS §1202, Form 926, Form 8886, §163(j), and more

**Result:** 7,201 OTD fields | 0 validation errors | Full adversarial review | Preparer's Summary generated

---

## Requirements

```bash
pip install pdfplumber ruamel.yaml
```

Python 3.10+. No other dependencies.

---

## Quick Start

```bash
# 1. Extract PDF text
python scripts/phase1_extract_text.py \
  --pdf "path/to/your-k1.pdf" \
  --out "output/artifacts"

# 2. Classify pages
python scripts/phase2_classify.py \
  --index "output/artifacts/text_blocks/page_index.json" \
  --out "output/artifacts/fragments/page_manifest.json"

# 3. Build mini taxonomy (token-efficient extraction reference)
python scripts/build_mini_taxonomy.py \
  --text-dir "output/artifacts/text_blocks" \
  --master-taxonomy "../../taxonomies/irs-k1-1065-2025.yaml" \
  --out "output/artifacts/fragments/mini_taxonomy.yaml"

# 4. Run Phase 3 inline AI extraction (see SKILL.md §6 for agent turn discipline)

# 5. Assemble
python scripts/phase4_assemble.py \
  --fragments "output/artifacts/fragments" \
  --out "output/artifacts/output.otd.yaml" \
  --sha256 "<pdf-sha256>"

# 6. Validate
python scripts/validate_otd.py \
  --input "output/artifacts/output.otd.yaml"
```

---

## Pipeline Overview

```
PDF → Phase 1 (Python) → Phase 2 (Python) → Phase 3 (AI + Python)
    → Phase 4 (Python) → Phase 5 (Adversarial AI) → output.otd.yaml
```

| Phase | Method | Description |
|-------|--------|-------------|
| 1 | Python | Deterministic PDF text extraction via `pdfplumber`. Zero AI tokens. |
| 2 | Python | Page classification + mini taxonomy construction. Zero AI tokens. |
| 3 | AI (inline) | 5-turn sequential extraction: Face Page → Overflow → Footnotes A → Footnotes B → State Schedules |
| 4 | Python | OTD assembly + validation. Zero AI tokens. |
| 5 | AI (adversarial) | Spot-check, code validation, capital check, coverage audit. Emits patch. |

---

## Directory Layout

```
skills/k1-otd/
├── README.md                        ← You are here
├── SKILL.md                         ← Full agent execution manifest
├── reference/                       ← Local copies of spec files (for self-contained use)
│   ├── otd-spec-v0.2.yaml
│   ├── irs-k1-1065-2025.yaml
│   ├── otd-footnote-taxonomy.md
│   ├── proof-emitted.otd.yaml
│   ├── otd-emitter-spec.md
│   ├── otd-parser-spec.md
│   └── otd-derivation-spec.md
└── scripts/
    ├── phase1_extract_text.py       ← PDF → text blocks
    ├── phase2_classify.py           ← Page classification
    ├── build_mini_taxonomy.py       ← Token-efficient taxonomy builder
    ├── extract_state_grids.py       ← Deterministic state grid extraction (with AI fallback)
    ├── state_grid_parsers.py        ← Reusable parser strategies for state grid formats
    ├── phase4_assemble.py           ← Fragment → OTD assembly
    ├── validate_otd.py              ← OTD validation
    └── apply_adversary_patch.py     ← Apply adversarial review corrections
```

---

## Output Contract

The primary deliverable is `output.otd.yaml` — a document conforming to the current [OTD TaxNode/reference schema](../../spec/otd-spec-v0.2.yaml) while using document envelope version `0.1`.

**Target quality gate:** `PASS | 0 errors | ≤1 warning`

The one acceptable warning on amended K-1s: `supersedes_document_id is null` (when the prior OTD document ID is genuinely unknown).

---

## Key Design Decisions

**Inline extraction over sub-agents.** Phase 3 uses the main agent in sequential turns rather than parallel sub-agents. This eliminates start/pause/resume latency, timeout risk, and wake signal overhead. See `SKILL.md §10` for rationale.

**Face-Page Merge Rule.** Overflow extraction (Turn B) explicitly re-reads the face page to ensure Part III coded entries visible on both the face and overflow statements are not silently dropped.

**Adversarial review is mandatory.** Phase 5 must use a frontier model (Gemini 3.1 Pro or Claude 4.6) for genuine cross-document reasoning. Self-review by the same model that extracted the data is insufficient for a tax document of this complexity.

**Parser library (`state_grid_parsers.py`).** State schedule grids vary dramatically by issuer and OCR engine. The parser library accumulates successful strategies across runs rather than discarding them. New parsing challenges should extend this library.

---

## Validated Against

- **Form:** IRS Schedule K-1 (Form 1065), Tax Year 2022, Amended
- **Entity:** Complex fund-of-funds LP with 4 underlying activities
- **Pages:** 19 (face + overflow + 16 footnote pages + activity schedule + state schedule)
- **SHA-256:** `95489c86cb56fd869a5ab8d38a2b4ee00a0a261cf5998e11e647acdee057ce06`
- **Result:** 7,201 fields | 66 statement nodes | 87 coded entries | 0 errors

---

## Contributing

State grid formats vary by K-1 issuer. If you encounter a new format that `extract_state_grids.py` escalates:
1. Add a new parser strategy to `state_grid_parsers.py`
2. Wire it into `extract_state_grids.py`
3. Open a PR describing the format you solved

See `SKILL.md §6` ("State Schedule Parsing & Parser Library") for the contribution protocol.

---

## License

Creative Commons Attribution 4.0 International (CC BY 4.0).
See [LICENSE](../../LICENSE).
