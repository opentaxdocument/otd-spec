
#!/usr/bin/env python
"""K-1 face reader: consumes a grammar (grammars/*.grammar.yaml) and the
elastic geometry primitives (elastic_geometry.py) to produce a full
evidence-enveloped face_page.json for a given PDF.

Design invariants (see GRAMMAR-FORMAT.md, elastic_geometry.py docstrings):
  - Every field emits a full evidence envelope, never a bare value.
  - Status is always one of grammar['status_values'] -- never a silent
    default. A reader that cannot locate its anchor emits 'unresolved'.
  - Readers not yet implemented emit 'unresolved' with
    method='reader_not_implemented' rather than crash or guess.
  - Coordinates are observations attached as evidence, never a lookup key
    used ahead of label matching.

Known limitation carried into this wave (documented, not silently
resolved): checkbox fields with no coordinate hint (item_k2, item_k3,
item_i2) rely entirely on label-band text matching. Earlier session
evidence showed a frame near the Part II/III column boundary
(x0=294.4, top=411) picked up a Part III box label ("9c") as its
nearest-right neighbor instead of its true governing text, because both
sit at the same y across the gutter. Whether the Wave 4 lane-split fix
resolves this is verified empirically in tests/test_face_reader.py, not
assumed.
"""
import argparse
import sys
import re
import json
import hashlib
import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import pdfplumber
    import yaml
except ImportError as e:
    print("ERROR: missing dependency: %s" % e)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).parent))
from elastic_geometry import (
    compute_row_tolerance, build_row_bands, derive_region_bounds,
    detect_checkbox_frames, evaluate_checkbox, find_frame_near,
    classify_fonts,
)

FRAME_MATCH_TOLERANCE = 3.0
NUMERIC_RE = re.compile(r"^\(?\$?-?[\d,]+\.?\d*\)?%?$")
DEFAULT_GRAMMAR = (
    Path(__file__).resolve().parent.parent
    / "grammars"
    / "k1-1065-2025.grammar.yaml"
)


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _norm_collapse(s):
    return re.sub(r"\s+", " ", _norm(s))


def label_matches(band_text, label):
    """Elastic label match: full normalized phrase, then a 2-word prefix
    fallback, then a single leading-word fallback -- in that order of
    confidence. Handles wrapped/truncated printed text without requiring
    an exact literal match."""
    if not label:
        return False
    band_n = _norm_collapse(band_text)
    label_n = _norm_collapse(label)
    if not label_n:
        return False
    if label_n in band_n:
        return True
    words = label_n.split()
    if len(words) >= 2:
        prefix = " ".join(words[:2])
        if prefix in band_n:
            return True
    if words and words[0] in band_n.split():
        return True
    return False



def fuse_paren_tokens(words):
    """Repair accounting negatives whose parentheses render as SEPARATE tokens.

    Measured defect: the source prints

        Withdrawals and distributions . . . $( 27,000 )

    and text extraction emits THREE words -- '$(' , '27,000' , ')'.
    parse_numeric does honour parens-as-negative, but only when they are FUSED
    to the numeric token ('(112,600)' -> -112600.0, correct). Split across
    tokens the negation markers never reach the parser, so a printed (27,000)
    was asserted as +27,000 -- a sign flip on a tax document, the same
    fabrication class as reporting an unknown checkbox as false.

    The document carries its own disproof: Item L continuity closes only at
    -27,000 (113,000 + 168,000 + 7,206,200 - 27,000 = 7,460,200); at +27,000
    it computes 7,514,200 against a printed ending capital of 7,460,200.

    Repaired ONCE at the word-list boundary rather than inside each reader, so
    every extraction path inherits the correct sign instead of four separate
    fixes drifting apart. The fused token keeps the NUMERIC token's x0 so
    column assignment is unchanged, and carries `_fused_paren` so the
    reconstruction stays auditable.
    """
    out, i, n = [], 0, len(words)
    while i < n:
        w = words[i]
        t = w["text"]
        # An opener carries '(' and no digits: '(' or '$('.
        if "(" in t and not re.search(r"\d", t) and i + 1 < n:
            nxt = words[i + 1]
            if NUMERIC_RE.match(nxt["text"]) and ")" not in nxt["text"]:
                closer = None
                if i + 2 < n and ")" in words[i + 2]["text"] \
                        and not re.search(r"\d", words[i + 2]["text"]):
                    closer = words[i + 2]
                if closer is not None:
                    fused = dict(nxt)
                    fused["text"] = "(" + nxt["text"].strip("()") + ")"
                    fused["x1"] = closer.get("x1", nxt.get("x1"))
                    fused["_fused_paren"] = True
                    out.append(fused)
                    i += 3
                    continue
        out.append(w)
        i += 1
    return out


def band_words(band):
    """x-sorted words for a band, with split-paren negatives repaired."""
    return fuse_paren_tokens(
        sorted(band["words"], key=lambda w: w["x0"]))


def parse_numeric(token, signed_hint=False):
    """Parse a numeric-looking token, honoring parens-as-negative
    (accounting notation) regardless of the field's declared sign."""
    t = token.strip()
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    t = t.replace("$", "").replace(",", "").replace("%", "")
    try:
        val = float(t)
    except ValueError:
        return None
    if negative:
        val = -abs(val)
    return val


def find_band_for_frame(row_bands, frame, row_tol):
    """Locate the row band whose y-position matches a checkbox frame.
    Matched by top-proximity only (frames are not part of extract_words
    output, so there is no direct containment test)."""
    best, best_dist = None, None
    for band in row_bands:
        dist = abs(band["top"] - frame["top"])
        if dist <= row_tol * 1.5 and (best is None or dist < best_dist):
            best, best_dist = band, dist
    return best


def right_text_for_frame(band, frame):
    if band is None:
        return ""
    words = [w for w in band["words"] if w["x0"] >= frame["x1"] - 1.0]
    words.sort(key=lambda w: w["x0"])
    return " ".join(w["text"] for w in words)



def left_text_for_frame(band, frame):
    """Text to the LEFT of a frame within its row band, in reading order.

    Mirror of right_text_for_frame. Required because the 2025 K-1 face uses
    BOTH label directions and the reader previously only looked right:

        [X] Limited partner ...................  label to the RIGHT
        ... check here ..................... [X] label to the LEFT

    Measured: item_i2, item_k2 and item_k3 all place the box at x0=294.40,
    the right edge of the left column, with the sentence to its left. Their
    frames were detected perfectly by the geometric primitive and then thrown
    away because the binder swept rightward into right-column content.
    """
    if band is None:
        return ""
    words = [w for w in band["words"]
             if w.get("x1", w["x0"]) <= frame["x0"] + 1.0]
    words.sort(key=lambda w: w["x0"])
    return " ".join(w["text"] for w in words)


def side_text_for_frame(band, frame, association):
    """Adjacent text on the side the grammar's label_association declares.

    `label_association` was already declared per field in the grammar; the
    reader simply never branched on it. The vocabulary existed, the behaviour
    did not.
    """
    if association == "nearest_left":
        return left_text_for_frame(band, frame)
    if association == "either":
        return "%s %s" % (left_text_for_frame(band, frame),
                          right_text_for_frame(band, frame))
    return right_text_for_frame(band, frame)


def _label_rank(text, label, association):
    """Rank a frame as a label candidate: (tier, gap), or None if no match.

    Two problems make bare containment unusable, both measured on the real
    page:

    1. Frames sharing a row band inherit each other's labels. The Final K-1
       frame's right text is 'Final K-1 Amended K-1 OMB No. 1545-0123', which
       contains BOTH labels -- so containment reports ambiguity on a page a
       human reads without hesitation. Resolved by GAP: the governing label is
       the one IMMEDIATELY adjacent, so rank by how close the match sits to
       the frame edge (0 == flush against it).

    2. label_matches' elastic fallbacks fire too eagerly across siblings.
       box_23's two-word prefix 'more than' matches box_22's row verbatim.
       Resolved by TIER: an exact-phrase match anywhere always outranks a
       prefix match, so reduced probes are consulted only when no candidate
       matches in full.

    tier 0 = full phrase, 1 = two-word prefix, 2 = leading word.
    """
    tn = _norm_collapse(text)
    ln = _norm_collapse(label)
    if not tn or not ln:
        return None
    probes = [ln]
    words = ln.split()
    if len(words) >= 2:
        probes.append(" ".join(words[:2]))
    if words:
        probes.append(words[0])
    for tier, probe in enumerate(probes):
        idx = tn.find(probe)
        if idx >= 0:
            if association == "nearest_left":
                gap = float(len(tn) - (idx + len(probe)))
            else:
                gap = float(idx)
            return (tier, gap)
    return None



