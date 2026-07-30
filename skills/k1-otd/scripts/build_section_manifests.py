
#!/usr/bin/env python
"""Build deterministic logical-section and page-projection manifests.

The classifier reads extracted page text, identifies logical sections using
signatures and row-shape evidence, and writes both the detailed section
manifest and the page-level projection consumed by downstream fragment
workers. Ambiguous sections remain explicit review outcomes.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Canonical emission order and worker assignment.
# (section name, manifest `type` value, worker)  worker None => split.
SECTION_ORDER = [
    ("face_page",                  "face_page",         "A"),
    ("overflow_statements",        "overflow_statement", "B"),
    ("line_item_details",          "line_item_detail",  "F"),
    ("footnotes",                  "footnote",          None),
    ("activity_schedule",          "activity_schedule", "E"),
    ("state_schedules",            "state_schedule",    "E"),
    ("unresolved_requires_review", "unresolved",        "R"),
]


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
    """Project classified sections onto the page-level projection.

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
            # Active contract: the assembler merges face-page coded
            # entries with the overflow list, so the face page must appear.
            pages = sorted(set(pages) | set(face))
        out.append({"name": name, "pages": pages,
                    "type": ptype, "worker": worker})
    return out


def main():
    p = argparse.ArgumentParser(
        description="K-1 OTD logical section manifest builder")
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
    args = p.parse_args()

    with open(args.index, encoding="utf-8") as f:
        index = json.load(f)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not args.text_dir:
        print("ERROR: --text-dir is required for logical section classification.\n"
              "       It reads the extracted page text; page_index.json carries "
              "page metadata only.\n"
              "       Pass --text-dir text_blocks/.",
              file=sys.stderr)
        return 2

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
            "(build_section_manifests.py -> section_segmenter.py + "
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
    print("\nSection manifest build complete: %d pages -> %d logical sections -> %d "
          "pipeline sections" % (len(pages), len(secs), len(sections)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
