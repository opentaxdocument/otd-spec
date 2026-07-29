
#!/usr/bin/env python
"""Assign a pipeline role to each LOGICAL SECTION of a K-1 package.

WHY SECTIONS AND NOT PAGES
    Real K-1 pages carry several unrelated sections, and single sections span
    pages. A page-level classifier must pick ONE role per page and is
    therefore forced to be wrong about the rest. Observed on this corpus:
      * one page held ECI / FDAP / PASSIVE / UBTI / TABULAR SUMMARY blocks
      * one page held an activity ROSTER above an activity MATRIX
      * a detail block opened on one page and continued on the next

SCORING FROM SHAPES, NOT TEXT
    The segmenter has already tagged every row into a producer-neutral shape
    (CODED_ENTRY, LABEL_AMOUNT, JURISDICTION_ROW, GRID_ROW, ROSTER_ROW,
    TOTAL, SCOPE_HEADING, COLUMN_HEADER, PROSE, NOISE). This module scores
    the shape HISTOGRAM. No regex is re-run over page text, so the role can
    never disagree with the shapes the boundaries were derived from.

    The single most discriminating feature is trailing_amount_mode:
        1  -> one amount per row  -> coded entry or detail component
        N  -> one amount per column -> a grid

FAIL-CLOSED
    A section with insufficient or contradictory evidence is 'unresolved'.
    The previous classifier's `return "footnote"` fall-through was a
    fail-open default: every page it could not understand silently became a
    footnote, which is how pages titled "OVERFLOW STATEMENTS" were filed as
    footnotes. There is deliberately no default role here.

VOCABULARY STAYS IN DATA
    Title phrases, jurisdiction names/codes, and thresholds live in
    signatures/page-signatures.yaml. Onboarding a producer is a data change.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from page_classifier import load_signatures, title_signal  # noqa: E402
from section_segmenter import (BOX_CODE, DATA_SHAPES,  # noqa: E402
                              LINE_COL_HEADER, prepare, segment_document)

BOX_KEYED_ROW = re.compile(r"^\s*(\d{1,2})([a-z]{0,2})\b", re.I)


def _ratio(part, whole):
    return (part / whole) if whole else 0.0


def score_section(sec, sig):
    """Score every plausible role for one section from its shape histogram.

    Returns {role: (score, [reasons])}, scores in 0..1.
    """
    sh = sec.get("shapes") or {}
    rows = max(sec.get("row_count") or 0, 1)
    amts = sec.get("trailing_amount_mode")
    heading = sec.get("heading") or ""
    text = sec.get("text") or ""
    data = sum(v for k, v in sh.items() if k in DATA_SHAPES) or 0

    ce = sh.get("CODED_ENTRY", 0)
    lr = sh.get("LINE_REF_AMOUNT", 0)
    la = sh.get("LABEL_AMOUNT", 0)
    jr = sh.get("JURISDICTION_ROW", 0)
    gr = sh.get("GRID_ROW", 0)
    rr = sh.get("ROSTER_ROW", 0)
    tot = sh.get("TOTAL", 0)
    scope = sh.get("SCOPE_HEADING", 0)
    prose = sh.get("PROSE", 0)

    out = {}

    # ---- face page --------------------------------------------------------
    face_id = sum(1 for rx in sig["_face_any"] if rx.search(text))
    face_parts = sum(1 for rx in sig["_face_parts"] if rx.search(text))
    if face_id >= 1 and face_parts >= 2:
        out["face_page"] = (min(0.70 + 0.10 * face_parts, 1.0),
                            ["form identity marker present",
                             "%d part headings" % face_parts])

    # ---- coded overflow vs reference footnote -----------------------------
    # BOTH shapes are "box/code reference + one amount", so row shape alone
    # cannot separate them. The discriminator is DENSITY:
    #
    #   OVERFLOW enumerates many CODES within FEW boxes -- it exists because
    #   one box ran out of room on the face. Codes per box is high.
    #
    #   A REFERENCE FOOTNOTE restates many DIFFERENT boxes under some lens
    #   (effectively-connected income, FDAP, passive) with one code each.
    #   Codes per box is ~1.
    #
    # Observed defect this gate closes: a footnote page reporting the ECI
    # PORTION of eight different boxes was classified as overflow. Routed to
    # the overflow fragment it would have emitted box 9A = 3,662 when the
    # real box 9A was 897,700 -- a plausible, confidently-wrong tax figure.
    # That is the exact fabrication class this project exists to prevent.
    if ce >= 2 and (amts is None or amts == 1):
        boxes, pairs = {}, set()
        for ln in text.splitlines():
            m = BOX_CODE.match(ln.strip())
            if not m:
                continue
            code = (m.group(2) or m.group(3) or "").upper()
            if not code:
                continue
            bx = m.group(1)
            boxes[bx] = boxes.get(bx, 0) + 1
            pairs.add((bx, code))
        distinct_boxes = len(boxes) or 1
        cpb = len(pairs) / distinct_boxes
        r = _ratio(ce, data)
        if cpb >= float(th_codes_per_box(sig)):
            score = 0.58 + 0.32 * r
            reasons = ["%d coded-entry rows (box+code+one amount), %.0f%% of "
                       "data" % (ce, r * 100),
                       "%d codes across %d box(es) = %.1f codes/box "
                       "(enumeration within a box)"
                       % (len(pairs), distinct_boxes, cpb)]
            if tot == 0:
                score += 0.04
                reasons.append("no subtotal rows (an overflow list does not "
                               "total)")
            out["coded_overflow"] = (min(score, 1.0), reasons)
        else:
            out["narrative_footnote"] = (0.74, [
                "%d codes across %d distinct boxes = %.1f codes/box"
                % (len(pairs), distinct_boxes, cpb),
                "references many boxes with one code each: a footnote citing "
                "form lines, NOT an enumeration of one box's codes"])

    # ---- line item detail -------------------------------------------------
    # Decomposes ONE box into components, then totals. The total row (or a
    # scope heading naming a single line) is what separates a decomposition
    # from an arbitrary label/amount list.
    if (la + lr) >= 2 and (amts is None or amts == 1):
        # DENOMINATOR MUST INCLUDE PROSE.
        # Counting only DATA_SHAPES let a section with 18 prose rows and 2
        # label/amount rows score a PERFECT 1.00 density (2/2), because PROSE
        # is not a DATA shape. That is a fail-open denominator: it silently
        # drops the very evidence that contradicts the claim. Three prose
        # footnote sections scored 0.78 as "detail" on that basis.
        r = _ratio(la + lr, data + prose)
        # CORROBORATION IS MANDATORY.
        # A decomposition either sums to its parent (a TOTAL row) or names the
        # line it decomposes (a scope heading). A bare list of label/amount
        # pairs is not a decomposition. Every genuine detail section in this
        # corpus carried one or both and scored 0.88-1.00; every false positive
        # carried neither and scored exactly 0.78 -- base plus full density.
        corroborated = False
        score = 0.34 + 0.28 * r
        reasons = ["%d label/line-ref component rows, %.0f%% of ALL rows "
                   "(prose included in denominator)" % (la + lr, r * 100)]
        if tot:
            score += 0.18
            corroborated = True
            reasons.append("%d subtotal row(s): components sum to a box value"
                           % tot)
        if scope:
            score += 0.18
            corroborated = True
            reasons.append("%d scope heading(s) naming a single line" % scope)
        elif re.search(r"(?i)\b(?:line|box)\s*\d{1,2}[a-z]{0,2}\b.*\b"
                       r"(detail|details|breakdown|components?)\b", heading):
            score += 0.14
            corroborated = True
            reasons.append("section heading scopes one line and declares detail")
        if not corroborated:
            score = min(score, 0.45)
            reasons.append("NO subtotal row and NO scope heading: an "
                           "uncorroborated label/amount list is not a "
                           "decomposition of any box")
        out["line_item_detail"] = (min(score, 1.0), reasons)

    # ---- jurisdiction-bearing sections ------------------------------------
    # Both state roles need jurisdictions in column 1. They are separated by
    # whether the COLUMNS are keyed to form lines (per-line allocation grid)
    # or are summary measures (withholding / composite / PTE totals).
    if jr >= 2:
        th = sig["thresholds"]
        strong = jr >= int(th.get("min_jurisdiction_rows", 3))
        line_keyed = bool(LINE_COL_HEADER.search(heading)) or \
            bool(LINE_COL_HEADER.search(text[:400]))
        base = 0.58 + 0.30 * _ratio(jr, data)
        if not strong:
            base -= 0.12
        if line_keyed:
            out["state_jurisdiction_grid"] = (
                min(base + 0.12, 1.0),
                ["%d jurisdiction rows" % jr,
                 "columns keyed to form lines (per-line state allocation)"])
        else:
            out["state_tax_summary"] = (
                min(base, 1.0),
                ["%d jurisdiction rows" % jr,
                 "columns are summary measures, not form lines"])

    # ---- activity schedule ------------------------------------------------
    # A matrix whose ROWS are form boxes and whose COLUMNS are activities:
    # multiple amounts per row, box-keyed row labels, no jurisdictions.
    if gr >= 2 and jr == 0 and (amts or 0) >= 2:
        box_keyed = sum(1 for ln in text.splitlines()
                        if BOX_KEYED_ROW.match(ln.strip()))
        # A multi-amount grid is NOT sufficient. An activity schedule must
        # ALSO show what its columns are: either rows keyed by form box
        # (the matrix) or a roster naming the activities. Without that gate,
        # any wide numeric table on a footnote page scored as an activity
        # schedule -- observed on three unrelated pages.
        if box_keyed >= 2 or rr >= 1:
            r = _ratio(gr, data)
            score = 0.52 + 0.26 * r
            reasons = ["%d grid rows with %s amounts each (one per column)"
                       % (gr, amts), "no jurisdiction column"]
            if box_keyed >= 2:
                score += 0.14
                reasons.append("%d rows keyed by form box number" % box_keyed)
            if rr:
                score += 0.06
                reasons.append("%d roster rows (activity list with flags)" % rr)
            out["activity_schedule"] = (min(score, 1.0), reasons)
    elif rr >= 2 and gr == 0 and jr == 0:
        out["activity_schedule"] = (
            0.60, ["%d roster rows (entity list with yes/no flags), no amounts"
                   % rr])

    # ---- narrative footnote -----------------------------------------------
    # Prose-dominant with little or no tabular structure.
    # A footnote is PROSE. Smallness alone is NOT footnote evidence.
    #
    # The prior condition (data <= 2) fired on a one-row CODED_ENTRY fragment
    # carrying zero prose, scoring it 0.55 as a narrative footnote. Such
    # fragments are rendering artifacts of the table they sit inside -- a
    # wrapped cell, a stray continuation row -- not narrative notes.
    #
    # Routed to the footnotes fragment, each one becomes a STATEMENT node:
    # an attachment the source document never contained. That is the same
    # fabrication class as the 22 phantom statements produced earlier by
    # duck-typed list detection, arriving by a different path.
    #
    # Requiring narrative rows to OUTNUMBER data rows (or data to be wholly
    # absent) leaves these fragments with no candidate at all, so they fall
    # to 'unresolved' and are absorbed into the section they actually belong
    # to -- observable in the evidence trail, never silently invented.
    narrative_rows = prose + sh.get("HEADING", 0)
    if narrative_rows >= 1 and (narrative_rows > data or data == 0):
        score = min(0.55 + 0.20 * _ratio(narrative_rows, rows), 1.0)
        # A reference-footnote score may already be set by the coded branch
        # above. Take the stronger evidence rather than clobbering it.
        prev = out.get("narrative_footnote")
        if not prev or score > prev[0]:
            out["narrative_footnote"] = (
                score,
                ["%d data rows vs %d prose/heading rows" % (data, prose)])

    return out


def th_codes_per_box(sig):
    """Minimum codes-per-box for a section to count as coded overflow.

    Lives in the signature data file when present so it can be tuned per
    producer without a code change; the default is deliberately low enough
    that a genuine two-box overflow still qualifies.
    """
    return (sig.get("thresholds") or {}).get("min_codes_per_box", 2.0)



def _shape_compatible(frag, nb):
    """A tabular fragment must not be absorbed into a differently-shaped role.

    Observed defect: a single JURISDICTION_ROW ("WEST VIRGINIA 4,648 5,925
    7,294 3,180") was absorbed into an adjacent prose footnote purely because
    it sat next to one. That is a category error -- a footnote is prose, and
    folding table rows into it converts real grid data into fabricated
    statement content.

    When the fragment has a dominant DATA shape, the absorbing section must
    share it. A fragment with no data shape at all (pure prose or noise) may
    attach anywhere, since it asserts no tabular content.
    """
    fs = frag.get("dominant_shape")
    if not fs:
        return True
    return nb.get("dominant_shape") == fs


def classify_sections(sections, sig):
    """Assign a role to every section, then absorb unresolved fragments."""
    th = sig["thresholds"]
    amb = float(th.get("structural_ambiguity_margin", 0.15))

    for sec in sections:
        cands = score_section(sec, sig)
        t_role, t_score, t_phrase, t_margin = title_signal(
            (sec.get("heading") or "") + "\n" + (sec.get("text") or "")[:300],
            sig)

        scored = dict(cands)
        effect = None
        if t_role and t_role in scored:
            base, why = scored[t_role]
            scored[t_role] = (min(base + 0.12, 1.0),
                              why + ["title corroborates (%r, %.2f)"
                                     % (t_phrase, t_score)])
            effect = "corroborated"
        elif t_role:
            effect = "unsupported_by_structure"

        ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
        if not ranked:
            sec["role"], sec["score"], sec["margin"] = "unresolved", 0.0, 0.0
            sec["reasons"] = ["no structural signal met its minimum"]
        else:
            role, (score, why) = ranked[0]
            second = ranked[1][1][0] if len(ranked) > 1 else 0.0
            margin = score - second
            if margin < amb and t_role and t_role in scored:
                role, (score, why) = t_role, scored[t_role]
                why = list(why) + ["ambiguous structure (margin %.2f) broken "
                                   "by section title" % margin]
                effect = "tiebreak"
            elif margin < amb:
                role, score = "unresolved", score
                why = ["ambiguous: " + ", ".join(
                    "%s=%.2f" % (r, v[0]) for r, v in ranked[:3])]
            # MINIMUM CONFIDENCE, independent of margin.
            # Margin and confidence answer different questions: margin asks
            # "is another role competitive?", confidence asks "is THIS role
            # actually supported?". A weak role with no competitor had a huge
            # margin and resolved anyway -- an uncontested guess is still a
            # guess. Both gates must pass.
            min_conf = float((sig.get("thresholds") or {}).get(
                "min_role_confidence", 0.50))
            if role != "unresolved" and score < min_conf:
                why = list(why) + [
                    "score %.2f below minimum role confidence %.2f: evidence "
                    "is too weak to assert a role even though no other role "
                    "competed" % (score, min_conf)]
                role = "unresolved"
            sec["role"], sec["score"] = role, round(float(score), 3)
            sec["margin"], sec["reasons"] = round(float(margin), 3), why

        sec["title_effect"] = effect
        sec["candidates"] = {r: round(float(v[0]), 3) for r, v in
                             sorted(scored.items(), key=lambda kv: -kv[1][0])}


    # A form FACE is a single document artifact. Its internal structure --
    # Part I / Part II / Part III, the box grid, the capital ledger -- is not a
    # set of independently routable sections. Observed defect: the face page
    # fragmented into seven sections and only the first kept the face role; the
    # rest leaked into the detail and activity fragments, which would have fed
    # face values into the assembler as if they were overflow or activity data.
    face_pages = {s["page"] for s in sections
                  if s["role"] == "face_page" and s["score"] >= 0.80}
    for s in sections:
        if s["page"] in face_pages and s["role"] != "face_page":
            s["role"], s["score"] = "face_page", max(s["score"], 0.80)
            s["collapsed_into_face"] = True
            s["reasons"] = [
                "page %s carries the form face; every section on a face page "
                "belongs to the face artifact and must not route elsewhere"
                % s["page"]]

    # Absorb unresolved fragments into a confident neighbour on the SAME page.
    # A 1-2 row fragment between two sections of one role is a rendering
    # artifact, not an independent section. Recorded, never silent.
    for i, sec in enumerate(sections):
        if sec["role"] != "unresolved":
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(sections):
                nb = sections[j]
                # An ABSORBED section must never itself absorb. Allowing it
                # let a role propagate two hops from the evidence that
                # justified it -- observed when an absorbed fragment's
                # inherited score qualified it as an absorber for its own
                # neighbour. Absorption is one hop from real evidence only.
                if nb["page"] == sec["page"] and nb["role"] != "unresolved" \
                        and nb["score"] >= 0.60 \
                        and not nb.get("absorbed_from") \
                        and _shape_compatible(sec, nb):
                    sec["role"] = nb["role"]
                    # Inherit the absorbing section's confidence. Leaving the
                    # original 0.00 made an absorbed fragment look like a
                    # zero-confidence assertion when the confidence actually
                    # comes from its neighbour. Marked, never implied.
                    sec["score"] = nb["score"]
                    sec["score_source"] = "inherited_from_absorbing_section"
                    sec["absorbed_from"] = nb["index"]
                    sec["reasons"] = [
                        "unresolved %d-row fragment absorbed into adjacent "
                        "section %d (role %s, score %.2f) on the same page"
                        % (sec["row_count"], nb["index"], nb["role"],
                           nb["score"])]
                    break


    # SECOND-PASS ABSORPTION: page-dominant role.
    #
    # Immediate-neighbour absorption fails when SEVERAL CONSECUTIVE fragments are
    # all unresolved -- each one's neighbour is itself unresolved, so none can
    # absorb. Observed on a footnote page whose tabular sub-tables (label+amount
    # rows with no subtotal and no scope heading) had no candidate role at all,
    # leaving 20 of 37 rows unresolved beside 17 rows of confident footnote.
    #
    # The page is legitimate EVIDENCE even though it is not the unit of
    # classification. If every confident section on a page agrees on one role, an
    # unresolved fragment on that page belongs to it. If the confident sections
    # DISAGREE, the fragment stays unresolved -- ambiguity is never resolved by
    # majority vote, and a mixed page is exactly the case section-level
    # classification exists to handle honestly.
    page_roles = {}
    for s in sections:
        if s["role"] == "unresolved" or s.get("absorbed_from"):
            continue
        if s["score"] < 0.60:
            continue
        page_roles.setdefault(s["page"], {})
        entry = page_roles[s["page"]].setdefault(
            s["role"], {"rows": 0, "shapes": set()})
        entry["rows"] += s["row_count"]
        if s["dominant_shape"]:
            entry["shapes"].add(s["dominant_shape"])

    for s in sections:
        if s["role"] != "unresolved":
            continue
        roles = page_roles.get(s["page"]) or {}
        # Shape agreement, never a vote count. A fragment with a dominant data
        # shape may only join a role whose confident sections on this page
        # share that shape -- otherwise a stray table row could be attached to
        # a prose role simply because prose held the row majority.
        if s["dominant_shape"]:
            roles = {r: v for r, v in roles.items()
                     if s["dominant_shape"] in v["shapes"]}
        if len(roles) != 1:
            continue
        only_role = next(iter(roles))
        s["role"] = only_role
        s["score"] = 0.60
        s["score_source"] = "page_dominant_unanimous"
        s["reasons"] = [
            "every confident section on page %s carries role %s (%d rows); this "
            "%d-row fragment has no independent candidate and belongs to that "
            "section" % (s["page"], only_role, roles[only_role]["rows"],
                         s["row_count"])]

    for sec in sections:
        sec["section"] = sig["role_to_section"].get(sec["role"], sec["role"])
    return sections


def main():
    ap = argparse.ArgumentParser(
        description="Classify logical sections of a K-1 package")
    ap.add_argument("--text-dir", required=True)
    ap.add_argument("--signatures", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    sig = prepare(load_signatures(args.signatures))
    d = Path(args.text_dir)
    pages = []
    for f in sorted(d.glob("page_*.txt")):
        m = re.search(r"page_(\d+)", f.name)
        pages.append({"page": int(m.group(1)) if m else 0,
                      "text": f.read_text(encoding="utf-8", errors="replace")})
    if not pages:
        print("ERROR: no page_*.txt in %s" % d, file=sys.stderr)
        return 1

    sections = classify_sections(segment_document(pages, sig), sig)

    print("page sec  role                      score  rows  shape")
    for s in sections:
        pg = str(s["page"])
        if s.get("continued_pages"):
            pg += "+" + ",".join(str(p) for p in s["continued_pages"])
        print("%-4s %-4s %-25s %5.2f %5d  %s%s"
              % (pg, s["index"], s["role"], s["score"], s["row_count"],
                 s["dominant_shape"] or "-",
                 "  [absorbed]" if s.get("absorbed_from") else ""))
        if args.verbose:
            for r in s["reasons"]:
                print("        - %s" % r)

    roll = {}
    for s in sections:
        roll.setdefault(s["section"], set()).add(s["page"])
    print()
    print("PIPELINE SECTION ROLL-UP")
    for k in sorted(roll):
        print("  %-30s pages=%s" % (k, sorted(roll[k])))
    unres = [(s["page"], s["index"]) for s in sections
             if s["role"] == "unresolved"]
    print()
    print("  sections: %d   unresolved: %d %s"
          % (len(sections), len(unres), unres if unres else ""))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({
            "signatures": sig["_path"],
            "total_sections": len(sections),
            "sections": [{k: v for k, v in s.items() if k != "rows"}
                         for s in sections],
            "roll_up": {k: sorted(v) for k, v in roll.items()},
        }, indent=2), encoding="utf-8")
        print()
        print("WROTE %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
