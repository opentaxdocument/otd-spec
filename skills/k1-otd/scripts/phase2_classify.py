
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Phase 2: Deterministic Section Classification

Classifies each LOGICAL SECTION of the package, then projects the result onto
the page-level manifest the downstream workers already consume.

WHY SECTIONS AND NOT PAGES
    A page is a printing artifact. Real K-1 pages carry several unrelated
    sections, and single sections span pages. Observed on a 27-page package:
      * one page held THREE headed blocks (ECI / FDAP / PASSIVE) each
        restating form lines under a different lens
      * one page held an activity ROSTER above an activity MATRIX
      * a detail block opened on one page and continued onto the next
    A page-level classifier must pick ONE role per page and is therefore
    forced to be wrong about the rest.

DEFECTS THIS REPLACES (all measured on the same document)
    1. Unanchored substring matching in the Phase 1 hint: `"state income" in
       text` matched "real eSTATE INCOME", filing a page titled "LINE ITEM
       DETAILS / LINE 20V - UNRELATED BUSINESS TAXABLE INCOME DETAIL" as a
       state schedule. `"eci" in text` matched sp-ECI-fied and pr-ECI-ous.
    2. Fail-open default `return "footnote"`: every page the classifier could
       not understand silently became a footnote. That is how four pages
       titled "SCHEDULE K-1 FORM 1065 - OVERFLOW STATEMENTS" were filed as
       footnotes, and eight pages titled "LINE ITEM DETAILS" were scattered
       across three unrelated fragments.
    3. A char-count heuristic (`char_count < 3000 and page_num <= 6`) standing
       in for reading the page's own content.
    Prior classifier: ~10 of 27 pages correct, ZERO escalations.
    Section classifier: 0 misroutings, escalations recorded explicitly.

BACKWARD COMPATIBILITY (deliberate, verified against phase4_assemble.py)
    * page_manifest.json keeps its exact shape: total_pages, sections[] of
      {name, pages, type, worker}, classification_notes.
    * The face page is still merged into the overflow page list — the
      assembler relies on that for face-page coded-entry merging.
    * Footnotes are still split A/B across parallel workers C and D.
    * phase4_assemble.py loads FIXED fragment filenames, so adding sections
      (line_item_details, unresolved_requires_review) cannot break it.

FAIL-CLOSED BY DEFAULT
    Section classification requires the extracted page text. If --text-dir is
    absent the run ERRORS rather than silently falling back to the defective
    heuristics; the legacy path must be requested explicitly with
    --legacy-heuristics and is recorded in the manifest when used. A
    classifier that quietly degrades to a known-broken path has not
    classified anything.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Canonical emission order and worker assignment.
# (pipeline section name, legacy `type` value, worker)  worker None => split.
SECTION_ORDER = [
    ("face_page",                  "face_page",         "A"),
    ("overflow_statements",        "overflow_statement", "B"),
    ("line_item_details",          "line_item_detail",  "F"),
    ("footnotes",                  "footnote",          None),
    ("activity_schedule",          "activity_schedule", "E"),
    ("state_schedules",            "state_schedule",    "E"),
    ("unresolved_requires_review", "unresolved",        "R"),
]


# ---------------------------------------------------------------------------
# Legacy page heuristics — retained for explicit opt-in only.
# ---------------------------------------------------------------------------

def refine_type(hint, char_count, page_num):
    """DEPRECATED page-level heuristics. Reachable only via
    --legacy-heuristics. Preserved verbatim so a legacy run remains
    reproducible, not because it is correct: the `return "footnote"`
    fall-through is a fail-open default and the char-count rule is a proxy for
    reading the page."""
    if page_num == 1:
        return "face_page"
    if hint in ("activity_schedule", "state_tax_summary", "state_schedule"):
        return hint
    if hint == "overflow_statement":
        return "overflow_statement"
    if char_count < 3000 and page_num <= 6:
        return "overflow_statement"
    return "footnote"


def split_footnotes(pages):
    """Split a footnote page list into two roughly equal halves."""
    mid = (len(pages) + 1) // 2
    return pages[:mid], pages[mid:]


# ---------------------------------------------------------------------------
# Section-level classification
# ---------------------------------------------------------------------------

