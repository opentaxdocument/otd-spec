
#!/usr/bin/env python
"""Elastic geometry primitives for K-1 face grammar readers.

These are the shared building blocks every grammar `reader` type (see
grammars/GRAMMAR-FORMAT.md) is built on. Nothing here uses absolute
coordinates as a lookup key -- every measurement is derived from the
document under evaluation at call time, per the coordinate_policy
invariant in the grammar format spec.

Design invariants enforced here:
  - Region boundaries are located by heading-anchor text, then bounded by
    the NEXT region's heading anchor -- never a fixed y-range. This is what
    lets one grammar tolerate ~24pt of vertical drift between form years
    (confirmed empirically between the 2019 and 2025 Item L position).
  - Row-band tolerance is derived from the OBSERVED median glyph height on
    the page under test, not a fixed pixel constant.
  - COLUMN-LANE SPLITTING (per-band, not page-wide): Schedule K-1 is a
    two-column form, and the Part III heading sits at the TOP of the page
    in the right column, parallel to the page header -- not below Part II.
    A PAGE-WIDE gutter search cannot find this split: the apparent gap
    between "Schedule K-1 2025" (left, ends x1~99.9) and "Part III..."
    (right, starts x0~236.8) is invisible in a full-page x-coverage
    histogram because OTHER row bands elsewhere on the page occupy that
    same x-range (confirmed empirically -- a page-wide gutter probe found
    no qualifying gap anywhere in 150-480, yet the SAME row at top=48.4
    has a measured 137pt internal word-to-word gap). The correct
    granularity is PER-BAND: cluster words into y-bands first (exactly as
    a single-column model would), then for each resulting band, look for
    its OWN widest internal x-gap and split there if it exceeds a
    threshold generous enough to separate two column headers.
  - Checkbox evaluation is THREE-STATE, always: present | verified_absent |
    unresolved. A frame that cannot be located, or a page with no content
    layer at all, MUST return unresolved -- never a default False. This
    corrects a defect found earlier this session in a standalone probe
    script (no unresolved path existed despite the docstring promising
    one) and in the first draft of the template fit gate's frame counter
    (duplicate-rect overcounting). Both are fixed permanently here.
  - Checkbox governing label is the NEAREST WORD TO THE RIGHT on the same
    row band, confirmed empirically against three independently-checked
    real boxes (Item G -> "Limited", Item H1 -> "Domestic", Item M -> "No").
    Using left-nearest would have inverted the Item M read.
  - Checkbox frames are deduplicated by rounded coordinate before use.
    Flattened real preparer PDFs commonly render each frame as TWO
    overlapping identical rects (confirmed on Copperleaf: raw rect count
    36, unique frame count 18). Extractium's own DetectDrawnCheckBoxes.cs
    solves the same problem via .Deduplicate(MaxOverlap) -- independent
    corroboration this is a real, not hypothetical, PDF-producer behavior.
"""
import sys
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber not available")
    sys.exit(2)


FRAME_MIN = 6.5
FRAME_MAX = 9.5
CONTAINMENT_TOLERANCE = 0.5
FRAME_MATCH_TOLERANCE = 3.0
DEFAULT_BAND_TOL_FALLBACK = 4.2
BAND_TOL_RATIO = 0.6

# Threshold for splitting a single y-band into left/right lane sub-bands.
# Measured evidence: the real two-column header split has a 137pt internal
# gap ("K-1" ends x1=99.9, "2025" begins x0=236.8). Normal same-column text
# (a label followed by its value, or wrapped words) essentially never
# produces an internal gap this wide within one row band on this form.
# Set conservatively below the observed real split to leave margin while
# still being well above ordinary word/field spacing.
BAND_SPLIT_GAP_THRESHOLD = 80.0


# ---------------------------------------------------------------------------
# Row banding (y-cluster first, then per-band lane split on internal gap)
# ---------------------------------------------------------------------------

