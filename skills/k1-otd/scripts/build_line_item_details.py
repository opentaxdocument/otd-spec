#!/usr/bin/env python
"""K-1 OTD Skill -- Line-Item Detail Reconciliation: Fragment Builder.

Parses "LINE NN - <label> DETAIL" pages (this fixture's supplemental
breakdown pages) into a structured fragment mapping each printed line-item
detail block to its governing form box/code, its component rows, and its
printed TOTAL. This is the raw material for a face-vs-detail reconciliation
gate: does the box value the deterministic face reader observed match the sum
the document itself printed for that box's components?

GENERIC BY DESIGN, NOT VENDOR-SPECIFIC:
  - The box/code mapping is derived from the GRAMMAR at runtime
    (box_number_2025 on scalar_cell and coded_rows fields), never hardcoded
    per-vendor or per-fixture.
  - The parsing rule matches any page containing "LINE <token> - ... DETAIL"
    headings -- a structural marker, not a vendor name or fixture identifier.
  - A printed line token that does not resolve to any declared box (grammar
    drift, unknown box) is recorded as UNMATCHED rather than dropped or
    silently guessed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    raise

HEADING_RE = re.compile(
    r"^LINE\s+([0-9]{1,2}[A-Z]{0,3})\s*-\s*(.+?)\s+DETAIL\s*$", re.MULTILINE)
ROW_RE = re.compile(r"^(.+?)\s{1,}(\(?[\d,]+\.?\d*\)?)?\s*$")


def build_box_maps(grammar_path):
    """Return (scalar_map, coded_map): box_number_2025 (lowercased) -> field_key.

    Derived entirely from the grammar's declared fields -- never hardcoded --
    so a different form year's grammar produces a different, correct map
    with no code change here.
    """
    grammar = yaml.safe_load(open(grammar_path, encoding="utf-8"))
    scalar_map, coded_map = {}, {}
    for key, fdef in (grammar.get("fields") or {}).items():
        if not isinstance(fdef, dict):
            continue
        box_num = fdef.get("box_number_2025")
        if box_num is None:
            continue
        reader = fdef.get("reader")
        norm = str(box_num).strip().lower()
        if reader == "scalar_cell":
            scalar_map[norm] = key
        elif reader == "coded_rows":
            coded_map[norm] = key
    return scalar_map, coded_map


def normalize_token(tok):
    """'01' -> '1', '06A' -> '6a', '11ZZ' -> '11zz'. Strips one leading zero,
    lowercases any letter suffix."""
    tok = tok.strip()
    m = re.match(r"^0?([0-9]{1,2})([A-Z]{0,3})$", tok, re.IGNORECASE)
    if not m:
        return tok.lower()
    return (m.group(1) + m.group(2)).lower()


def split_numeric_prefix(token):
    """'11a' -> ('11', 'a'); '6a' -> ('6', 'a'); '21' -> ('21', '')."""
    m = re.match(r"^([0-9]{1,2})([a-z]{0,3})$", token)
    if not m:
        return token, ""
    return m.group(1), m.group(2)


def parse_amount(text):
    text = (text or "").strip()
    if not text:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("$", "").strip()
    if not text:
        return None
    try:
        val = float(text)
    except ValueError:
        return None
    return -val if neg else val


def parse_page(text):
    """Extract every 'LINE <token> - <label> DETAIL' block from raw page
    text. Returns a list of {line_token, label, components, total}."""
    # Phase 1 appends a "=== TABLES ===" section that re-renders the same
    # blocks via pdfplumber's table extraction. Without truncating here, the
    # LAST heading block on a page has no next-match boundary and its
    # "end" runs to len(text), swallowing that entire duplicate section as
    # spurious components -- including a stray TOTAL that can overwrite the
    # real one with null or a wrong value. Truncate BEFORE parsing, not after.
    text = text.split("=== TABLES ===")[0]
    blocks = []
    matches = list(HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        token, label = m.group(1), m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        components, total = [], None
        for ln in lines:
            if ln.upper() == "DESCRIPTION VALUE":
                continue
            rm = ROW_RE.match(ln)
            if not rm:
                continue
            row_label = rm.group(1).strip()
            row_value = parse_amount(rm.group(2))
            if row_label.upper() == "TOTAL":
                total = row_value
            else:
                components.append({"label": row_label, "value": row_value})
        blocks.append({
            "line_token": token, "label": label,
            "components": components, "total": total,
        })
    return blocks


def resolve_box(token, scalar_map, coded_map):
    norm = normalize_token(token)
    if norm in scalar_map:
        return "scalar", scalar_map[norm], None
    num, suffix = split_numeric_prefix(norm)
    if num in coded_map:
        return "coded", coded_map[num], suffix.upper()
    return "unmatched", None, None


def main():
    p = argparse.ArgumentParser(
        description="K-1 OTD Skill -- line-item detail fragment builder")
    p.add_argument("--pages", required=True,
                   help="Path to text_blocks/ directory")
    p.add_argument("--grammar", required=True,
                   help="Path to face grammar YAML (source of box mapping)")
    p.add_argument("--out", required=True,
                   help="Output path for line_item_details.json")
    args = p.parse_args()

    scalar_map, coded_map = build_box_maps(args.grammar)
    pages_dir = Path(args.pages)
    records = []
    for page_file in sorted(pages_dir.glob("page_*.txt")):
        text = page_file.read_text(encoding="utf-8")
        for block in parse_page(text):
            mode, box_key, code = resolve_box(
                block["line_token"], scalar_map, coded_map)
            records.append({
                "source_page": page_file.name,
                "line_token": block["line_token"],
                "label": block["label"],
                "mode": mode,
                "box_key": box_key,
                "code": code,
                "components": block["components"],
                "total": block["total"],
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print("WROTE %s" % out_path)
    matched = sum(1 for r in records if r["mode"] != "unmatched")
    print("Blocks: %d total, %d matched (%d scalar, %d coded), %d unmatched"
          % (len(records), matched,
             sum(1 for r in records if r["mode"] == "scalar"),
             sum(1 for r in records if r["mode"] == "coded"),
             sum(1 for r in records if r["mode"] == "unmatched")))
    for r in records:
        if r["mode"] == "unmatched":
            print("  UNMATCHED: %s (%s) on %s"
                  % (r["line_token"], r["label"], r["source_page"]))


if __name__ == "__main__":
    main()
