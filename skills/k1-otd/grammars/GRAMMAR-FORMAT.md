
# K-1 Face Grammar Format

Status: draft (Wave 1, 2026-07-28)
Applies to: `skills/k1-otd/grammars/*.grammar.yaml`

## Purpose

A face grammar describes *how to read* a specific tax form face and overflow
tables deterministically, without depending on:

- absolute PDF coordinates as the primary parsing mechanism,
- AcroForm/XFA fields (real preparer output frequently has none — confirmed
  empirically: 20/20 sampled real Meridian K-1s have `AcroForm.present=false`,
  0 fields, 0 annotations),
- a fixed box/item *number*, which is not stable across tax years (confirmed:
  2019 box 21/22 became 2025 box 22/23 when box 21 was inserted).

Instead, a grammar is **label-anchored and elastic**: every field is located
by matching text/geometry patterns *relative to* other patterns on the same
page, at whatever coordinates they happen to appear on a given document.

This is the same principle Extractium (Dave Laroche's parallel project)
arrived at independently via its `Schema.txt` label-anchored format. Where our
schema differs, it differs in evidence discipline (three-state minimum,
fit-gate verification, tracked taxonomy gaps), not in the elastic-geometry
principle itself.

## Design invariants (non-negotiable)

1. **Three-state minimum on every observation.**
   Every field emits one of `present | zero | blank | not_applicable |
   ambiguous | unreadable | missing | unresolved`. A detector that cannot
   evaluate a field MUST emit `unresolved`. It must NEVER default to a
   negative or empty value. This is the failure class OTD exists to prevent.
2. **No absolute-coordinate primary parsing.** Coordinates are runtime
   observations attached as evidence, never a lookup key.
3. **Box/item numbers are year-scoped labels, not identity.** The grammar's
   `semantic_id` binding is the identity; the printed number is metadata that
   may differ by tax year.
4. **Every field binds to a taxonomy `semantic_id`,** or is explicitly marked
   `taxonomy_gap: true` with a note. Unbound fields are not silently dropped.
5. **Fit verification is separate from extraction.** A grammar's
   `fit_signals` block exists so a *fit gate* (see `template_match.py`, Wave 3)
   can verify this grammar is the right one to apply to a given document
   before any field is trusted. Extraction never self-certifies its own fit.

## Top-level structure

```yaml
schema_version: "k1-face-grammar/1.0"
status: draft | reviewed | production
form:
  jurisdiction: federal
  form_name: "K-1 (Form 1065)"
  tax_year: 2025
  form_number_context: "651123"   # OCR-A catalog token observed on face

source_evidence:
  golden_pdf_sha256: "..."
  golden_pdf_path: "..."
  instructions_reconciled: true|false
  derivation_method: "empirical layout probe + IRS instruction reconciliation"
  derivation_date: "2026-07-28"

status_values: [present, zero, blank, not_applicable, ambiguous, unreadable, missing, unresolved]

coordinate_policy:
  use: label_anchored_relative_geometry
  forbidden_use: absolute_coordinate_primary_parse

fit_signals:
  # NOT used for extraction. Used only by the template fit gate (Wave 3)
  # to verify this grammar applies to the document under evaluation.
  required_text: ["Schedule K-1 (Form 1065) 2025"]
  page_size_points: [612, 792]
  expected_checkbox_frame_count: 18
  ocr_catalog_token: "651123"
  frame_signature_note: >
    Frame signature is computed at runtime from observed checkbox-frame
    geometry (8x8 stroke-only rects). It is a corroborating signal, not
    a trusted precomputed value — a hardcoded signature would itself be
    an absolute-template anti-pattern.

regions:
  - id: header
  - id: part_i
  - id: part_ii
  - id: part_iii

fields:
  <field_key>:
    semantic_id: <taxonomy semantic_id, dotted path>
    region: <region id>
    reader: <reader type, see below>
    anchors: {...}          # reader-specific
    value_type: decimal|string|boolean|enum|date|object
    cardinality: 1 | "0..1" | "0..n"
    taxonomy_gap: true       # omit if bound; include with a note if not
    notes: "..."
```

## Region derivation

Regions are **not** fixed y-ranges. Each region is bounded at runtime by
locating its own heading anchor and the *next* region's heading anchor. This
mirrors the `AppearingAfter`/`AppearingBefore` windowing pattern used
elsewhere in this project's tooling, applied to PDF geometry instead of text
files.

| Region | Heading anchor (any variant) |
|---|---|
| `header` | "Schedule K-1", "651123" (OCR-A token area) |
| `part_i` | "Part I", "Information About the Partnership" |
| `part_ii` | "Part II", "Information About the Partner" |
| `part_iii` | "Part III", "Partner's Share of Current Year Income" |

A region's lower bound is the next region's heading anchor top-of-frame,
minus tolerance. This is what lets the same grammar tolerate the ~24pt
vertical drift observed between the 2019 and 2025 Item L position.

## Checkbox semantics (confirmed empirically)

A checkbox frame is an 8x8pt stroke-only rectangle. It survives PDF
flattening at identical coordinates to the IRS blank template (confirmed:
line count, frame count and position identical across all 23 sampled
documents within a form-year group).

The **mark** is a small vector curve (~5.46 x 3.86pt, inset +1.30/+2.10 from
the frame's top-left) fully contained within the frame — **not a glyph**. A
glyph-based detector (searching for `X`/`☒` characters) will report every
real checkbox as unchecked on flattened documents. This was verified as a
live, not hypothetical, failure mode.

Detection is therefore a two-channel process:

1. **Vector containment** (primary): does a curve/line/char element exist
   fully inside the frame rectangle? → `present`, value = true/false.
2. **Raster fallback** (secondary, for scanned/rasterized documents): pixel
   density test inside the frame region.

If *neither* channel can evaluate the frame (e.g. frame located but page has
no content layer, or a rendering error), the status is `unresolved` — never
`present` with value `false`. This distinction is the single most important
guard in this document: **verified unchecked and unresolved are different
facts and must never be conflated.**

### Label association rule (confirmed empirically)

The label governing a checkbox is the **word immediately to its right** on
the same row band, regardless of what word sits to its left. Confirmed
against 3 independently-checked boxes on real documents:

| Frame | Left neighbor | Right neighbor (= governing label) | Checked |
|---|---|---|---|
| Item G, 2nd box | "LLC" | "Limited" | yes |
| Item H1, 1st box | "H1" | "Domestic" | yes |
| Item M, 2nd box | "Yes" | "No" | yes |

Using the left-nearest word would have inverted the Item M read (reporting
"Yes" instead of "No"). This rule is a hard requirement in
`independent_checkbox` and `exclusive_choice_pair` readers.

## Reader catalog

| Reader | Semantics | Output |
|---|---|---|
| `header_period` | Locates a date/year value near a labeled column header | atomic date or year, distinguishing blank (calendar-year filer) from unresolved |
| `bounded_identity` | EIN/TIN-shaped identifier, bounded by adjacent labels | raw + normalized identifier, redaction-aware |
| `address_block` | Composite name/address text block | atomic name/street/city/state/zip children |
| `bounded_text` | Free text bounded by adjacent labels | raw text + optional normalized code |
| `independent_checkbox` | Single zero-or-one boolean, vector+raster, nearest-right label | boolean + observation status |
| `exclusive_choice_pair` | Two checkboxes representing mutually exclusive options | enum + ambiguity status if both/neither legible |
| `multi_checkbox_set` | 0..n independently observable checkboxes (not mutually exclusive) | independently observed booleans |
| `conditional_record` | A checkbox governs whether dependent fields are expected | governing field + atomic dependents |
| `two_column_grid` | Fixed named rows x named columns, decimal values | OTD grid with atomic cells, column bands derived from header anchors |
| `ledger` | Named rows, single value column, may have continuity validation | fixed-row grid or atomic children |
| `range_pair` | Beginning/ending scalar pair | two atomic scalars |
| `scalar_cell` | Single decimal (possibly signed) value | scalar decimal |
| `coded_rows` | Repeated code+amount rows, face slots + unbounded overflow, possible statement linkage | evidence-bearing coded entries, duplicates preserved |
| `attachment_reference` | A checkbox that asserts an attachment exists | reference, asserted only when checked |

### Grammar-declared value aliases

A `bounded_text` field with `value_type: enum` may declare `value_aliases`
when printed text must be normalized to a canonical taxonomy value:

```yaml
value_aliases:
  "PARTNERSHIP (LIMITED)": partnership
```

Matching is case- and punctuation-insensitive. The evidence envelope retains the
printed text in `raw_text`, emits the canonical value in `normalized_value`, and
records alias use in `method` and `notes`. Alias maps must be non-empty
string-to-string mappings; malformed or misplaced maps fail grammar validation.

## Evidence envelope (every field, every reader)

```text
raw_text
normalized_value
status              # from status_values
page
bbox
coordinate_frame     # e.g. pdf_top_left_origin_points (runtime-observed)
method               # e.g. interior_vector | raster_fallback | text_anchor
rule_id              # which grammar rule fired
confidence
grammar_id
grammar_version
```

## Known tracked gaps (do not silently resolve — flag in the grammar)

- **Item J decrease-reason control has no taxonomy `semantic_id` yet.**
  2019 face: one checkbox ("Check if decrease is due to sale or exchange").
  2025 face: two independent checkboxes ("Sale" / "Exchange"). Corroborated
  independently by Extractium's schema (`J.SaleOrExchange` 2019-only vs
  `J.Sale`/`J.Exchange` 2025-only). Marked `taxonomy_gap: true` in the 2025
  grammar pending a taxonomy addition.
- **Capital account `basis_method` is not observed on the 2025 face** (all 5
  basis-method tokens absent across 23/23 sampled documents, both years).
  The pipeline currently emits `"tax"` as an inference from the post-2020
  mandate. Under OTD's no-fabrication rule this must be marked as *derived*,
  not *observed*, if retained at all. Flagged for Tom's review, not resolved
  by this grammar.
- **Several `box_20.other_information.*` taxonomy entries have no
  `value_type`** (e.g. `reserved_ba`, `fuel_tax_credit`, `section_199a`).
  These likely resolve to statement references rather than scalars. The
  `coded_rows` reader must treat "no value_type declared" as "expect a
  statement reference," not as a data defect.