def load_pages(text_dir):
    d = Path(text_dir)
    pages = []
    for f in sorted(d.glob("page_*.txt")):
        m = re.search(r"page_(\d+)", f.name)
        pages.append({"page": int(m.group(1)) if m else 0,
                      "text": f.read_text(encoding="utf-8", errors="replace")})
    return pages


def classify(text_dir, signatures=None):
    from page_classifier import load_signatures
    from section_classifier import classify_sections
    from section_segmenter import prepare, segment_document

    sig = prepare(load_signatures(signatures))
    pages = load_pages(text_dir)
    if not pages:
        raise SystemExit("ERROR: no page_*.txt found in %s" % text_dir)
    return sig, pages, classify_sections(segment_document(pages, sig), sig)


def manifest_sections(secs):
    """Project classified sections onto the legacy page-level section list.

    A page is listed under EVERY role it carries. The projection is lossy at
    page granularity by construction -- section_manifest.json carries the
    exact line ranges -- and pages carrying more than one role are reported
    so the loss is visible rather than implied.
    """
    by_name = {}
    for s in secs:
        by_name.setdefault(s["section"], []).append(s)

    face = sorted({s["page"] for s in by_name.get("face_page", [])})
    out = []
    for name, ptype, worker in SECTION_ORDER:
        group = by_name.get(name)
        if not group:
            continue
        pages = sorted({s["page"] for s in group})
        if name == "footnotes":
            a, b = split_footnotes(pages)
            if a:
                out.append({"name": "footnotes_a", "pages": a,
                            "type": ptype, "worker": "C"})
            if b:
                out.append({"name": "footnotes_b", "pages": b,
                            "type": ptype, "worker": "D"})
            continue
        if name == "overflow_statements" and face:
            # Preserved behaviour: the assembler merges face-page coded
            # entries with the overflow list, so the face page must appear.
            pages = sorted(set(pages) | set(face))
        out.append({"name": name, "pages": pages,
                    "type": ptype, "worker": worker})
    return out


