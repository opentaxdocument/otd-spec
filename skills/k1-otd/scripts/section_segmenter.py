
#!/usr/bin/env python
"""Row-shape tagging and logical-section segmentation for K-1 packages.

WHY SECTIONS AND NOT PAGES
    A page is a printing artifact. A SECTION is the logical unit the tax form
    actually imposes. Real K-1 pages routinely carry several unrelated
    sections, and single sections routinely span pages:

      * one page carried THREE headed blocks (ECI / FDAP / PASSIVE), each
        restating form lines under a different lens -- which is why the same
        box+code pair appeared twice on one page
      * one page carried an activity ROSTER (no amounts) immediately above an
        activity MATRIX (box-keyed rows, one amount per activity column)
      * a detail block beginning on one page continued onto the next with no
        heading of its own

    A page-level classifier must pick ONE role per page and therefore must be
    wrong about the rest. Section-level classification removes that forced
    error, and a page containing exactly one section classifies identically --
    so there is no loss, only added resolution.

DEGRADATION
    If boundary detection finds nothing, the whole page becomes one section
    and behaviour degrades to page-level. That is an honest failure mode: the
    caller can see section_count == 1 and method == "whole_page_fallback".

DIVISION OF RESPONSIBILITY
    STRUCTURAL primitives live here in code: what a monetary token looks
    like, what a box/code reference looks like, what a total row looks like.
    These are tax-form grammar, invariant across producers.

    VOCABULARY lives in signatures/page-signatures.yaml: jurisdiction names
    and codes, title phrases, detail keywords. These vary by producer and
    must be extensible without touching code.

This module segments and describes. It does NOT assign roles -- that is the
classifier's job, and it is deliberately a separate step so the boundaries
can be inspected before any role is asserted.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_classifier import load_signatures, split_sections  # noqa: E402


# ---------------------------------------------------------------------------
# Structural primitives (producer-invariant tax-form grammar)
# ---------------------------------------------------------------------------

# A monetary token: optional paren/minus, optional $, digits with optional
# thousands separators, optional 1-2 decimal places. Percentages excluded --
# "(28%)" is part of a label, not an amount.
MONEY = re.compile(r"^\(?\s*-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s*\)?$|"
                   r"^\(?\s*-?\$?\s*\d+(?:\.\d{1,2})?\s*\)?$")

# A box/code reference at the START of a row. Three observed forms:
#   LINE 11B - ...      BOX 11B ...      11B ...      LINE 11, CODE B ...
BOX_CODE = re.compile(
    r"^\s*(?:line|box)?\s*(\d{1,2})\s*([A-Za-z]{1,2})?\s*(?:,\s*)?"
    r"(?:code\s+([A-Za-z]{1,2})\b)?\s*[-:.\u2013]?\s",
    re.I)

TOTAL_ROW = re.compile(r"^\s*(?:sub)?total\b", re.I)

# A heading that scopes what follows to ONE line/box and declares a breakdown.
SCOPE_HEADING = re.compile(
    r"^\s*(?:line|box)\s*(\d{1,2}[A-Za-z]{0,2})\b.*\b"
    r"(detail|details|breakdown|components?|composition)\b", re.I)

# Column headers keyed by form line number: "LINE 1 LINE 2 LINE 5 ..."
LINE_COL_HEADER = re.compile(
    r"(?i)\bline\s+\d{1,2}[A-Za-z]{0,2}\b"
    r"(?:[^A-Za-z0-9]{0,4}\bline\s+\d{1,2}[A-Za-z]{0,2}\b){2,}")

# Column headers that are bare single letters: "A B C D"
LETTER_COL_HEADER = re.compile(r"^\s*([A-Z](?:\s+[A-Z]){2,})\s*$")

CELL_SPLIT = re.compile(r"\s*\|\s*")


def money_token(tok):
    t = tok.strip()
    if not t or not any(c.isdigit() for c in t):
        return False
    if "%" in t:
        return False
    return bool(MONEY.match(t))


def trailing_amounts(line):
    """Count the run of monetary tokens at the END of a row.

    This is the single most discriminating structural feature available:
      coded entry / detail component -> exactly 1 trailing amount
      grid row (one amount per column) -> N >= 2

    It is what separates '11B OTHER INCOME | 80,700' (a coded entry) from
    '9c UNRECAPTURED SECTION 1250 GAIN 28,760 41,142 49,879 23,434'
    (a four-column activity matrix row that merely LOOKS box-keyed).
    """
    toks = [t for t in re.split(r"[\s|]+", line.strip()) if t]
    n = 0
    for tok in reversed(toks):
        if money_token(tok):
            n += 1
        else:
            break
    return n


def leading_cell(line):
    """First cell of a row, whether pipe-delimited or whitespace-aligned."""
    s = line.strip()
    if "|" in s:
        parts = [p for p in CELL_SPLIT.split(s) if p.strip()]
        return parts[0].strip() if parts else ""
    return s


def is_jurisdiction_cell(cell, sig):
    """True when the first cell names a taxing jurisdiction, by code OR name.

    Requiring a 2-letter USPS code alone scored ZERO on grids whose first
    column reads 'ALABAMA', 'ALASKA', 'ARIZONA'. Both forms must resolve.
    """
    c = cell.strip()
    if not c:
        return False
    up = c.upper()
    if len(up) == 2 and up.isalpha() and up in sig.get("_jurisdictions", set()):
        return True
    lower = " ".join(re.findall(r"[a-z]+", c.lower()))
    if not lower:
        return False
    names = sig.get("_jurisdiction_names", set())
    if lower in names:
        return True
    # A grid row prints the name followed by amounts; take the leading words.
    words = lower.split()
    for n in (3, 2, 1):
        if len(words) >= n and " ".join(words[:n]) in names:
            return True
    return False


def _flatten_names(raw):
    out = set()
    for item in (raw or []):
        for part in str(item).split(","):
            p = " ".join(re.findall(r"[a-z]+", part.lower()))
            if p:
                out.add(p)
    return out


def prepare(sig):
    """Attach derived vocabulary sets. Idempotent."""
    if "_jurisdiction_names" not in sig:
        sig["_jurisdiction_names"] = _flatten_names(
            sig.get("jurisdiction_names"))
    return sig


# ---------------------------------------------------------------------------
# Row shapes
# ---------------------------------------------------------------------------

DATA_SHAPES = {"CODED_ENTRY", "LINE_REF_AMOUNT", "LABEL_AMOUNT",
               "GRID_ROW", "JURISDICTION_ROW", "ROSTER_ROW"}
HEADER_SHAPES = {"HEADING", "SCOPE_HEADING", "COLUMN_HEADER"}

# A real section heading is a PHRASE, not a fragment. Text extraction renders
# rotated sidebar labels one character per line ("E", "O", "N", "S", "M") and
# emits sentinel fragments ("* STMT", "MIT A", "BI", "T."). Admitting those as
# headings opened a boundary on nearly every row: a page with exactly TWO
# logical sections was shredded into 25.
MIN_HEADING_WORDS = 2
MIN_HEADING_ALPHA_CHARS = 8

# Repeated standalone booleans mark a ROSTER row -- an entity list with
# yes/no attribute columns. It carries no monetary amount, so without this
# it would be misread as a heading.
BOOLEAN_TOKEN = re.compile(r"^(?:yes|no|y|n|true|false)$", re.I)


def tag_row(line, sig):
    """Classify one physical row into a producer-neutral shape."""
    s = line.strip()
    if not s:
        return "BLANK"
    if s.startswith("=") or set(s) <= set("-=_| "):
        return "BLANK"

    if SCOPE_HEADING.search(s):
        return "SCOPE_HEADING"
    if TOTAL_ROW.match(s):
        return "TOTAL"

    amts = trailing_amounts(s)

    if amts == 0:
        if LINE_COL_HEADER.search(s) or LETTER_COL_HEADER.match(s):
            return "COLUMN_HEADER"

        toks = [t for t in re.split(r"[\s|]+", s) if t]
        bools = sum(1 for t in toks if BOOLEAN_TOKEN.match(t.strip(",.")))
        words = re.findall(r"[A-Za-z][A-Za-z'&/().,-]*", s)
        if not words:
            return "NOISE"
        alpha = " ".join(words)
        # Roster: a label plus repeated standalone booleans.
        if bools >= 2 and (len(toks) - bools) >= 1:
            return "ROSTER_ROW"
        # Too short to be a section heading -> extraction artifact, not structure.
        if (len(words) < MIN_HEADING_WORDS
                or len(alpha.replace(" ", "")) < MIN_HEADING_ALPHA_CHARS):
            return "NOISE"
        # A heading names a section; it does not terminate like a sentence.
        # All-caps narrative body text is common on footnote pages, so the
        # terminator guard is what separates a section name from prose.
        if (len(words) <= 12 and alpha.upper() == alpha
                and not s.rstrip().endswith((".", ":", ";", ","))):
            return "HEADING"
        return "PROSE"

    if amts == 1:
        m = BOX_CODE.match(s)
        if m:
            code = m.group(2) or m.group(3)
            # A trailing letter that is really part of a word ("11 INTEREST")
            # is rejected: a code is 1-2 letters standing alone or fused to
            # the digits, never the first letters of a longer word.
            if code and len(code) <= 2:
                after = s[m.end(0):] if m.end(0) <= len(s) else ""
                fused = re.match(r"^\s*(?:line|box)?\s*\d{1,2}[A-Za-z]{1,2}\b",
                                 s, re.I)
                if fused or m.group(3):
                    return "CODED_ENTRY"
            return "LINE_REF_AMOUNT"
        return "LABEL_AMOUNT"

    # Two or more trailing amounts -> a grid row.
    if is_jurisdiction_cell(leading_cell(s), sig):
        return "JURISDICTION_ROW"
    return "GRID_ROW"


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def segment_page(text, sig, page_num=None):
    """Split one page into logical sections.

    A boundary opens at:
      * any HEADING / SCOPE_HEADING / COLUMN_HEADER
      * a change in the dominant DATA shape (BLANK/PROSE runs are tolerated)
      * the first data row after a TOTAL row (a totalled block has closed)

    Returns a list of section dicts carrying the line range, heading, row
    shape histogram, and the reason the section opened.
    """
    prepare(sig)
    body, _ = split_sections(text)
    raw_lines = body.splitlines()
    tagged = [(i, l, tag_row(l, sig)) for i, l in enumerate(raw_lines)]

    # WRAPPED-ROW REASSEMBLY.
    # A long label wraps, leaving its amount alone on the following line:
    #     LINE 15Q - CREDITS - UNUSED INVESTMENT CREDIT FROM THE QUALIFYING...
    #     119,200
    # Tagged independently these read as PROSE followed by a bare number, so a
    # genuine coded entry is LOST and the fragment scores as a footnote -- which
    # would both drop a real $119,200 credit and fabricate a statement node.
    # Joining a box/code-prefixed row that has no amount to an immediately
    # following amount-only row restores the logical row. Producer-neutral: label
    # wrapping is a function of column width, not of any vendor's wording.
    joined = []
    _skip = set()
    for _k in range(len(tagged)):
        _i, _l, _sh = tagged[_k]
        if _i in _skip:
            continue
        _merged = None
        if (_sh in ("PROSE", "HEADING") and trailing_amounts(_l) == 0
                and BOX_CODE.match(_l.strip())):
            for _j in range(_k + 1, min(_k + 3, len(tagged))):
                _ni, _nl, _nsh = tagged[_j]
                if _nsh == "BLANK":
                    continue
                # Amount-only continuation: carries a value and no real words.
                if (trailing_amounts(_nl) >= 1
                        and not re.search(r"[A-Za-z]{3,}", _nl)):
                    _merged = _l.rstrip() + " " + _nl.strip()
                    _skip.add(_ni)
                break
        if _merged:
            joined.append((_i, _merged, tag_row(_merged, sig)))
        else:
            joined.append((_i, _l, _sh))
    tagged = joined

    # A SINGLE row of a different shape inside an otherwise homogeneous block is
    # a rendering artifact -- a wrapped cell, a stray column, a subtotal printed
    # with one amount inside a multi-column grid. It is not a section boundary.
    # Without this, a table alternating 1-amount and N-amount rows fragmented
    # into 21 "sections" that were all one table.
    data_seq = [(i, sh) for i, _, sh in tagged if sh in DATA_SHAPES]
    transient = set()
    for _k in range(1, len(data_seq) - 1):
        if (data_seq[_k][1] != data_seq[_k - 1][1]
                and data_seq[_k + 1][1] == data_seq[_k - 1][1]):
            transient.add(data_seq[_k][0])

    sections = []
    cur = None
    last_data_shape = None
    just_closed = False

    def open_section(idx, reason, heading=None):
        return {"page": page_num, "line_start": idx, "line_end": idx,
                "open_reason": reason, "heading": heading,
                "rows": [], "shapes": {}}

    def close(sec):
        if sec and any(s in DATA_SHAPES or s in ("TOTAL", "PROSE")
                       for s in sec["shapes"]):
            sections.append(sec)
        elif sec and sec["shapes"]:
            sections.append(sec)

    for idx, line, shape in tagged:
        if shape in ("BLANK", "NOISE"):
            # NOISE must NEVER open a boundary. Rotated-sidebar fragments and
            # sentinel marks are extraction artifacts, not document structure.
            if cur:
                cur["line_end"] = idx
                if shape == "NOISE":
                    cur["shapes"]["NOISE"] = cur["shapes"].get("NOISE", 0) + 1
            continue

        boundary = None
        heading = None

        if shape in HEADER_SHAPES:
            boundary = "header:%s" % shape
            heading = line.strip()
        elif shape in DATA_SHAPES:
            if just_closed:
                boundary = "after_total"
            elif last_data_shape and shape != last_data_shape:
                # Tolerate LINE_REF_AMOUNT / CODED_ENTRY mixing inside one
                # referencing block; they are the same logical row family.
                same_family = {shape, last_data_shape} <= {
                    "CODED_ENTRY", "LINE_REF_AMOUNT"}
                if not same_family and idx not in transient:
                    boundary = "shape_change:%s->%s" % (last_data_shape, shape)

        # Consecutive header rows form ONE header block, not N sections. A
        # section opens when a header block is followed by DATA. Producers
        # routinely stack a page title, a scope heading, and a column header
        # before the first data row; treating each as a boundary created
        # three empty sections and detached the heading from its own body.
        has_data = cur is not None and any(
            k in DATA_SHAPES for k in cur["shapes"])
        if shape in HEADER_SHAPES and cur is not None and not has_data:
            boundary = None
            if heading:
                cur["heading"] = ((cur["heading"] + " | " + heading)
                                  if cur["heading"] else heading)

        if boundary or cur is None:
            close(cur)
            cur = open_section(idx, boundary or "page_start", heading)
        elif heading and not cur["heading"]:
            cur["heading"] = heading

        cur["line_end"] = idx
        cur["rows"].append({"line": idx, "shape": shape, "text": line.strip()})
        cur["shapes"][shape] = cur["shapes"].get(shape, 0) + 1

        if shape in DATA_SHAPES:
            last_data_shape = shape
            just_closed = False
        elif shape == "TOTAL":
            just_closed = True
        elif shape in HEADER_SHAPES:
            last_data_shape = None
            just_closed = False

    close(cur)

    if not sections:
        sections = [{"page": page_num, "line_start": 0,
                     "line_end": max(len(raw_lines) - 1, 0),
                     "open_reason": "whole_page_fallback", "heading": None,
                     "rows": [], "shapes": {}}]

    for si, sec in enumerate(sections, 1):
        sec["index"] = si
        sec["row_count"] = len(sec["rows"])
        sec["dominant_shape"] = (max(
            ((k, v) for k, v in sec["shapes"].items() if k in DATA_SHAPES),
            key=lambda kv: kv[1])[0] if any(
                k in DATA_SHAPES for k in sec["shapes"]) else None)
        sec["text"] = "\n".join(r["text"] for r in sec["rows"])
        sec["trailing_amount_mode"] = _mode(
            [trailing_amounts(r["text"]) for r in sec["rows"]
             if r["shape"] in DATA_SHAPES])
    return sections


def _mode(values):
    if not values:
        return None
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def segment_document(pages, sig):
    """Segment every page, then merge sections that continue across a page
    break: a first-on-page section with no heading of its own whose dominant
    shape and trailing-amount mode match the last section of the prior page.
    """
    prepare(sig)
    per_page = []
    for p in pages:
        per_page.append(segment_page(p["text"], sig, p["page"]))

    merged = []
    for secs in per_page:
        for si, sec in enumerate(secs):
            if (merged and si == 0 and not sec["heading"]
                    and sec["dominant_shape"]
                    and merged[-1]["dominant_shape"] == sec["dominant_shape"]
                    and merged[-1]["trailing_amount_mode"]
                    == sec["trailing_amount_mode"]):
                prev = merged[-1]
                prev.setdefault("continued_pages", []).append(sec["page"])
                prev["rows"].extend(sec["rows"])
                prev["row_count"] += sec["row_count"]
                for k, v in sec["shapes"].items():
                    prev["shapes"][k] = prev["shapes"].get(k, 0) + v
                prev["text"] += "\n" + sec["text"]
                continue
            merged.append(sec)
    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Segment K-1 pages into logical sections")
    ap.add_argument("--text-dir", required=True)
    ap.add_argument("--signatures", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pages", default=None,
                    help="Comma-separated page numbers to show in detail")
    args = ap.parse_args()

    sig = prepare(load_signatures(args.signatures))
    d = Path(args.text_dir)
    files = sorted(d.glob("page_*.txt"))
    if not files:
        print("ERROR: no page_*.txt in %s" % d, file=sys.stderr)
        return 1

    pages = []
    for f in files:
        m = re.search(r"page_(\d+)", f.name)
        pages.append({"page": int(m.group(1)) if m else 0,
                      "text": f.read_text(encoding="utf-8", errors="replace")})

    sections = segment_document(pages, sig)

    print("=" * 112)
    print("%-6s %-4s %-24s %-20s %5s %5s  %s" % (
        "page", "sec", "dominant_shape", "open_reason", "rows", "amts",
        "heading"))
    print("=" * 112)
    for sec in sections:
        pg = str(sec["page"])
        if sec.get("continued_pages"):
            pg += "+" + ",".join(str(p) for p in sec["continued_pages"])
        print("%-6s %-4s %-24s %-20s %5d %5s  %s" % (
            pg, sec["index"], str(sec["dominant_shape"]),
            sec["open_reason"][:20], sec["row_count"],
            str(sec["trailing_amount_mode"]),
            (sec["heading"] or "")[:38]))

    print()
    print("total sections: %d across %d pages" % (len(sections), len(pages)))

    detail = set()
    if args.pages:
        detail = {int(x) for x in args.pages.split(",") if x.strip()}
    for sec in sections:
        if sec["page"] not in detail:
            continue
        print()
        print("-" * 112)
        print("PAGE %s SECTION %d  heading=%r  open=%s  shapes=%s"
              % (sec["page"], sec["index"], sec["heading"],
                 sec["open_reason"], sec["shapes"]))
        print("-" * 112)
        for r in sec["rows"][:14]:
            print("  %-18s %s" % (r["shape"], r["text"][:88]))
        if sec["row_count"] > 14:
            print("  ... %d more rows" % (sec["row_count"] - 14))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({
            "signatures": sig["_path"],
            "total_pages": len(pages),
            "total_sections": len(sections),
            "sections": [{k: v for k, v in s.items() if k != "rows"}
                         for s in sections],
        }, indent=2), encoding="utf-8")
        print()
        print("WROTE %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