def compute_row_tolerance(page):
    """Derive row-band tolerance from the OBSERVED median character size."""
    sizes = sorted(c["size"] for c in page.chars if c.get("size"))
    if not sizes:
        return DEFAULT_BAND_TOL_FALLBACK
    median_size = sizes[len(sizes) // 2]
    return round(median_size * BAND_TOL_RATIO, 2)


def _split_words_by_internal_gap(words, threshold=BAND_SPLIT_GAP_THRESHOLD):
    """Given words already clustered into one y-band, find the single
    widest x-gap between adjacent words (sorted left to right). If that
    gap meets/exceeds threshold, split into two word groups there and
    return [(left_words, 'left'), (right_words, 'right')]. Otherwise
    return [(words, 'any')] unchanged.

    This is evaluated PER BAND, not page-wide -- see module docstring for
    why a page-wide gutter search fails on this form.
    """
    ordered = sorted(words, key=lambda w: w["x0"])
    if len(ordered) < 2:
        return [(ordered, "any")]

    best_gap, best_idx = 0.0, None
    for i in range(1, len(ordered)):
        gap = ordered[i]["x0"] - ordered[i - 1]["x1"]
        if gap > best_gap:
            best_gap, best_idx = gap, i

    if best_gap >= threshold and best_idx is not None:
        return [(ordered[:best_idx], "left"), (ordered[best_idx:], "right")]
    return [(ordered, "any")]


def build_row_bands(page, tol=None):
    """Cluster words into row bands by vertical proximity (y-only, exactly
    as a single-column model would), THEN split each resulting band by its
    own widest internal x-gap if that gap is wide enough to indicate two
    physically separate columns sharing the same y-coordinate.

    Each returned band carries a 'lane' key: 'left' | 'right' | 'any'.
    A page with no wide-gap bands anywhere behaves identically to a plain
    single-column banding pass (backward compatible).
    """
    if tol is None:
        tol = compute_row_tolerance(page)

    words = sorted(page.extract_words(), key=lambda w: w["top"])

    y_bands = []
    for w in words:
        placed = False
        for band in y_bands:
            if abs(w["top"] - band["top"]) <= tol:
                band["words"].append(w)
                placed = True
                break
        if not placed:
            y_bands.append({"top": w["top"], "words": [w]})

    all_bands = []
    for yb in y_bands:
        for sub_words, lane in _split_words_by_internal_gap(yb["words"]):
            if not sub_words:
                continue
            sub_words = sorted(sub_words, key=lambda w: w["x0"])
            all_bands.append({
                "top": yb["top"],
                "lane": lane,
                "words": sub_words,
                "text": " ".join(w["text"] for w in sub_words),
                "bottom": max(w["bottom"] for w in sub_words),
                "x0": min(w["x0"] for w in sub_words),
                "x1": max(w["x1"] for w in sub_words),
            })

    all_bands.sort(key=lambda b: b["top"])
    return all_bands


# ---------------------------------------------------------------------------
# Region derivation (elastic -- anchor-bounded, lane-aware, never a fixed
# y-range)
# ---------------------------------------------------------------------------

def derive_region_bounds(page, region_defs, row_bands=None):
    """Locate each region's heading anchor, bound it by the NEXT region's
    anchor (same lane, or an 'any'-lane region). A region whose anchor
    cannot be located is marked status=unresolved rather than silently
    defaulting to a guessed range.

    region_defs: list of {"id": str, "heading_anchors": [str, ...],
                           "lane": "left"|"right"|"any" (optional,
                           default "any")}

    Anchor matching uses WORD-BOUNDARY regex, not naive substring
    containment (Part I / Part II are literal substrings of Part III;
    naive containment collapsed all three onto one band before this fix).

    Search floor is tracked PER LANE, not globally. A single global rising
    floor is wrong for a two-column form: the real Part III heading sits
    at a similar or even earlier y-position than Part II, because it is
    in a different column, not below it. Declaring lane: right on part_iii
    (and lane: left on header/part_i/part_ii) lets its floor start
    independently at -1.0, so it can match its top-of-page anchor without
    being blocked by left-lane content resolved later in scan order.
    """
    if row_bands is None:
        row_bands = build_row_bands(page)

    def anchor_matches(anchor, text):
        pattern = r"(?<!\w)" + re.escape(anchor.lower()) + r"(?!\w)"
        return re.search(pattern, text.lower()) is not None

    lane_of_id = {rd["id"]: rd.get("lane", "any") for rd in region_defs}

    anchor_tops = {}
    anchor_evidence = {}
    anchor_lane_found = {}
    floor_by_lane = {}

    for rd in region_defs:
        rid = rd["id"]
        lane = rd.get("lane", "any")
        floor = floor_by_lane.get(lane, -1.0)

        found_top, found_text, found_lane = None, None, None
        for anchor in rd.get("heading_anchors", []):
            for band in row_bands:
                band_lane = band.get("lane", "any")
                if lane != "any" and band_lane not in (lane, "any"):
                    continue
                if band["top"] <= floor:
                    continue
                if anchor_matches(anchor, band["text"]):
                    found_top, found_text, found_lane = band["top"], band["text"], band_lane
                    break
            if found_top is not None:
                break

        anchor_tops[rid] = found_top
        anchor_evidence[rid] = found_text
        anchor_lane_found[rid] = found_lane
        if found_top is not None:
            floor_by_lane[lane] = found_top
            if lane != "any":
                floor_by_lane["any"] = max(floor_by_lane.get("any", -1.0), found_top)

    ids = [rd["id"] for rd in region_defs]
    bounds = {}
    for i, rid in enumerate(ids):
        top = anchor_tops.get(rid)
        rid_lane = lane_of_id[rid]
        bottom = page.height
        for j in range(i + 1, len(ids)):
            nxt_id = ids[j]
            nxt_lane = lane_of_id[nxt_id]
            if rid_lane != "any" and nxt_lane not in (rid_lane, "any"):
                continue
            nxt_top = anchor_tops.get(nxt_id)
            if nxt_top is not None:
                bottom = nxt_top
                break
        bounds[rid] = {
            "top": top,
            "bottom": bottom,
            "status": "resolved" if top is not None else "unresolved",
            "anchor_text_matched": anchor_evidence.get(rid),
            "lane": anchor_lane_found.get(rid) or rid_lane,
        }
    return bounds


# ---------------------------------------------------------------------------
# Checkbox frame detection (deduplicated)
# ---------------------------------------------------------------------------

def detect_checkbox_frames(page):
    """Detect checkbox-sized stroke-only rectangles, deduplicated by
    rounded coordinate. See module docstring for the duplicate-rect
    rationale (confirmed on real flattened preparer PDFs)."""
    raw = []
    for r in page.rects:
        w, h = r.get("width", 0), r.get("height", 0)
        if FRAME_MIN <= w <= FRAME_MAX and FRAME_MIN <= h <= FRAME_MAX and not r.get("fill", False):
            raw.append(r)

    seen = {}
    for r in raw:
        key = (round(r["x0"], 1), round(r["top"], 1))
        if key not in seen:
            seen[key] = {"x0": r["x0"], "x1": r["x1"], "top": r["top"], "bottom": r["bottom"]}
    return list(seen.values())


def find_frame_near(frames, x0_hint, top_hint, tol=FRAME_MATCH_TOLERANCE):
    """Locate the frame nearest a grammar's anchor hint coordinates.

    Hints in a grammar file are DERIVED FROM OBSERVATION during authoring,
    not asserted as ground truth -- this function still measures the real
    document and simply uses the hint to disambiguate among candidates.
    """
    candidates = [f for f in frames if abs(f["x0"] - x0_hint) <= tol and abs(f["top"] - top_hint) <= tol]
    if not candidates:
        return None
    candidates.sort(key=lambda f: abs(f["x0"] - x0_hint) + abs(f["top"] - top_hint))
    return candidates[0]


# ---------------------------------------------------------------------------
# Checkbox evaluation -- THREE STATE, always
# ---------------------------------------------------------------------------

def evaluate_checkbox(page, frame):
    """Evaluate one checkbox frame. Returns status in
    {present, verified_absent, unresolved} -- NEVER a bare boolean default.
    """
    if frame is None:
        return {"status": "unresolved", "value": None, "method": "frame_not_located"}

    x0 = frame["x0"] - CONTAINMENT_TOLERANCE
    x1 = frame["x1"] + CONTAINMENT_TOLERANCE
    top = frame["top"] - CONTAINMENT_TOLERANCE
    bottom = frame["bottom"] + CONTAINMENT_TOLERANCE

    def contained(el):
        return x0 <= el["x0"] and el["x1"] <= x1 and top <= el["top"] and el["bottom"] <= bottom

    evidence = []
    for c in page.curves:
        if contained(c):
            evidence.append(("curve", c))
    for l in page.lines:
        if contained(l):
            evidence.append(("line", l))
    for ch in page.chars:
        if contained(ch):
            evidence.append(("char", ch))

    if evidence:
        return {
            "status": "present", "value": True,
            "method": "interior_vector", "evidence_count": len(evidence),
        }

    page_text = page.extract_text() or ""
    if len(page.chars) == 0 and len(page_text.strip()) == 0:
        return {"status": "unresolved", "value": None, "method": "no_content_layer"}

    return {"status": "verified_absent", "value": False, "method": "interior_verified_empty"}


def nearest_right_label(page, frame, row_tol):
    """Governing label = nearest word to the RIGHT of the frame, same row
    band. Confirmed empirically; see module docstring."""
    if frame is None:
        return None
    words = page.extract_words()
    frame_center = (frame["top"] + frame["bottom"]) / 2.0
    frame_right = frame["x1"]

    same_row = [w for w in words if abs(((w["top"] + w["bottom"]) / 2.0) - frame_center) <= row_tol]
    to_right = [w for w in same_row if w["x0"] >= frame_right - 1.0]
    if not to_right:
        return None
    to_right.sort(key=lambda w: w["x0"])
    return to_right[0]["text"]


# ---------------------------------------------------------------------------
# Font classification (template vs. overlaid data)
# ---------------------------------------------------------------------------

def classify_fonts(page):
    """Separate subset-embedded template fonts (6-letter uppercase prefix +
    '+') from base/overlaid data fonts."""
    counts = {}
    for c in page.chars:
        fname = c.get("fontname", "unknown")
        counts[fname] = counts.get(fname, 0) + 1

    template, data = {}, {}
    for fname, n in counts.items():
        prefix = fname.split("+", 1)[0]
        if len(prefix) == 6 and prefix.isalpha() and prefix.isupper() and "+" in fname:
            template[fname] = n
        else:
            data[fname] = n
    return {"template_fonts": template, "data_fonts": data}


# ---------------------------------------------------------------------------
# Self-test: exercise every primitive above against real evidence, using
# the actual grammar file's region definitions.
# ---------------------------------------------------------------------------

def _self_test():
    import yaml

    grammar_path = Path("D:/Visual Studio Projects/otd-spec/skills/k1-otd/grammars/k1-1065-2025.grammar.yaml")
    with open(grammar_path, "r", encoding="utf-8") as fh:
        grammar = yaml.safe_load(fh)
    region_defs = grammar["regions"]

    cases = [
        {
            "name": "IRS blank (all frames empty)",
            "pdf": "D:/SecondWind/Artifacts/20260727-k1-face-form-grammar/irs/f1065sk1.pdf",
            "expected_checked_labels": set(),
        },
        {
            "name": "Copperleaf real preparer doc (3 known-checked boxes)",
            "pdf": "D:/SecondWind/Artifacts/20260714-otd-spec-refamiliarization/Examples/"
                   "Copperleaf_Real_Estate_Fund_V_L_P-Meridian_Real_Assets_Aggregator_L_P-Federal-K1.pdf",
            "expected_checked_labels": {"Limited", "Domestic", "No"},
        },
    ]

    all_pass = True
    for case in cases:
        print("=" * 100, flush=True)
        print("CASE: %s" % case["name"], flush=True)

        pdf = pdfplumber.open(case["pdf"])
        page = pdf.pages[0]

        row_tol = compute_row_tolerance(page)
        row_bands = build_row_bands(page, row_tol)
        bounds = derive_region_bounds(page, region_defs, row_bands)

        print("row_tol=%.2f" % row_tol, flush=True)
        print("REGION BOUNDS:", flush=True)
        region_ok = True
        for rid, b in bounds.items():
            print("    %-10s top=%-8s bottom=%-8s status=%-10s lane=%-5s matched=%r" % (
                rid, b["top"], b["bottom"], b["status"], b["lane"], b["anchor_text_matched"]), flush=True)
            if b["status"] != "resolved":
                region_ok = False

        frames = detect_checkbox_frames(page)
        print("FRAMES: %d" % len(frames), flush=True)

        checked_labels = set()
        unresolved_count = 0
        for f in frames:
            result = evaluate_checkbox(page, f)
            label = nearest_right_label(page, f, row_tol)
            if result["status"] == "present":
                checked_labels.add(label)
            if result["status"] == "unresolved":
                unresolved_count += 1
            print("    frame x0=%6.1f top=%6.1f -> status=%-16s label=%s" % (
                f["x0"], f["top"], result["status"], label), flush=True)

        fonts = classify_fonts(page)
        print("FONTS: template=%d  data=%d" % (len(fonts["template_fonts"]), len(fonts["data_fonts"])), flush=True)

        ok = (checked_labels == case["expected_checked_labels"]) and region_ok
        print("EXPECTED checked labels: %s" % case["expected_checked_labels"], flush=True)
        print("OBSERVED checked labels: %s" % checked_labels, flush=True)
        print("UNRESOLVED frames: %d" % unresolved_count, flush=True)
        print("ALL REGIONS RESOLVED: %s" % region_ok, flush=True)
        print("RESULT: %s" % ("PASS" if ok else "FAIL"), flush=True)

        all_pass = all_pass and ok
        pdf.close()

    print("=" * 100, flush=True)
    print("OVERALL: %s" % ("ALL PASS" if all_pass else "SOME FAILED"), flush=True)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(_self_test())