def main():
    p = argparse.ArgumentParser(
        description="K-1 OTD Phase 2 — deterministic section classification")
    p.add_argument("--index", required=True,
                   help="Path to text_blocks/page_index.json")
    p.add_argument("--out", required=True,
                   help="Output path for fragments/page_manifest.json")
    p.add_argument("--text-dir", default=None,
                   help="Path to text_blocks/ (required for section "
                        "classification)")
    p.add_argument("--section-out", default=None,
                   help="Output path for section_manifest.json "
                        "(default: beside --out)")
    p.add_argument("--signatures", default=None,
                   help="Override signatures/page-signatures.yaml")
    p.add_argument("--legacy-heuristics", action="store_true",
                   help="Explicitly use the DEPRECATED page-level heuristics")
    args = p.parse_args()

    with open(args.index, encoding="utf-8") as f:
        index = json.load(f)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not args.text_dir and not args.legacy_heuristics:
        print("ERROR: --text-dir is required for section classification.\n"
              "       Section classification needs the extracted page text; "
              "page_index.json\n"
              "       carries only char_count and a type_hint produced by "
              "unanchored substring\n"
              "       matching. Pass --text-dir text_blocks/, or request the "
              "deprecated path\n"
              "       explicitly with --legacy-heuristics.",
              file=sys.stderr)
        return 2

    if args.legacy_heuristics:
        classified = [{"page": pg["page"],
                       "type": refine_type(pg.get("type_hint", "footnote"),
                                           pg.get("char_count", 0),
                                           pg["page"])}
                      for pg in index["pages"]]

        def pages_of(t):
            return [c["page"] for c in classified if c["type"] == t]

        face = pages_of("face_page")
        overflow = pages_of("overflow_statement")
        footnote = pages_of("footnote")
        activity = pages_of("activity_schedule")
        state = [c["page"] for c in classified
                 if c["type"] in ("state_schedule", "state_tax_summary")]
        fn_a, fn_b = split_footnotes(footnote)

        sections = []
        if face:
            sections.append({"name": "face_page", "pages": face,
                             "type": "face_page", "worker": "A"})
        if overflow:
            sections.append({"name": "overflow_statements",
                             "pages": (face + overflow) if face else overflow,
                             "type": "overflow_statement", "worker": "B"})
        if fn_a:
            sections.append({"name": "footnotes_a", "pages": fn_a,
                             "type": "footnote", "worker": "C"})
        if fn_b:
            sections.append({"name": "footnotes_b", "pages": fn_b,
                             "type": "footnote", "worker": "D"})
        if activity:
            sections.append({"name": "activity_schedule", "pages": activity,
                             "type": "activity_schedule", "worker": "E"})
        if state:
            sections.append({"name": "state_schedules", "pages": state,
                             "type": "state_schedule", "worker": "E"})

        manifest = {
            "total_pages": index["total_pages"],
            "method": "legacy_page_heuristics",
            "sections": sections,
            "classification_notes": (
                "DEPRECATED page-level heuristics used by explicit request "
                "(--legacy-heuristics). Known defective: unanchored substring "
                "matching and a fail-open 'footnote' default. "
                "%d footnote pages split %d+%d across workers C/D."
                % (len(footnote), len(fn_a), len(fn_b))),
        }
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("WROTE %s" % out)
        for s in sections:
            print("  %-28s pages=%s  worker=%s"
                  % (s["name"], s["pages"], s["worker"]))
        print("\nPhase 2 complete (LEGACY): %d pages -> %d sections"
              % (index["total_pages"], len(sections)))
        return 0

    sig, pages, secs = classify(args.text_dir, args.signatures)
    sections = manifest_sections(secs)

    roles_by_page = {}
    for s in secs:
        roles_by_page.setdefault(s["page"], set()).add(s["section"])
    multi = {str(k): sorted(v) for k, v in roles_by_page.items()
             if len(v) > 1}
    unresolved = [{"page": s["page"], "index": s["index"],
                   "rows": s["row_count"],
                   "dominant_shape": s["dominant_shape"],
                   "reasons": s["reasons"]}
                  for s in secs if s["role"] == "unresolved"]

    manifest = {
        "total_pages": index["total_pages"],
        "method": "section_level",
        "sections": sections,
        "classification_notes": (
            "Section-level deterministic classification "
            "(phase2_classify.py -> section_segmenter.py + "
            "section_classifier.py). %d logical sections across %d pages; "
            "%d unresolved. Roles derive from row-shape histograms, not from "
            "page adjacency or char counts. Pages carrying more than one role "
            "appear under each; see section_manifest.json for exact line "
            "ranges."
            % (len(secs), len(pages), len(unresolved))),
        "multi_role_pages": multi,
        "unresolved_sections": unresolved,
    }
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    sec_out = Path(args.section_out) if args.section_out else \
        out.parent / "section_manifest.json"
    sec_out.parent.mkdir(parents=True, exist_ok=True)
    sec_out.write_text(json.dumps({
        "total_pages": len(pages),
        "total_sections": len(secs),
        "signatures": sig.get("_path"),
        "sections": [{
            "page": s["page"],
            "continued_pages": s.get("continued_pages", []),
            "index": s["index"],
            "section": s["section"],
            "role": s["role"],
            "score": s["score"],
            "margin": s.get("margin"),
            "score_source": s.get("score_source"),
            "absorbed_from": s.get("absorbed_from"),
            "collapsed_into_face": s.get("collapsed_into_face", False),
            "line_start": s["line_start"],
            "line_end": s["line_end"],
            "row_count": s["row_count"],
            "dominant_shape": s["dominant_shape"],
            "trailing_amount_mode": s.get("trailing_amount_mode"),
            "heading": s.get("heading"),
            "shapes": s.get("shapes"),
            "candidates": s.get("candidates"),
            "title_effect": s.get("title_effect"),
            "reasons": s.get("reasons"),
        } for s in secs],
    }, indent=2), encoding="utf-8")

    print("WROTE %s" % out)
    print("WROTE %s" % sec_out)
    for s in sections:
        print("  %-28s pages=%s  worker=%s"
              % (s["name"], s["pages"], s["worker"]))
    if multi:
        print("\n  multi-role pages: %s"
              % ", ".join("%s=%s" % (k, "+".join(v))
                          for k, v in sorted(multi.items(),
                                             key=lambda kv: int(kv[0]))))
    if unresolved:
        print("  unresolved sections (require review): %s"
              % ", ".join("p%s#%s(%dr,%s)"
                          % (u["page"], u["index"], u["rows"],
                             u["dominant_shape"]) for u in unresolved))
    print("\nPhase 2 complete: %d pages -> %d logical sections -> %d "
          "pipeline sections" % (len(pages), len(secs), len(sections)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
