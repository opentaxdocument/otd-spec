
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Phase 1: PDF Text Extraction
Extracts text from each page of a K-1 PDF, saves as individual page files.
Output: text_blocks/page_NN.txt + text_blocks/page_index.json
"""
import sys
import json
import argparse
import hashlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def classify(text, page_num):
    """Heuristic page type classification from content keywords."""
    t = text.lower()
    if page_num == 1:
        return "face_page"
    if "schedule of activities" in t:
        return "activity_schedule"
    if any(x in t for x in ["state k-1", "state income", "state tax summary", "composite return"]):
        return "state_schedule"
    if any(x in t for x in ["see statement", "statement 1 ", "statement 2 ", "statement 3 "]):
        return "overflow_statement"
    if any(x in t for x in ["199a", "qbi", "qualified business income", "w-2 wages"]):
        return "footnote"
    if "163(j)" in t or "business interest limitation" in t:
        return "footnote"
    if any(x in t for x in ["form 926", "firpta", "ubti", "fdap", "eci", "1061", "depletion",
                              "installment sale", "recapture", "754", "163(j)", "1202", "8886"]):
        return "footnote"
    if "state tax" in t and ("total" in t or "summary" in t):
        return "state_tax_summary"
    return "footnote"


def main():
    p = argparse.ArgumentParser(description="K-1 OTD Skill — Phase 1: PDF text extraction")
    p.add_argument("--pdf", required=True, help="Path to K-1 PDF")
    p.add_argument("--out", required=True, help="Output artifacts root directory")
    args = p.parse_args()

    try:
        import pdfplumber
    except ImportError:
        print("ERROR: pdfplumber not installed. Run: pip install pdfplumber", file=sys.stderr)
        sys.exit(1)

    pdf = Path(args.pdf)
    out = Path(args.out)
    text_dir = out / "text_blocks"
    text_dir.mkdir(parents=True, exist_ok=True)

    # Hash the source PDF for provenance
    sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    pages_meta = []

    with pdfplumber.open(str(pdf)) as doc:
        n = len(doc.pages)
        for i, page in enumerate(doc.pages, 1):
            # Primary: text layer
            text = page.extract_text() or ""

            # Secondary: structured tables (critical for state grids and activity schedules)
            table_lines = []
            for tbl in (page.extract_tables() or []):
                for row in (tbl or []):
                    cells = [str(c or "").strip() for c in (row or [])]
                    if any(cells):
                        table_lines.append(" | ".join(cells))
            if table_lines:
                text += "\n\n=== TABLES ===\n" + "\n".join(table_lines)

            pg_file = text_dir / f"page_{i:02d}.txt"
            pg_file.write_text(text, encoding="utf-8")
            print(f"WROTE {pg_file}")

            pages_meta.append({
                "page": i,
                "file": f"page_{i:02d}.txt",
                "char_count": len(text),
                "type_hint": classify(text, i)
            })

    index = {
        "pdf_name": pdf.name,
        "pdf_path": str(pdf),
        "sha256": sha256,
        "total_pages": n,
        "pages": pages_meta
    }
    idx_file = text_dir / "page_index.json"
    idx_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"WROTE {idx_file}")
    print(f"\nPhase 1 complete: {n} pages extracted")
    print(f"SHA-256: {sha256}")
    print(f"\nPage type hints:")
    for pg in pages_meta:
        print(f"  page_{pg['page']:02d}: {pg['type_hint']} ({pg['char_count']} chars)")


if __name__ == "__main__":
    main()