def _side_text_variants(row_bands, band, frame, association, row_tol,
                        max_extra=2, gap_factor=2.5):
    """Side text for a frame: its own band first, then progressively widened by
    absorbing adjacent bands to reconstruct a WRAPPED printed label.

    Returns [(extra_bands, text), ...] nearest-first, so a caller can prefer a
    single-band match and fall back to a reassembled one.

    MEASURED NEED (item_k3). The form prints:
        top=591.50  'K3 Check if any of the above liability is subject to
                     guarantees or other'
        top=600.50  'payment obligations by the partner. See instructions ...'
    The frame is at top=600.25 and binds to the 600.50 band, whose left text is
    only 'payment obligations by the partner. See instructions . . . . .'. The
    declared phrase "subject to guarantees or other payment obligations" spans
    BOTH baselines, so no single band can contain it.

    This is the same defect class as D3 (Qualified nonrecourse / financing)
    recurring on the checkbox path -- two independent occurrences, so wrapped
    labels are a general property of this form, not a one-off.

    GUARDS:
      * gap_factor: an adjacent band is only absorbed when its baseline gap is
        <= row_tol * 2.5. Measured gap here is 9.00 against a limit of 10.50,
        while the box_22/box_23 rows sit 12.00 apart and are correctly NOT
        stitched together. The same constant shape as the D3 merge guard.
      * max_extra caps reassembly at two additional bands, so a runaway sweep
        cannot manufacture a match from unrelated prose.
      * Widening is directional only. For association 'either' the base text
        already spans both sides and a prepend/append order would be arbitrary,
        so no widening is attempted rather than guessing one.
    """
    if band is None:
        return []
    base = side_text_for_frame(band, frame, association)
    out = [(0, base)]
    if association not in ("nearest_left", "nearest_right"):
        return out

    if association == "nearest_left":
        neighbours = sorted([b for b in row_bands if b["top"] < band["top"] - 0.01],
                            key=lambda b: -b["top"])
    else:
        neighbours = sorted([b for b in row_bands if b["top"] > band["top"] + 0.01],
                            key=lambda b: b["top"])

    acc = base
    prev_top = band["top"]
    limit = row_tol * gap_factor
    for i, nb in enumerate(neighbours[:max_extra], 1):
        if abs(nb["top"] - prev_top) > limit:
            break
        seg = side_text_for_frame(nb, frame, association)
        if association == "nearest_left":
            acc = ("%s %s" % (seg, acc)).strip()
        else:
            acc = ("%s %s" % (acc, seg)).strip()
        out.append((i, acc))
        prev_top = nb["top"]
    return out


def locate_checkbox_frame(ctx, region, anchors):
    """Locate a single checkbox frame, FRAME-FIRST.

    A checkbox's EXISTENCE is geometric fact -- detect_checkbox_frames finds
    every stroke-only square on the page and does not care what any grammar
    expects. Its IDENTITY is an inference. The frame set is therefore
    authoritative and a region bound is only a PREFERENCE: it may narrow the
    search, it may never delete a frame.

    Measured defect this replaces: the `header` region derives to top=48.404,
    so a floor of 48.404-5 excluded the Final K-1 and Amended K-1 frames at
    top=38.00 outright (regions=<NONE>), and the box-16 frame at top=180.50
    exceeded header bottom+5=150.319. Three frames were deleted by a DERIVED
    heuristic before any matching ran -- a derived bound vetoing observed
    evidence, which is the fail-open shape inverted.

    Returns (frame_or_None, disposition). A disposition suffixed
    `_outside_region` records that the frame was bound from the full page
    because the region window did not contain it. The widened pass demands an
    unambiguous bind -- a single best label match or two coordinate hints --
    so widening can never guess.
    """
    frames = ctx["frames"]
    bounds = ctx["region_bounds"].get(region, {})
    top, bottom = bounds.get("top"), bounds.get("bottom")

    in_region = frames
    if top is not None:
        lo = top - 5
        hi = (bottom + 5) if bottom is not None else 1e9
        in_region = [f for f in frames if lo <= f["top"] <= hi]

    x0_hint = anchors.get("frame_x0_hint")
    top_hint = anchors.get("frame_row_top_hint")
    governing_label = anchors.get("governing_label", "")
    association = anchors.get("label_association", "nearest_right")

    def attempt(pool, suffix):
        """Bind within `pool`. Returns (frame, disposition) on success,
        (None, 'ambiguous...') when genuinely ambiguous, or (None, None) when
        simply not found -- so the caller can distinguish 'no' from 'unclear'.
        """
        if not pool:
            return None, None

        if x0_hint is not None and top_hint is not None:
            f = find_frame_near(pool, x0_hint, top_hint,
                               tol=FRAME_MATCH_TOLERANCE)
            if f:
                return f, "coordinate_hint" + suffix

        narrowed = pool
        if top_hint is not None:
            narrowed = [f for f in pool
                        if abs(f["top"] - top_hint) <= FRAME_MATCH_TOLERANCE * 2]

        if not governing_label:
            if len(narrowed) == 1:
                return narrowed[0], "coordinate_hint_only" + suffix
            return None, None

        # Rank candidates by tier -> extra bands -> gap, in that order.
        #
        #   tier   an exact-phrase match always beats a reduced-prefix match,
        #          so box_23's two-word prefix 'more than' cannot steal
        #          box_22's row.
        #   extra  a label found in the frame's OWN band always beats one
        #          reassembled across bands, so widening can only ever recover
        #          a frame that single-band matching could not bind at all.
        #   gap    among equals, the governing label is the one IMMEDIATELY
        #          adjacent to the frame.
        #
        # The gap tiebreak is load-bearing for item_k3: widening lets the
        # box_22 frame at x0=331.20 see the same prepended text, but its
        # accumulated string is the k3 frame's plus a trailing ' 22', so the
        # phrase sits strictly closer to the true frame at x0=294.40.
        ranked = []
        for f in narrowed:
            band = find_band_for_frame(ctx["row_bands"], f, ctx["row_tol"])
            for n_extra, text in _side_text_variants(
                    ctx["row_bands"], band, f, association, ctx["row_tol"]):
                r = _label_rank(text, governing_label, association)
                if r is not None:
                    ranked.append({"tier": r[0], "gap": r[1],
                                   "extra": n_extra, "frame": f})
                    break
        if not ranked:
            return None, None
        best_tier = min(r["tier"] for r in ranked)
        ranked = [r for r in ranked if r["tier"] == best_tier]
        best_extra = min(r["extra"] for r in ranked)
        ranked = [r for r in ranked if r["extra"] == best_extra]
        wrapped = "_wrapped" if best_extra else ""
        if len(ranked) == 1:
            return ranked[0]["frame"], "label_match" + wrapped + suffix
        ranked.sort(key=lambda r: r["gap"])
        if ranked[0]["gap"] < ranked[1]["gap"]:
            return (ranked[0]["frame"],
                    "label_match_adjacent" + wrapped + suffix)
        return None, "ambiguous" + suffix

    frame, disp = attempt(in_region, "")
    if frame is not None:
        return frame, disp
    if disp:
        return None, disp

    # The region preference did not yield a frame. Widen to the whole page --
    # but only an unambiguous bind may commit, so widening cannot guess.
    can_widen = bool(governing_label) or (x0_hint is not None
                                          and top_hint is not None)
    if can_widen and len(in_region) != len(frames):
        frame, disp = attempt(frames, "_outside_region")
        if frame is not None:
            return frame, disp
        if disp:
            return None, disp

    if not governing_label:
        return None, "no_label_no_unique_hint"
    return None, "not_found"



def _merge_bands(group):
    """Reconstruct one logical row from consecutive physical bands.

    A label too long for its cell wraps, so the label's tokens and the values
    that belong to them end up on different baselines. Merging restores the
    logical row: the values land in the same band as the label they belong to,
    which is what every downstream reader assumes. `_wrapped_from` records the
    baselines that were joined so the reconstruction stays auditable rather
    than looking like a single printed line.
    """
    words = sorted((w for b in group for w in b["words"]), key=lambda w: w["x0"])
    return {
        "top": min(b["top"] for b in group),
        "bottom": max(b["bottom"] for b in group),
        "x0": min(b["x0"] for b in group),
        "x1": max(b["x1"] for b in group),
        "text": " ".join(b["text"] for b in group),
        "words": words,
        "_wrapped_from": [b["top"] for b in group],
    }


