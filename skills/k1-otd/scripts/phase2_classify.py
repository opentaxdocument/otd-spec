
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Phase 2: Deterministic Page Classification
Reads page_index.json heuristics from Phase 1 and writes page_manifest.json.
Zero AI tokens. Replaces sub-agent approach (abandoned 2026-05-10 due to
multi-ignition-sequence timeouts — deterministic classification has no reasoning value).
"""
import sys
import json
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def refine_type(hint, char_count, page_num):
    """Apply override rules on top of Phase 1 heuristics."""
    if page_num == 1:
        return "face_page"
    if hint in ("activity_schedule", "state_tax_summary", "state_schedule"):
        return hint
    if hint == "overflow_statement":
        return "overflow_statement"
    # Small pages (< 3000 chars) after face page are likely overflow continuations
    if char_count < 3000 and page_num <= 6:
        return "overflow_statement"
    return "footnote"


def split_footnotes(pages):
    """Split footnote page list into two roughly equal halves for parallel workers."""
    mid = (len(pages) + 1) // 2
    return pages[:mid], pages[mid:]


def main():
    p = argparse.ArgumentParser(description="K-1 OTD Phase 2 — deterministic page classification")
    p.add_argument("--index", required=True, help="Path to text_blocks/page_index.json")
    p.add_argument("--out",   required=True, help="Output path for fragments/page_manifest.json")
    args = p.parse_args()

    with open(args.index, encoding="utf-8") as f:
        index = json.load(f)

    classified = []
    for pg in index["pages"]:
        ptype = refine_type(
            pg.get("type_hint", "footnote"),
            pg.get("char_count", 0),
            pg["page"]
        )
        classified.append({"page": pg["page"], "type": ptype})

    def pages_of(t):
        return [c["page"] for c in classified if c["type"] == t]

    face     = pages_of("face_page")
    overflow = pages_of("overflow_statement")
    footnote = pages_of("footnote")
    activity = pages_of("activity_schedule")
    state    = [c["page"] for c in classified if c["type"] in ("state_schedule", "state_tax_summary")]

    fn_a, fn_b = split_footnotes(footnote)

    sections = []
    if face:
        sections.append({"name": "face_page",           "pages": face,     "type": "face_page",         "worker": "A"})
    if overflow:
        # Include face page in overflow pages for face-page coded entry merging
        ov_pages = face + overflow if face else overflow
        sections.append({"name": "overflow_statements", "pages": ov_pages,  "type": "overflow_statement", "worker": "B"})
    if fn_a:
        sections.append({"name": "footnotes_a",         "pages": fn_a,     "type": "footnote",           "worker": "C"})
    if fn_b:
        sections.append({"name": "footnotes_b",         "pages": fn_b,     "type": "footnote",           "worker": "D"})
    if activity:
        sections.append({"name": "activity_schedule",   "pages": activity,  "type": "activity_schedule",  "worker": "E"})
    if state:
        sections.append({"name": "state_schedules",     "pages": state,    "type": "state_schedule",     "worker": "E"})

    manifest = {
        "total_pages": index["total_pages"],
        "sections": sections,
        "classification_notes": (
            f"Deterministic Python classification (phase2_classify.py). "
            f"{len(footnote)} footnote pages split {len(fn_a)}+{len(fn_b)} across workers C/D. "
            f"Source: page_index.json type_hints from Phase 1."
        )
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"WROTE {out}")
    for s in sections:
        print(f"  {s['name']:25s} pages={s['pages']}  worker={s['worker']}")
    print(f"\nPhase 2 complete: {index['total_pages']} pages → {len(sections)} sections")


if __name__ == "__main__":
    main()