def _find_wrapped_anchor(bands, anchors, top_hint=None, max_span=3):
    """Match a declared anchor whose tokens WRAP across consecutive bands.

    Measured defect this closes: the source prints

        Qualified nonrecourse
        financing . . . $ 10,000 $ 52,600

    while the grammar declares anchor "Qualified nonrecourse financing" as one
    string. No single row band contains that token sequence, so the anchor
    matcher returned None and the row was written off `row_anchor_not_found` --
    silently discarding a real 10,000 / 52,600 liability pair. The adjacent
    rows ("Nonrecourse", "Recourse") resolved fine, so the loss was invisible
    in aggregate: it is the LABEL that splits, not the values.

    This is NOT a misspelling and fuzzy matching would not help -- every token
    is spelled correctly, they are merely on two baselines. Vertical adjacency
    is required (gap within ~2.5x median band height) so the search cannot
    stitch a label across a section boundary, and the span is capped, so a
    long prose page cannot be collapsed into one row.
    """
    ordered = sorted(bands, key=lambda b: b["top"])
    heights = sorted(b["bottom"] - b["top"] for b in ordered
                     if b["bottom"] > b["top"])
    med_h = heights[len(heights) // 2] if heights else 10.0
    gap_limit = med_h * 2.5

    patterns = [r"(?<!\w)" + re.escape(_norm(a)) + r"(?!\w)" for a in anchors]
    hits = []
    for i in range(len(ordered)):
        for n in range(2, max_span + 1):
            if i + n > len(ordered):
                break
            group = ordered[i:i + n]
            if any((b["top"] - a["top"]) > gap_limit
                   for a, b in zip(group, group[1:])):
                break
            joined = _norm(" ".join(g["text"] for g in group))
            if any(re.search(p, joined) for p in patterns):
                hits.append(_merge_bands(group))
                break          # prefer the SMALLEST span from this start
    if not hits:
        return None
    if top_hint is not None:
        hits.sort(key=lambda b: abs(b["top"] - top_hint))
    return hits[0]


def find_band_by_anchor(row_bands, anchors, floor=None, ceiling=None, top_hint=None):
    """Find a row band by anchor text (single string or list of variants),
    optionally scoped to a y-window and preferring proximity to a hint."""
    if isinstance(anchors, str):
        anchors = [anchors]
    candidates = row_bands
    if floor is not None:
        candidates = [b for b in candidates if b["top"] >= floor - 2]
    if ceiling is not None:
        candidates = [b for b in candidates if b["top"] <= ceiling + 2]

    def matches_any(band):
        for a in anchors:
            pattern = r"(?<!\w)" + re.escape(_norm(a)) + r"(?!\w)"
            if re.search(pattern, _norm(band["text"])):
                return True
        return False

    found = [b for b in candidates if matches_any(b)]
    if not found:
        # Single-band matching always wins when it succeeds; the wrapped
        # fallback fires ONLY on total failure, so no existing match can be
        # changed by it. Absence of a single-band match is not evidence the
        # row is absent -- it may simply be a label that wrapped.
        return _find_wrapped_anchor(candidates, anchors, top_hint)
    if top_hint is not None:
        found.sort(key=lambda b: abs(b["top"] - top_hint))
    return found[0]


def extract_last_numeric(band, exclude_leading_token=None):
    """Return (raw_text, value) for the rightmost numeric-looking token
    in a band, excluding a leading token that is just the box's own
    printed number (not its value) if it appears first in the band."""
    words = band_words(band)
    numeric_words = [w for w in words if NUMERIC_RE.match(w["text"])]
    if not numeric_words:
        return None
    if exclude_leading_token and words and len(numeric_words) >= 1:
        first_word_norm = words[0]["text"].strip(".").lower()
        if first_word_norm == str(exclude_leading_token).lower() and numeric_words[0] is words[0]:
            numeric_words = numeric_words[1:]
    if not numeric_words:
        return None
    last = numeric_words[-1]
    val = parse_numeric(last["text"])
    return (last["text"], val)

def extract_numeric_bounded(band, own_box_number, boundary_tokens, label=None, row_bands=None, value_line_offset=10.21):
    """Bound numeric-token search to THIS box's segment of a row band that
    may contain multiple box entries concatenated (Part III's dense
    multi-box-per-row grid places two unrelated boxes in one y-band --
    confirmed empirically: box_1's band also contains box_14's label,
    box_12's band also contains box_21's label). Segment bounds:
    start at own_box_number's token position, end at the next OTHER
    known box-number token in the same band (or end of band if none).

    Part III boxes are TWO-LINE cells: the label prints on one baseline,
    the dollar value on a SEPARATE line ~10.2pt below it (confirmed
    empirically: box_1 label top=74.50, value '556,000' top=84.71;
    box_12 label top=506.50, value '75,000' top=516.71 -- both exactly
    10.21pt offset). The label's own band therefore legitimately has NO
    numeric value token; the search must also check the next band down.
    Excludes the leading box-number token itself from being read as a
    value, since it is numeric-shaped ("1", "12", "21", ...) but is not
    a dollar amount."""
    if not own_box_number:
        return None, "no_own_box_number"

    words = band_words(band)
    start_idx = None
    for i, w in enumerate(words):
        if w["text"].strip(".") == str(own_box_number):
            start_idx = i
            break
    if start_idx is None:
        return None, "own_box_number_token_not_found"

    end_idx = len(words)
    for i in range(start_idx + 1, len(words)):
        token = words[i]["text"].strip(".")
        if token in boundary_tokens and token != str(own_box_number):
            end_idx = i
            break

    x0_bound = words[start_idx]["x0"]
    x1_bound = words[end_idx]["x0"] if end_idx < len(words) else None

    # THE SEARCH WINDOW MUST ALWAYS HAVE A RIGHT EDGE.
    #
    # x1_bound above is set ONLY when another known box-number token happens
    # to appear in this label's own band. When it does not, x1_bound was None,
    # and the next-line filter below read `x1_bound is None` as "no limit"
    # rather than "limit unknown" -- so the window ran to the end of the line,
    # across the column gutter, and then deliberately selected the RIGHTMOST
    # numeric token in that unbounded span. Unbounded window plus
    # rightmost-match reaches as far right as the page allows and takes
    # whatever it finds there.
    #
    # Measured on a real document: 5 of 18 Part III scalars confidently
    # wrong, every one of them via segment_bounded_next_line --
    #     box_5  -> 17,200    (box 17 code B)        truth 434,000
    #     box_8  -> 19        (box 19's label no.)   truth  94,000
    #     box_9b -> 20        (box 20's label no.)   truth 394,600
    #     box_9c -> 2,317,700 (box 20 code A)        truth  95,300
    #     box_10 -> (112,600) (box 20 code B)        truth  16,100
    # The five that were CORRECT simply happened to have a bounding token in
    # their own band. Correctness by coincidence is not correctness.
    #
    # The edge is instead DERIVED from the document: box-number tokens cluster
    # by column, so the left edge of the next column to the right IS the right
    # edge of this one. No fixed coordinate is introduced and the tolerance
    # scales with the observed glyph width, so the rule holds at any font size
    # or page scale.
    edge_source = "band_token"
    if x1_bound is None:
        widths = [(w["x1"] - w["x0"]) / max(len(w["text"]), 1)
                  for w in words if w.get("x1") is not None and w["text"]]
        char_w = sorted(widths)[len(widths) // 2] if widths else 2.0
        tol = max(4.0 * char_w, 8.0)
        xs = []
        for b in (row_bands or [band]):
            for w in b["words"]:
                if w["text"].strip(".") in boundary_tokens \
                        and w["x0"] > x0_bound + tol:
                    xs.append(w["x0"])
        if xs:
            x1_bound = min(xs)
            edge_source = "derived_next_column"
        else:
            # No further column exists to the right of this one. That is
            # POSITIVE evidence the column extends to the region edge, not
            # missing evidence -- recorded either way so the distinction
            # stays auditable in the emitted method string.
            edge_source = "region_edge"

    # Exclude the label phrase's OWN words from the same-line numeric
    # search. Confirmed empirically: box_12's label is "Section 179
    # deduction" -- "179" is a section-reference number embedded INSIDE
    # the label text itself, not a value, and NUMERIC_RE matches it just
    # like a real dollar amount. The earlier "prefer next-line" fix only
    # masked this on documents where a genuine next-line value exists
    # (e.g. Copperleaf's "75,000"); on a blank form with no next-line
    # value, execution falls through to same-line and incorrectly reads
    # "179" as $179. Skip past every word that is part of the label
    # phrase itself (label_word_count words after the box-number token),
    # so only text AFTER the label -- where a genuine trailing same-line
    # value would legitimately appear -- is eligible.
    label_word_count = len(label.split()) if label else 0
    same_line_start = start_idx + 1 + label_word_count
    segment = words[same_line_start:end_idx]
    numeric_words = [w for w in segment if NUMERIC_RE.match(w["text"])]
    # Prefer the NEXT LINE value first. Same-line numeric tokens are
    # frequently embedded IRS section-reference numbers inside the label
    # itself (e.g. "Section 179 deduction" -- "179" is not a dollar
    # amount), which this form's two-line cell layout makes structurally
    # ambiguous to distinguish from a genuine same-line trailing value.
    # Confirmed empirically: box_12's real value ("75,000") sits on the
    # next line down; "179" on the label line is a section-number, not
    # a value, and was being matched first before this reordering.
    if row_bands is not None:
        label_top = band["top"]
        candidate_bands = [
            b for b in row_bands
            if 2.0 <= (b["top"] - label_top) <= (value_line_offset + 6.0)
        ]
        for cb in sorted(candidate_bands, key=lambda b: b["top"]):
            cb_words = band_words(cb)
            in_segment = [
                w for w in cb_words
                if w["x0"] >= x0_bound - 5.0 and (x1_bound is None or w["x0"] < x1_bound - 5.0)
            ]
            cb_numeric = [w for w in in_segment if NUMERIC_RE.match(w["text"])]
            if cb_numeric:
                last = cb_numeric[-1]
                val = parse_numeric(last["text"])
                return (last["text"], val), \
                    "segment_bounded_next_line(edge=%s)" % edge_source

    if numeric_words:
        last = numeric_words[-1]
        val = parse_numeric(last["text"])
        return (last["text"], val), \
            "segment_bounded_same_line(edge=%s)" % edge_source

    return None, "segment_bounded_no_value"


def locate_column_x(row_bands, columns_def, near_top, window=25):
    """Derive column x-positions from header anchor words near a hint
    y-position. Elastic: no fixed x is ever declared in the grammar."""
    col_x = {}
    for band in row_bands:
        if abs(band["top"] - near_top) > window:
            continue
        for col in columns_def:
            anchor_n = _norm(col["anchor"])
            for w in band["words"]:
                if _norm(w["text"]) == anchor_n:
                    col_x.setdefault(col["id"], []).append(w["x0"])
    return {cid: sum(xs) / len(xs) for cid, xs in col_x.items()}


def assign_to_nearest_column(band, col_x, exclude_leading_token=None, boundary_tokens=None):
    """Bucket numeric tokens in a band by nearest column x-position.

    boundary_tokens: if given, truncate the word list at the first word
    whose stripped text matches a known OTHER box's number token. This
    guards against the same cross-column band-merging defect confirmed
    empirically in scalar_cell: Part II/III's dense grid frequently places
    two unrelated rows in one y-band (e.g. 'Loss 100.000000 % 100.000000 %
    11 Other income (loss)' -- the trailing '11' is box_11's printed
    number, not an Item J value; 'Nonrecourse . . $ 39,700 $ 51,600 13
    Other deductions' -- '13' is box_13's number). Without truncation,
    the box number itself can be misread as a trailing numeric value.
    Known limitation: a legitimate dollar value that happens to equal a
    boundary token string (e.g. an amount of exactly $13) would be
    truncated too. Acceptable for this build stage; flagged, not silently
    accepted as risk-free."""
    words = band_words(band)
    if boundary_tokens:
        kept = []
        for w in words:
            if w["text"].strip(".") in boundary_tokens:
                break
            kept.append(w)
        words = kept
    numeric_words = [w for w in words if NUMERIC_RE.match(w["text"])]
    if exclude_leading_token and words and numeric_words:
        first_norm = words[0]["text"].strip(".").lower()
        if first_norm == str(exclude_leading_token).lower() and numeric_words[0] is words[0]:
            numeric_words = numeric_words[1:]
    result = {}
    for w in numeric_words:
        if not col_x:
            continue
        best_col = min(col_x.items(), key=lambda kv: abs(kv[1] - w["x0"]))[0]
        val = parse_numeric(w["text"])
        result.setdefault(best_col, []).append((w["text"], val))
    return result


# ---------------------------------------------------------------------------
# Evidence envelope
# ---------------------------------------------------------------------------

def envelope(semantic_id, status, raw_text=None, normalized_value=None,
             bbox=None, method="unknown", rule_id=None, confidence=None,
             grammar_id="k1-1065-2025", grammar_version="k1-face-grammar/1.0",
             notes=None):
    return {
        "semantic_id": semantic_id,
        "raw_text": raw_text,
        "normalized_value": normalized_value,
        "status": status,
        "page": 1,
        "bbox": bbox,
        "coordinate_frame": "pdf_top_left_origin_points",
        "method": method,
        "rule_id": rule_id,
        "confidence": confidence,
        "grammar_id": grammar_id,
        "grammar_version": grammar_version,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_independent_checkbox(field_key, field_def, ctx):
    anchors = field_def.get("anchors", {})
    region = field_def.get("region")
    frame, disposition = locate_checkbox_frame(ctx, region, anchors)
    sem = field_def.get("semantic_id")

    if frame is None:
        return envelope(sem, "unresolved", method="checkbox_frame_%s" % disposition,
                         rule_id=field_key,
                         notes="Frame could not be uniquely located (%s)." % disposition)

    result = evaluate_checkbox(ctx["page"], frame)
    bbox = [frame["x0"], frame["top"], frame["x1"], frame["bottom"]]
    status_map = {"present": "present", "verified_absent": "verified_absent", "unresolved": "unresolved"}
    status = status_map.get(result["status"], "unresolved")
    return envelope(sem, status, raw_text=result.get("method"),
                     normalized_value=result.get("value"), bbox=bbox,
                     method="%s+%s" % (disposition, result["method"]), rule_id=field_key)


def read_exclusive_choice_pair(field_key, field_def, ctx):
    anchors = field_def.get("anchors", {})
    region = field_def.get("region")
    sem = field_def.get("semantic_id")
    options = field_def.get("options", [])
    left_a = dict(anchors.get("option_left", {}))
    right_a = dict(anchors.get("option_right", {}))
    if anchors.get("frame_row_top_hint") is not None:
        left_a.setdefault("frame_row_top_hint", anchors["frame_row_top_hint"])
        right_a.setdefault("frame_row_top_hint", anchors["frame_row_top_hint"])

    left_frame, left_disp = locate_checkbox_frame(ctx, region, left_a)
    right_frame, right_disp = locate_checkbox_frame(ctx, region, right_a)

    left_result = evaluate_checkbox(ctx["page"], left_frame) if left_frame else {"status": "unresolved", "method": "frame_%s" % left_disp}
    right_result = evaluate_checkbox(ctx["page"], right_frame) if right_frame else {"status": "unresolved", "method": "frame_%s" % right_disp}

    left_checked = left_result["status"] == "present"
    right_checked = right_result["status"] == "present"

    bbox = None
    if left_frame and right_frame:
        bbox = [min(left_frame["x0"], right_frame["x0"]), min(left_frame["top"], right_frame["top"]),
                max(left_frame["x1"], right_frame["x1"]), max(left_frame["bottom"], right_frame["bottom"])]

    if left_result["status"] == "unresolved" and right_result["status"] == "unresolved":
        return envelope(sem, "unresolved", method="both_frames_unresolved", rule_id=field_key, bbox=bbox,
                         notes="left=%s right=%s" % (left_disp, right_disp))
    if left_checked and right_checked:
        return envelope(sem, "ambiguous", method="both_options_checked", rule_id=field_key, bbox=bbox,
                         notes="Both %s and %s appear checked -- requires human review." % (options[0], options[1]))
    if left_checked:
        return envelope(sem, "present", normalized_value=options[0], method="interior_vector", rule_id=field_key, bbox=bbox)
    if right_checked:
        return envelope(sem, "present", normalized_value=options[1], method="interior_vector", rule_id=field_key, bbox=bbox)
    return envelope(sem, "blank", normalized_value=None, method="verified_neither_checked", rule_id=field_key, bbox=bbox,
                     notes="Both boxes located and verified unchecked -- neither option selected.")


def read_multi_checkbox_set(field_key, field_def, ctx):
    anchors = field_def.get("anchors", {})
    region = field_def.get("region")
    sem = field_def.get("semantic_id")
    top_hint = anchors.get("frame_row_top_hint")
    results = {}
    any_resolved = False
    bboxes = []
    for opt in anchors.get("options", []):
        opt_anchors = dict(opt)
        if top_hint is not None:
            opt_anchors.setdefault("frame_row_top_hint", top_hint)
        frame, disp = locate_checkbox_frame(ctx, region, opt_anchors)
        if frame is None:
            results[opt["id"]] = {"status": "unresolved", "method": "frame_%s" % disp}
            continue
        r = evaluate_checkbox(ctx["page"], frame)
        results[opt["id"]] = {"status": r["status"], "value": r.get("value")}
        bboxes.append([frame["x0"], frame["top"], frame["x1"], frame["bottom"]])
        any_resolved = True

    bbox = None
    if bboxes:
        bbox = [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                max(b[2] for b in bboxes), max(b[3] for b in bboxes)]

    status = "present" if any(v["status"] == "present" for v in results.values()) else \
             ("blank" if any_resolved else "unresolved")
    return envelope(sem, status, normalized_value=results, method="multi_checkbox_set",
                     rule_id=field_key, bbox=bbox,
                     notes="taxonomy_gap" if field_def.get("taxonomy_gap") else None)


def read_header_period(field_key, field_def, ctx):
    """Empirically corrected (this wave): the real beginning/ending fiscal-
    year row is NOT near the "calendar year"/"tax year" text (that band
    has no fill-in slots at all). It is a separate row reading
    "beginning [M] / [D] / [Y] ending [M] / [D] / [Y]", confirmed at
    top~97-100 on both the IRS blank and Copperleaf.

    Disambiguating "filled" from "boilerplate": the IRS blank shows a bare
    "2025" after "beginning" with NO month/day digits before it (a static
    year print, not a fill-in), and zero digit tokens at all after
    "ending". Copperleaf shows full "1/1/2025" and "12/31/2025". Rule,
    evidence-based: fewer than 2 digit tokens in a segment == no real
    date was filled (blank); 2+ digit tokens == a genuine month/day/year
    fill worth parsing."""
    anchors = field_def.get("anchors", {})
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    column_header = anchors.get("column_header", "")
    bounds = ctx["region_bounds"].get(region, {})
    boundary_tokens = ctx.get("box_boundary_tokens", set())

    band = None
    for b in ctx["row_bands"]:
        if bounds.get("top") is not None and b["top"] < bounds["top"] - 2:
            continue
        if bounds.get("bottom") is not None and b["top"] > bounds["bottom"] + 2:
            continue
        tn = _norm(b["text"])
        if re.search(r"(?<!\w)beginning(?!\w)", tn) and re.search(r"(?<!\w)ending(?!\w)", tn):
            band = b
            break
    if band is None:
        return envelope(sem, "unresolved", method="beginning_ending_row_not_found", rule_id=field_key,
                         notes="Could not locate a row containing both 'beginning' and 'ending' tokens in region '%s'." % region)

    words = band_words(band)
    lowered = [w["text"].strip(".,").lower() for w in words]
    if "beginning" not in lowered or "ending" not in lowered:
        return envelope(sem, "unresolved", method="beginning_or_ending_token_missing", rule_id=field_key)
    begin_idx = lowered.index("beginning")
    end_idx = lowered.index("ending")

    begin_segment = words[begin_idx + 1:end_idx]
    # NOTE: cannot use boundary_tokens here -- box numbers like "12" are
    # indistinguishable from date digits like day-of-month "12" (confirmed
    # empirically: Copperleaf's "ending 12 / 31 / 2025" broke on box_12's
    # boundary token before collecting any date digits). This form's date
    # fields render as a fixed 5-token pattern (digit / digit / digit),
    # so bound by a fixed token count instead of a text-based boundary.
    end_segment = words[end_idx + 1:end_idx + 6]

    def parse_date_segment(segment):
        digit_tokens = [w["text"] for w in segment if w["text"].isdigit()]
        if len(digit_tokens) < 2:
            return None
        parts = digit_tokens[-3:] if len(digit_tokens) >= 3 else digit_tokens
        return "/".join(parts)

    bbox = [band["x0"], band["top"], band["x1"], band["bottom"]]
    col_norm = _norm(column_header)
    segment = begin_segment if col_norm == "beginning" else end_segment
    raw_tokens = " ".join(w["text"] for w in segment) or None
    date_str = parse_date_segment(segment)

    if date_str is None:
        return envelope(sem, "blank", raw_text=raw_tokens, method="beginning_ending_row_located_no_fill",
                         rule_id=field_key, bbox=bbox,
                         notes="Calendar-year filer (fiscal-year beginning/ending fields blank) -- legitimate observation.")
    return envelope(sem, "present", raw_text=date_str, normalized_value=date_str,
                     method="beginning_ending_row_parsed", rule_id=field_key, bbox=bbox)


def read_scalar_cell(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    label = field_def.get("anchors", {}).get("label")
    box_num = field_def.get("box_number_2025")
    bounds = ctx["region_bounds"].get(region, {})

    if not label:
        return envelope(sem, "unresolved", method="no_label_anchor_in_grammar", rule_id=field_key,
                         notes="Grammar field has no anchors.label -- cannot locate deterministically.")

    band = find_band_by_anchor(ctx["row_bands"], label, floor=bounds.get("top"), ceiling=bounds.get("bottom"))
    if band is None:
        return envelope(sem, "unresolved", method="label_not_found", rule_id=field_key,
                         notes="Label %r not found in region '%s'." % (label, region))

    bbox = [band["x0"], band["top"], band["x1"], band["bottom"]]
    boundary_tokens = ctx.get("box_boundary_tokens", set())
    numeric, seg_method = extract_numeric_bounded(band, box_num, boundary_tokens,
                                                    label=label, row_bands=ctx["row_bands"])
    if numeric is None:
        return envelope(sem, "blank", raw_text=band["text"],
                         method="row_located_no_numeric_token(%s)" % seg_method,
                         rule_id=field_key, bbox=bbox)
    raw, val = numeric
    return envelope(sem, "present", raw_text=raw, normalized_value=val,
                     method="row_label_anchor(%s)" % seg_method, rule_id=field_key, bbox=bbox)

def extract_ledger_value(band, row_bands, window=12.0):
    """Return (raw_text, value) for a ledger row's dollar value. Checks the
    row's own band first (covers the common single-line case), then falls
    back to the NEAREST neighboring band within a small y-window on EITHER
    side (covers cases where PDF geometry displaces the value onto an
    adjacent band). Confirmed empirically: Copperleaf's 'Ending capital
    account' value (7,460,200 -- independently verified via the grammar's
    own item_l_continuity constraint: 113000+168000+7206200-27000=7460200)
    sits 5.31pt ABOVE its own label band, not below, almost certainly due
    to a rotated 'For IRS Use Only' sidebar column ('esU'/'ylnO' reversed
    fragments observed nearby) perturbing row-band y-clustering. A
    below-only search would miss this; NUMERIC_RE naturally excludes the
    non-numeric sidebar fragments from being misread as values.

    Window tightened to 8.0pt (from an initial 12.0pt) after empirical
    correction: the genuine displaced-value case above sits 5.31pt from
    its label, but a 12.0pt window also reached the row ABOVE this one
    (Current year net income, 11.62pt away) when the current row was
    legitimately blank -- fabricating a copied value from the wrong row.
    8.0pt admits the confirmed real case while excluding the confirmed
    false one. Not proven safe for all possible row spacings; a future
    corpus with tighter row spacing may need per-form-year tuning."""
    same_band = extract_last_numeric(band)
    if same_band is not None:
        return same_band
    candidates = [b for b in row_bands if b is not band and abs(b["top"] - band["top"]) <= 8.0]
    candidates.sort(key=lambda b: abs(b["top"] - band["top"]))
    for cb in candidates:
        result = extract_last_numeric(cb)
        if result is not None:
            return result
    return None


def read_two_column_grid(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    anchors = field_def.get("anchors", {})
    bounds = ctx["region_bounds"].get(region, {})
    columns_def = anchors.get("columns", [])
    row_hdr_hint = anchors.get("row_header_top_hint")

    col_x = locate_column_x(ctx["row_bands"], columns_def, row_hdr_hint) if row_hdr_hint else {}

    rows_out = {}
    any_row_found = False
    bboxes = []
    for row in anchors.get("rows", []):
        band = find_band_by_anchor(ctx["row_bands"], row["anchor"], floor=bounds.get("top"),
                                    ceiling=bounds.get("bottom"), top_hint=row.get("row_top_hint"))
        if band is None:
            rows_out[row["id"]] = {"status": "unresolved", "method": "row_anchor_not_found"}
            continue
        any_row_found = True
        bboxes.append([band["x0"], band["top"], band["x1"], band["bottom"]])
        assigned = assign_to_nearest_column(band, col_x, boundary_tokens=ctx.get("box_boundary_tokens"))
        row_result = {}
        for col in columns_def:
            vals = assigned.get(col["id"])
            if not vals:
                row_result[col["id"]] = {"status": "blank", "value": None}
            else:
                raw, val = vals[-1]
                row_result[col["id"]] = {"status": "present", "value": val, "raw_text": raw}
        rows_out[row["id"]] = row_result

    bbox = None
    if bboxes:
        bbox = [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                max(b[2] for b in bboxes), max(b[3] for b in bboxes)]

    status = "present" if any_row_found else "unresolved"
    return envelope(sem, status, normalized_value=rows_out, method="two_column_grid",
                     rule_id=field_key, bbox=bbox,
                     notes=None if col_x else "Column header x-positions not resolved -- values unassigned.")


def read_ledger(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    anchors = field_def.get("anchors", {})
    bounds = ctx["region_bounds"].get(region, {})
    row_hdr_hint = anchors.get("row_header_top_hint")

    rows_out = {}
    any_found = False
    bboxes = []
    for row in anchors.get("rows", []):
        anchor_val = row.get("anchors", row.get("anchor"))
        band = find_band_by_anchor(ctx["row_bands"], anchor_val, floor=bounds.get("top"),
                                    ceiling=bounds.get("bottom"), top_hint=row_hdr_hint)
        if band is None:
            rows_out[row["id"]] = {"status": "unresolved", "method": "row_anchor_not_found"}
            continue
        any_found = True
        bboxes.append([band["x0"], band["top"], band["x1"], band["bottom"]])
        numeric = extract_ledger_value(band, ctx["row_bands"])
        if numeric is None:
            rows_out[row["id"]] = {"status": "blank", "value": None}
        else:
            raw, val = numeric
            rows_out[row["id"]] = {"status": "present", "value": val, "raw_text": raw}

    bbox = None
    if bboxes:
        bbox = [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                max(b[2] for b in bboxes), max(b[3] for b in bboxes)]
    status = "present" if any_found else "unresolved"
    return envelope(sem, status, normalized_value=rows_out, method="ledger", rule_id=field_key, bbox=bbox)


def read_range_pair(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    anchors = field_def.get("anchors", {})
    bounds = ctx["region_bounds"].get(region, {})
    row_label = anchors.get("row_label")
    columns_def = anchors.get("columns", [])

    band = find_band_by_anchor(ctx["row_bands"], row_label, floor=bounds.get("top"), ceiling=bounds.get("bottom"))
    if band is None:
        return envelope(sem, "unresolved", method="row_label_not_found", rule_id=field_key)

    bbox = [band["x0"], band["top"], band["x1"], band["bottom"]]
    col_x = locate_column_x(ctx["row_bands"], columns_def, band["top"], window=15)
    assigned = assign_to_nearest_column(band, col_x, boundary_tokens=ctx.get("box_boundary_tokens"))
    result = {}
    for col in columns_def:
        vals = assigned.get(col["id"])
        if not vals:
            result[col["id"]] = {"status": "blank", "value": None}
        else:
            raw, val = vals[-1]
            result[col["id"]] = {"status": "present", "value": val, "raw_text": raw}
    return envelope(sem, "present", normalized_value=result, method="range_pair", rule_id=field_key, bbox=bbox)


def read_not_implemented(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    reader = field_def.get("reader")
    return envelope(sem, "unresolved", method="reader_not_implemented", rule_id=field_key,
                     notes="Reader '%s' not yet implemented (planned for a subsequent wave)." % reader)


READERS = {
    "independent_checkbox": read_independent_checkbox,
    "exclusive_choice_pair": read_exclusive_choice_pair,
    "multi_checkbox_set": read_multi_checkbox_set,
    "header_period": read_header_period,
    "scalar_cell": read_scalar_cell,
    "two_column_grid": read_two_column_grid,
    "ledger": read_ledger,
    "range_pair": read_range_pair,
}


def read_attachment_reference(field_key, field_def, ctx):
    """Read a checkbox that gates an attachment reference (e.g., Box 16 → K-3).
    Delegates to the same checkbox primitives as independent_checkbox, then
    maps the result to a checked boolean for assembler consumption."""
    anchors = field_def.get("anchors", {})
    region = field_def.get("region")
    frame, disposition = locate_checkbox_frame(ctx, region, anchors)
    sem = field_def.get("semantic_id")
    target_form = field_def.get("target_form")

    if frame is None:
        return envelope(sem, "unresolved", method="checkbox_frame_%s" % disposition,
                        rule_id=field_key,
                        notes="Attachment reference frame could not be located (%s)." % disposition)

    result = evaluate_checkbox(ctx["page"], frame)
    bbox = [frame["x0"], frame["top"], frame["x1"], frame["bottom"]]
    status_map = {"present": "present", "verified_absent": "verified_absent", "unresolved": "unresolved"}
    status = status_map.get(result["status"], "unresolved")
    checked = True if status == "present" else (False if status == "verified_absent" else None)

    return envelope(sem, status, raw_text=result.get("method"),
                    normalized_value=checked,
                    method="attachment_reference+%s" % result.get("method", "unknown"),
                    rule_id=field_key, bbox=bbox,
                    notes="Box 16 checkbox: checked=%s target_form=%s" % (checked, target_form))

READERS["attachment_reference"] = read_attachment_reference



# ---------------------------------------------------------------------------
# Overlay-only text, identity, conditional-record, and coded-row readers
# ---------------------------------------------------------------------------


def _field_anchor_phrase(field_def):
    anchors = field_def.get("anchors") or {}
    direct = (
        anchors.get("label")
        or anchors.get("governing_label")
        or anchors.get("governing_checkbox_label")
    )
    if direct:
        return direct

    for option_name in ("option_left", "option_right"):
        option = anchors.get(option_name)
        if isinstance(option, dict) and option.get("governing_label"):
            return option["governing_label"]

    return None


def _anchor_span(label, region, ctx):
    """Find the smallest defensible printed anchor span."""
    if not label:
        return None

    bounds = ctx["region_bounds"].get(region, {})
    lower = bounds.get("top", 0.0) - ctx["row_tol"]
    upper = bounds.get("bottom", ctx["page"].height) + ctx["row_tol"]
    bands = sorted(
        [
            band for band in ctx["row_bands"]
            if lower <= band["top"] <= upper
        ],
        key=lambda band: band["top"],
    )
    target = _norm(label)
    target_words = target.split()
    target_tokens = set(target_words)
    exact_candidates = []
    fuzzy_candidates = []

    for index in range(len(bands)):
        for width in range(1, min(4, len(bands) - index) + 1):
            window = bands[index:index + width]
            sample = _norm(" ".join(
                band.get("text", "") for band in window
            ))
            span = {
                "top": min(band["top"] for band in window),
                "bottom": max(band["bottom"] for band in window),
            }
            height = span["bottom"] - span["top"]
            extra_words = max(0, len(sample.split()) - len(target_words))

            if target and target in sample:
                exact_candidates.append(
                    ((extra_words, height, width, span["top"]), span)
                )
                continue

            sample_tokens = set(sample.split())
            overlap = (
                len(target_tokens & sample_tokens) / float(len(target_tokens))
                if target_tokens else 0.0
            )
            if overlap >= 0.80:
                fuzzy_candidates.append(
                    (
                        (-overlap, extra_words, height, width, span["top"]),
                        span,
                    )
                )

    if exact_candidates:
        return min(exact_candidates, key=lambda candidate: candidate[0])[1]
    if fuzzy_candidates:
        return min(fuzzy_candidates, key=lambda candidate: candidate[0])[1]
    return None


def _next_anchor_field(field_key, field_def, ctx):
    fields = ctx["grammar_fields"]
    order = ctx["field_order"]
    anchors = field_def.get("anchors") or {}
    explicit = anchors.get("end_anchor_field")

    if explicit:
        target = fields.get(explicit)
        return explicit, target if isinstance(target, dict) else None

    try:
        start = order.index(field_key)
    except ValueError:
        return None, None

    region = field_def.get("region")
    for candidate_key in order[start + 1:]:
        candidate = fields[candidate_key]
        if candidate.get("region") != region:
            continue
        if _field_anchor_phrase(candidate):
            return candidate_key, candidate
    return None, None


def _value_window(field_key, field_def, ctx):
    region = field_def.get("region")
    label = (field_def.get("anchors") or {}).get("label")
    start = _anchor_span(label, region, ctx)
    if not start:
        return None, "label_anchor_not_found"

    next_key, next_field = _next_anchor_field(field_key, field_def, ctx)
    if next_field:
        end = _anchor_span(
            _field_anchor_phrase(next_field), region, ctx
        )
        if not end:
            hint = (next_field.get("anchors") or {}).get(
                "frame_row_top_hint"
            )
            if hint is not None:
                end = {"top": float(hint), "bottom": float(hint)}
        if not end:
            return None, "end_anchor_not_found:%s" % next_key
        end_top = end["top"]
    else:
        end_top = ctx["region_bounds"].get(region, {}).get(
            "bottom", ctx["page"].height
        )

    # Filled overlay text may share the printed label row. Because these
    # readers accept data-font words only, widening to the row top cannot turn
    # template labels into extracted values.
    window_top = max(
        ctx["region_bounds"].get(region, {}).get("top", 0.0),
        start["top"] - ctx["row_tol"],
    )
    if end_top <= window_top:
        return None, "nonpositive_value_window"
    return (window_top, end_top), None


def _lane_x_bounds(region, ctx):
    lane = ctx["region_lanes"].get(region, "any")
    width = float(ctx["page"].width)
    midpoint = width / 2.0
    if lane == "left":
        return 0.0, midpoint
    if lane == "right":
        return midpoint, width
    return 0.0, width


def _overlay_words_in_window(region, top, bottom, ctx):
    left, right = _lane_x_bounds(region, ctx)
    tolerance = ctx["row_tol"]
    return sorted(
        [
            word for word in ctx["data_words"]
            if left <= (word["x0"] + word["x1"]) / 2.0 <= right
            and word["top"] >= top - tolerance * 0.5
            and word["top"] < bottom + tolerance * 1.5
        ],
        key=lambda word: (word["top"], word["x0"]),
    )


def _group_overlay_lines(words, tolerance):
    lines = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not lines or abs(word["top"] - lines[-1]["top"]) > tolerance:
            lines.append({"top": word["top"], "words": [word]})
        else:
            lines[-1]["words"].append(word)

    for line in lines:
        line["words"].sort(key=lambda item: item["x0"])
    return lines


def _words_bbox(words):
    if not words:
        return None
    return [
        min(word["x0"] for word in words),
        min(word["top"] for word in words),
        max(word["x1"] for word in words),
        max(word["bottom"] for word in words),
    ]


def _line_text(line):
    return " ".join(word["text"] for word in line["words"]).strip()


def _read_overlay_text(field_key, field_def, ctx, mode):
    sem = field_def.get("semantic_id")
    region = field_def.get("region")
    window, error = _value_window(field_key, field_def, ctx)
    if error:
        return envelope(
            sem, "unresolved", method=error, rule_id=field_key,
            notes="Could not derive a bounded overlay-text window from grammar anchors."
        )

    top, bottom = window
    words = _overlay_words_in_window(region, top, bottom, ctx)
    left, right = _lane_x_bounds(region, ctx)
    window_bbox = [left, top, right, bottom]

    if not words:
        return envelope(
            sem, "blank", normalized_value=None,
            method="overlay_font_window_verified_empty",
            rule_id=field_key, bbox=window_bbox,
            notes="The printed field window was located and contains no data-font words."
        )

    lines = _group_overlay_lines(words, ctx["row_tol"])
    line_texts = [_line_text(line) for line in lines if _line_text(line)]
    raw_text = "\n".join(line_texts)
    normalized = raw_text if mode == "address_block" else " ".join(line_texts)
    alias_applied = False
    value_aliases = field_def.get("value_aliases") or {}
    for printed, canonical in value_aliases.items():
        if _norm(printed) == _norm(normalized):
            normalized = canonical
            alias_applied = True
            break

    if mode == "bounded_identity" and not re.search(r"\d", normalized):
        return envelope(
            sem, "unresolved", raw_text=raw_text, normalized_value=None,
            method="overlay_identity_not_recognized", rule_id=field_key,
            bbox=_words_bbox(words),
            notes="Overlay text was present, but it did not contain identifier digits."
        )

    method = "overlay_font_%s" % mode
    notes = "Value derived only from data-font words inside grammar-bounded anchors."
    if alias_applied:
        method += "+value_alias"
        notes += " Printed text was normalized through grammar-declared value_aliases."

    return envelope(
        sem, "present", raw_text=raw_text, normalized_value=normalized,
        method=method, rule_id=field_key,
        bbox=_words_bbox(words),
        notes=notes
    )


def read_bounded_identity(field_key, field_def, ctx):
    return _read_overlay_text(field_key, field_def, ctx, "bounded_identity")


def read_address_block(field_key, field_def, ctx):
    return _read_overlay_text(field_key, field_def, ctx, "address_block")


def read_bounded_text(field_key, field_def, ctx):
    return _read_overlay_text(field_key, field_def, ctx, "bounded_text")


def read_conditional_record(field_key, field_def, ctx):
    anchors = field_def.get("anchors") or {}
    sem = field_def.get("semantic_id")
    checkbox_def = dict(field_def)
    checkbox_def["anchors"] = {
        "governing_label": anchors.get("governing_checkbox_label"),
        "frame_row_top_hint": anchors.get("frame_row_top_hint"),
        "label_association": anchors.get("label_association", "nearest_right"),
    }
    probe = read_independent_checkbox(field_key, checkbox_def, ctx)

    if probe["status"] == "unresolved":
        return envelope(
            sem, "unresolved", raw_text=probe.get("raw_text"),
            normalized_value=None,
            method="conditional_record+%s" % probe.get("method", "unresolved"),
            rule_id=field_key, bbox=probe.get("bbox"),
            notes=probe.get("notes"),
        )

    if probe["status"] == "verified_absent":
        return envelope(
            sem, "blank", raw_text=probe.get("raw_text"),
            normalized_value=None,
            method="conditional_record_governing_checkbox_unchecked",
            rule_id=field_key, bbox=probe.get("bbox"),
            notes="Governing checkbox was located and verified unchecked."
        )

    next_key, next_field = _next_anchor_field(field_key, field_def, ctx)
    end = (
        _anchor_span(_field_anchor_phrase(next_field), field_def.get("region"), ctx)
        if next_field else None
    )
    start_top = (
        probe["bbox"][3] if probe.get("bbox")
        else anchors.get("frame_row_top_hint", 0.0)
    )
    end_top = (
        end["top"] if end
        else ctx["region_bounds"].get(field_def.get("region"), {}).get(
            "bottom", ctx["page"].height
        )
    )
    words = _overlay_words_in_window(
        field_def.get("region"), start_top, end_top, ctx
    )
    lines = _group_overlay_lines(words, ctx["row_tol"])
    line_texts = [_line_text(line) for line in lines if _line_text(line)]

    if not line_texts:
        return envelope(
            sem, "unresolved", normalized_value={"checked": True},
            method="conditional_record_checked_missing_dependents",
            rule_id=field_key, bbox=probe.get("bbox"),
            notes="Governing checkbox is checked, but no dependent overlay text was found."
        )

    dependent = list(anchors.get("dependent_fields") or [])
    values = {"checked": True}
    identifier_index = next(
        (index for index, text in enumerate(line_texts) if re.search(r"\d", text)),
        None,
    )
    if "tin" in dependent:
        values["tin"] = (
            line_texts[identifier_index] if identifier_index is not None else None
        )
    remaining = [
        text for index, text in enumerate(line_texts)
        if index != identifier_index
    ]
    if "name" in dependent:
        values["name"] = " ".join(remaining) if remaining else None

    missing = [name for name in dependent if not values.get(name)]
    status = "unresolved" if missing else "present"
    method = (
        "conditional_record_overlay_partial"
        if missing else "conditional_record_overlay_complete"
    )
    return envelope(
        sem, status, raw_text="\n".join(line_texts),
        normalized_value=values, method=method, rule_id=field_key,
        bbox=_words_bbox(words) or probe.get("bbox"),
        notes=(
            "Missing checked dependent field(s): %s" % ", ".join(missing)
            if missing else
            "Checked record and dependent values derived from overlay-font evidence."
        ),
    )


def _template_box_anchor(box_number, ctx):
    region = ctx["region_bounds"].get("part_iii", {})
    midpoint = float(ctx["page"].width) / 2.0
    structural_starts = (midpoint, float(ctx["page"].width) * 0.73)
    candidates = [
        word for word in ctx["template_words"]
        if str(word.get("text", "")).strip() == str(box_number)
        and (word["x0"] + word["x1"]) / 2.0 >= midpoint
        and word["top"] >= region.get("top", 0.0) - ctx["row_tol"]
        and word["top"] <= region.get("bottom", ctx["page"].height)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda word: min(abs(word["x0"] - start) for start in structural_starts),
    )


def _coded_box_bounds(field_def, ctx):
    anchor = _template_box_anchor(field_def.get("box_number_2025"), ctx)
    if not anchor:
        return None

    width = float(ctx["page"].width)
    midpoint = width / 2.0
    split = width * 0.73
    is_left_subcolumn = anchor["x0"] < split
    column_left = midpoint if is_left_subcolumn else split
    column_right = split if is_left_subcolumn else width

    following = []
    seen = set()
    for candidate in ctx["grammar_fields"].values():
        box_number = candidate.get("box_number_2025")
        if not box_number or str(box_number) in seen:
            continue
        seen.add(str(box_number))
        candidate_anchor = _template_box_anchor(box_number, ctx)
        if not candidate_anchor:
            continue
        same_column = (candidate_anchor["x0"] < split) == is_left_subcolumn
        if same_column and candidate_anchor["top"] > anchor["top"] + 0.5:
            following.append(candidate_anchor["top"])

    bottom = (
        min(following) if following
        else ctx["region_bounds"].get("part_iii", {}).get(
            "bottom", ctx["page"].height
        )
    )
    return {
        "anchor": anchor,
        "left": column_left,
        "right": column_right,
        "top": anchor["top"],
        "bottom": bottom,
    }


def _parse_overlay_number(text):
    value = str(text).strip().replace("$", "").replace(",", "")
    if not value or value.upper() == "STMT":
        return None
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1]
    try:
        number = float(value)
    except ValueError:
        return None
    return -number if negative else number


def read_coded_rows(field_key, field_def, ctx):
    sem = field_def.get("semantic_id")
    bounds = _coded_box_bounds(field_def, ctx)
    if not bounds:
        return envelope(
            sem, "unresolved", method="coded_box_anchor_not_found",
            rule_id=field_key,
            notes="The printed box number could not be located in Part III."
        )

    words = sorted(
        [
            word for word in ctx["data_words"]
            if bounds["left"] <= (word["x0"] + word["x1"]) / 2.0 <= bounds["right"]
            and word["top"] >= bounds["top"] - ctx["row_tol"] * 0.5
            and word["top"] < bounds["bottom"] - 0.1
        ],
        key=lambda word: (word["top"], word["x0"]),
    )
    box_bbox = [
        bounds["left"], bounds["top"], bounds["right"], bounds["bottom"]
    ]

    if not words:
        return envelope(
            sem, "blank", normalized_value=[],
            method="overlay_font_coded_rows_verified_empty",
            rule_id=field_key, bbox=box_bbox,
            notes="Printed coded-row box was located and contains no data-font words."
        )

    entries = []
    unparsed = []
    for line in _group_overlay_lines(words, ctx["row_tol"]):
        line_words = line["words"]
        tokens = [word["text"].strip() for word in line_words]
        code = next(
            (
                token.upper() for token in tokens
                if re.fullmatch(r"(?:[A-Za-z]{1,2}|\*)", token)
                and token.upper() != "STMT"
            ),
            None,
        )
        statement_reference = any(
            token.upper() == "STMT" for token in tokens
        )
        amount = next(
            (
                parsed for parsed in
                (_parse_overlay_number(token) for token in reversed(tokens))
                if parsed is not None
            ),
            None,
        )

        if code is None and not statement_reference:
            unparsed.append(_line_text(line))
            continue

        entries.append({
            "code": code,
            "value": amount,
            "statement_reference": statement_reference,
            "raw_text": _line_text(line),
            "bbox": _words_bbox(line_words),
        })

    if not entries:
        return envelope(
            sem, "unresolved",
            raw_text="\n".join(_line_text(line) for line in _group_overlay_lines(
                words, ctx["row_tol"]
            )),
            normalized_value=None,
            method="coded_rows_overlay_unparsed", rule_id=field_key,
            bbox=_words_bbox(words),
            notes="Overlay words were present in the coded box, but no code/value row was recognized."
        )

    return envelope(
        sem, "present",
        raw_text="\n".join(entry["raw_text"] for entry in entries),
        normalized_value=entries,
        method="overlay_font_coded_rows", rule_id=field_key,
        bbox=_words_bbox(words),
        notes=(
            "Parsed %d face row(s) from overlay-font evidence%s."
            % (
                len(entries),
                "; ignored non-coded overlay row(s): %s" % " | ".join(unparsed)
                if unparsed else "",
            )
        ),
    )


READERS["bounded_identity"] = read_bounded_identity
READERS["address_block"] = read_address_block
READERS["bounded_text"] = read_bounded_text
READERS["conditional_record"] = read_conditional_record
READERS["coded_rows"] = read_coded_rows
# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def read_not_printed(field_key, field_def, ctx):
    """A field the grammar declares is NOT PRINTED on this form revision.

    This exists because `item_l.basis_method` declared `reader: bounded_text`
    with no label anchor -- structurally unbindable, so it would silently
    return `label_not_found` forever. Detected by grammar_validator.py's
    positive control (defect D16 family, 2026-07-29).

    Absence is VERIFIED, not asserted. The grammar supplies the tokens that
    WOULD appear if the field were printed; this reader confirms none of them
    are present before emitting `missing`. If a future form revision
    reintroduces the field, the probe fires and the read becomes `unresolved`
    with a loud note -- the grammar breaks visibly instead of quietly
    reporting a stale absence as fact.

    Emits:
      missing     -- probe tokens confirmed absent from the page
      unresolved  -- a probe token WAS found (grammar is stale), or no probe
                     tokens were declared (nothing to verify against)
    Never emits a value. `missing` means "this form revision does not carry
    this field", which is distinct from `blank` ("printed but empty") and from
    `unresolved` ("we could not determine").
    """
    anchors = field_def.get("anchors") or {}
    sem = field_def.get("semantic_id")
    tokens = anchors.get("absence_probe_tokens") or []

    if not tokens:
        return envelope(
            sem, "unresolved", method="no_absence_probe_tokens",
            rule_id=field_key,
            notes="Grammar declares this field is not printed but supplied no "
                  "absence_probe_tokens, so the claim cannot be verified. "
                  "Refusing to assert absence without evidence.")

    page_text = " ".join(
        (b.get("text") or "") for b in ctx.get("row_bands", [])).lower()
    found = [t for t in tokens if str(t).strip().lower() in page_text]

    if found:
        return envelope(
            sem, "unresolved", method="absence_probe_contradicted",
            rule_id=field_key,
            notes="Grammar declares this field is not printed on this form "
                  "revision, but probe token(s) %s WERE found on the page. The "
                  "grammar is stale for this document -- do not trust the "
                  "declared absence." % ", ".join(repr(t) for t in found))

    return envelope(
        sem, "missing", normalized_value=None,
        method="verified_absent_from_form_revision", rule_id=field_key,
        notes="Verified: none of the %d declared probe token(s) appear on this "
              "page, confirming the field is not printed on this form "
              "revision. Absence observed, not assumed." % len(tokens))


READERS["not_printed"] = read_not_printed


def read_face(pdf_path, grammar_path):
    with open(grammar_path, "r", encoding="utf-8") as fh:
        grammar = yaml.safe_load(fh)

    # ---- D16: fail-closed grammar shape preflight -------------------------
    # A grammar that declares a correct value at the WRONG NESTING LEVEL is
    # silently ignored by the readers below (historical: box_22/box_23 shipped
    # with correct labels as direct field children instead of under `anchors:`,
    # yielding no_label_no_unique_hint forever). Validate SHAPE before trusting
    # any field.
    #
    # The import is deliberately unguarded. A preflight that can be disabled by
    # deleting a file is a fail-open guard -- the exact defect family this
    # project exists to eliminate. If grammar_validator is missing, the install
    # is broken and extraction must stop loudly.
    import grammar_validator
    # Warnings are PRINTED, not returned-and-dropped. A warning nobody surfaces
    # is not a warning -- it is a silent gap wearing a label.
    _gw = grammar_validator.validate_grammar_or_die(
        grammar, grammar_path, known_readers=READERS.keys())
    if _gw:
        print("GRAMMAR: %d shape warning(s) for %s"
              % (len(_gw), grammar_path), file=sys.stderr, flush=True)
        for _w in _gw:
            print("  WARN: %s" % _w, file=sys.stderr, flush=True)

    pdf = pdfplumber.open(pdf_path)
    page = pdf.pages[0]

    row_tol = compute_row_tolerance(page)
    row_bands = build_row_bands(page, row_tol)
    region_bounds = derive_region_bounds(page, grammar["regions"], row_bands)
    frames = detect_checkbox_frames(page)
    fonts = classify_fonts(page)
    all_words = page.extract_words(extra_attrs=["fontname"])
    data_font_names = set(fonts["data_fonts"])
    data_words = [
        word for word in all_words
        if word.get("fontname") in data_font_names
    ]
    template_words = [
        word for word in all_words
        if word.get("fontname") not in data_font_names
    ]

    box_boundary_tokens = set()
    for fdef in grammar["fields"].values():
        bn = fdef.get("box_number_2025")
        if bn:
            box_boundary_tokens.add(str(bn))

    ctx = {
        "page": page, "row_bands": row_bands, "row_tol": row_tol,
        "region_bounds": region_bounds, "frames": frames,
        "box_boundary_tokens": box_boundary_tokens,
        "data_words": data_words,
        "template_words": template_words,
        "grammar_fields": grammar["fields"],
        "field_order": list(grammar["fields"].keys()),
        "region_lanes": {
            region["id"]: region.get("lane", "any")
            for region in grammar["regions"]
        },
    }

    fields_out = {}
    status_counts = {}
    for field_key, field_def in grammar["fields"].items():
        reader_name = field_def.get("reader")
        reader_fn = READERS.get(reader_name, read_not_implemented)
        try:
            result = reader_fn(field_key, field_def, ctx)
        except Exception as e:
            result = envelope(field_def.get("semantic_id"), "unresolved",
                               method="reader_exception", rule_id=field_key,
                               notes="Exception in reader '%s': %s" % (reader_name, e))
        if field_def.get("taxonomy_gap"):
            result["taxonomy_gap"] = True
        fields_out[field_key] = result
        status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1

    with open(pdf_path, "rb") as fh:
        pdf_sha256 = hashlib.sha256(fh.read()).hexdigest()

    output = {
        "source_pdf": Path(pdf_path).name,
        "source_pdf_sha256": pdf_sha256,
        "extraction_timestamp_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "grammar_id": grammar["form"]["form_name"] + "-" + str(grammar["form"]["tax_year"]),
        "grammar_version": grammar["schema_version"],
        "posture": grammar.get("validation", {}).get("posture", "hard_error"),
        "region_bounds": {k: {kk: vv for kk, vv in v.items() if kk != "anchor_text_matched"} for k, v in region_bounds.items()},
        "checkbox_frame_count": len(frames),
        "font_summary": {"template_font_count": len(fonts["template_fonts"]), "data_font_count": len(fonts["data_fonts"])},
        "status_counts": status_counts,
        "fields": fields_out,
    }

    pdf.close()
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract evidence-enveloped K-1 face fields from one PDF."
    )
    parser.add_argument("pdf", help="Source K-1 PDF")
    parser.add_argument(
        "grammar",
        nargs="?",
        default=str(DEFAULT_GRAMMAR),
        help="Face grammar YAML (default: shipped 2025 K-1 grammar)",
    )
    parser.add_argument(
        "--out",
        help="Write JSON to this path instead of standard output",
    )
    args = parser.parse_args(argv)
    result = read_face(args.pdf, args.grammar)
    payload = json.dumps(result, indent=2, default=str)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8", newline="\n")
        print("WROTE %s" % out_path.resolve())
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
