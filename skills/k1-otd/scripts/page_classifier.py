
#!/usr/bin/env python
"""Structural, producer-neutral page-role classifier for K-1 packages.

WHY THIS EXISTS
    The retired page-hint classifier tested bare
    lowercase substrings against whole-page text. Two matches were
    catastrophic:

        "eci"          matched inside  sp-eci-fied / pr-eci-ous / d-eci-sion
        "state income" matched inside  real e-state income

    The second caused a LINE ITEM DETAILS page (line 20V, unrelated business
    taxable income) to be routed to the state-schedule section. The first
    caused nearly every page to fall through to "footnote".

    It also never read any page's own title, so four pages explicitly titled
    "OVERFLOW STATEMENTS" were filed as footnotes.

DESIGN
    1. STRUCTURE FIRST. A page's role is decided by the SHAPE of its rows
       (coded entries / label+amount components / jurisdiction rows /
       line-keyed grid headers / prose). Structure is imposed by the tax
       form; titles are imposed by the producer.

    2. VOCABULARY SECOND, IN DATA. Title phrases live in
       signatures/page-signatures.yaml, never in this file. Matching is
       word-boundary anchored and fuzzy with BOTH a score gate and a margin
       gate; failing either discards the title signal entirely.

    3. AMBIGUITY ESCALATES. Two close structural candidates yield
       role="unresolved". A page is NEVER silently defaulted to a role.
       This is the fail-closed counterpart to the old fall-through default.

    4. CONTINUATION IS EXPLICIT. A page structurally identical to its
       predecessor, with no competing title of its own, inherits that role
       and is marked method="continuation" -- observable, not implied.

    5. EVERY DECISION CARRIES EVIDENCE. Each page reports its signal counts,
       candidate scores, margin, and the reason for the chosen role.

No new dependencies: re + difflib from the stdlib, ruamel.yaml already
required elsewhere in this skill.
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from ruamel.yaml import YAML
except ImportError:  # pragma: no cover
    print("ERROR: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)

DEFAULT_SIGNATURES = (Path(__file__).resolve().parent.parent
                      / "signatures" / "page-signatures.yaml")

_WORD = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Signature loading
# ---------------------------------------------------------------------------

def load_signatures(path=None):
    """Load the signature data file. Fails LOUDLY if absent.

    A classifier that cannot load its signatures has not classified
    anything. There is deliberately no permissive fallback here.
    """
    p = Path(path) if path else DEFAULT_SIGNATURES
    if not p.exists():
        raise FileNotFoundError(
            "page signatures not found at %s -- refusing to classify without "
            "them (a classifier with no signatures has not classified)" % p)
    yaml = YAML(typ="safe")
    with open(p, encoding="utf-8") as fh:
        sig = yaml.load(fh)
    for key in ("row_patterns", "title_lexicon", "thresholds",
                "role_to_section", "face_markers"):
        if key not in sig:
            raise ValueError("page signatures missing required key: %s" % key)
    sig["_path"] = p.name
    sig["_jurisdictions"] = _flatten_jurisdictions(sig.get("jurisdiction_codes"))
    sig["_compiled"] = {
        name: [re.compile(pat) for pat in pats]
        for name, pats in sig["row_patterns"].items()
    }
    sig["_face_any"] = [re.compile(p) for p in
                        (sig["face_markers"].get("required_any") or [])]
    sig["_face_parts"] = [re.compile(p) for p in
                          (sig["face_markers"].get("required_parts") or [])]
    return sig


def _flatten_jurisdictions(raw):
    """The YAML list may hold comma-joined strings; normalize to a set."""
    out = set()
    for item in (raw or []):
        for tok in str(item).replace(",", " ").split():
            tok = tok.strip().upper()
            if len(tok) == 2 and tok.isalpha():
                out.add(tok)
    return out


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

def split_sections(text):
    """Separate the raw text layer from the '=== TABLES ===' block that
    text extraction appends. Both are searched; keeping them apart lets us reason
    about which channel produced a signal."""
    marker = "=== TABLES ==="
    if marker in text:
        head, _, tail = text.partition(marker)
        return head, tail
    return text, ""


def heading_lines(text, limit=6):
    """First few non-empty, non-decorative lines -- the page's own title
    region. Producers print the title at the top; we do not guess further
    down the page."""
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("=") or s.startswith("---"):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def normalize_phrase(s):
    return " ".join(_WORD.findall(s.lower()))


# ---------------------------------------------------------------------------
# Structural signals
# ---------------------------------------------------------------------------

def structural_signals(text, sig):
    """Count producer-neutral structural features across the whole page."""
    body, tables = split_sections(text)
    whole = text
    comp = sig["_compiled"]

    def count(name, hay):
        n = 0
        for rx in comp.get(name, []):
            n += len(rx.findall(hay))
        return n

    lines = [l for l in whole.splitlines() if l.strip()]
    alpha_words = 0
    numeric_tokens = 0
    for l in lines:
        for tok in l.split():
            t = tok.strip("|$(),.").replace(",", "")
            if not t:
                continue
            if t.replace(".", "").replace("-", "").isdigit():
                numeric_tokens += 1
            elif any(c.isalpha() for c in t):
                alpha_words += 1
    total_tokens = alpha_words + numeric_tokens

    # Jurisdiction rows: pattern match AND a real USPS code in cell 1.
    juris = 0
    juris_seen = set()
    for rx in comp.get("jurisdiction_row", []):
        for m in rx.finditer(whole):
            code = (m.group(1) or "").upper()
            if code in sig["_jurisdictions"]:
                juris += 1
                juris_seen.add(code)

    sigs = {
        "coded_entry_rows": count("coded_entry_row", whole),
        "uncoded_line_rows": count("uncoded_line_row", whole),
        "label_amount_rows": count("label_amount_row", whole),
        "total_rows": count("total_row", whole),
        "detail_scope_headings": count("detail_scope_heading", whole),
        "line_column_headers": count("line_column_header", whole),
        "jurisdiction_rows": juris,
        "distinct_jurisdictions": len(juris_seen),
        "has_tables_block": bool(tables.strip()),
        "line_count": len(lines),
        "alpha_words": alpha_words,
        "numeric_tokens": numeric_tokens,
        "alpha_ratio": (alpha_words / total_tokens) if total_tokens else 1.0,
    }

    face_any = sum(1 for rx in sig["_face_any"] if rx.search(whole))
    face_parts = sum(1 for rx in sig["_face_parts"] if rx.search(whole))
    sigs["face_identity_markers"] = face_any
    sigs["face_part_headings"] = face_parts
    return sigs


# ---------------------------------------------------------------------------
# Title signal (secondary, gated)
# ---------------------------------------------------------------------------

def title_signal(text, sig):
    """Fuzzy-match the page's heading region against the title lexicon.

    Returns (role, score, matched_phrase, margin) or (None, 0.0, None, 0.0).

    BOTH gates must pass:
      score  >= thresholds.title_match_score
      margin >= thresholds.title_match_margin   (best vs best-other-role)

    A score gate alone accepts confident nonsense. The margin gate is what
    actually detects ambiguity between two plausible roles.
    """
    th = sig["thresholds"]
    heads = heading_lines(text)
    if not heads:
        return None, 0.0, None, 0.0
    hay = [normalize_phrase(h) for h in heads]

    best_by_role = {}
    for role, phrases in sig["title_lexicon"].items():
        best = 0.0
        best_phrase = None
        for phrase in (phrases or []):
            p = normalize_phrase(str(phrase))
            if not p:
                continue
            for h in hay:
                if not h:
                    continue
                # Exact containment on word boundaries is the strong case.
                if re.search(r"\b" + re.escape(p) + r"\b", h):
                    score = 1.0
                else:
                    score = difflib.SequenceMatcher(None, p, h).ratio()
                    # Also try the phrase against a same-length window so a
                    # long heading does not dilute a genuine match.
                    if len(h) > len(p):
                        for i in range(0, len(h) - len(p) + 1, 2):
                            w = h[i:i + len(p)]
                            score = max(score, difflib.SequenceMatcher(
                                None, p, w).ratio())
                if score > best:
                    best, best_phrase = score, str(phrase)
        if best > 0:
            best_by_role[role] = (best, best_phrase)

    if not best_by_role:
        return None, 0.0, None, 0.0

    ranked = sorted(best_by_role.items(), key=lambda kv: kv[1][0], reverse=True)
    top_role, (top_score, top_phrase) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else 0.0
    margin = top_score - second

    if top_score < float(th["title_match_score"]):
        return None, top_score, top_phrase, margin
    if margin < float(th["title_match_margin"]):
        # Two roles matched almost equally well -- discard rather than guess.
        return None, top_score, top_phrase, margin
    return top_role, top_score, top_phrase, margin


# ---------------------------------------------------------------------------
# Structural candidate scoring
# ---------------------------------------------------------------------------

def structural_candidates(s, sig, page_num, total_pages):
    """Score each role from structural evidence alone. Returns
    {role: (score, [reasons])} with scores in 0..1."""
    th = sig["thresholds"]
    fm = sig["face_markers"]
    out = {}

    # --- face page -------------------------------------------------------
    if (s["face_identity_markers"] >= 1
            and s["face_part_headings"] >= int(fm.get("min_parts", 2))):
        score = 0.70 + 0.10 * min(s["face_part_headings"], 3)
        out["face_page"] = (min(score, 1.0), [
            "form identity marker present",
            "%d part headings present" % s["face_part_headings"]])

    # --- coded overflow --------------------------------------------------
    if s["coded_entry_rows"] >= int(th["min_coded_entry_rows"]):
        density = s["coded_entry_rows"] / max(s["line_count"], 1)
        score = min(0.55 + density, 1.0)
        reasons = ["%d coded entry rows (box+code+amount)" % s["coded_entry_rows"]]
        if s["total_rows"] == 0:
            score = min(score + 0.05, 1.0)
            reasons.append("no total rows (overflow lists, does not subtotal)")
        out["coded_overflow"] = (score, reasons)

    # --- line item detail ------------------------------------------------
    if (s["label_amount_rows"] >= int(th["min_label_amount_rows"])
            and (s["total_rows"] >= int(th["min_total_rows"])
                 or s["detail_scope_headings"] >= 1)):
        density = s["label_amount_rows"] / max(s["line_count"], 1)
        score = min(0.55 + density, 1.0)
        reasons = ["%d label+amount component rows" % s["label_amount_rows"]]
        if s["total_rows"]:
            reasons.append("%d total row(s) -- components sum to a box value"
                           % s["total_rows"])
        if s["detail_scope_headings"]:
            reasons.append("%d heading(s) scoping the page to one line"
                           % s["detail_scope_headings"])
        out["line_item_detail"] = (score, reasons)

    # --- state jurisdiction grid -----------------------------------------
    if s["distinct_jurisdictions"] >= int(th["min_jurisdiction_rows"]):
        score = min(0.60 + 0.02 * s["distinct_jurisdictions"], 1.0)
        reasons = ["%d distinct USPS jurisdiction codes in first column"
                   % s["distinct_jurisdictions"]]
        if s["line_column_headers"]:
            score = min(score + 0.10, 1.0)
            reasons.append("line-keyed column headers present")
        out["state_jurisdiction_grid"] = (score, reasons)

    # --- narrative footnote ----------------------------------------------
    structured = (s["coded_entry_rows"] + s["label_amount_rows"]
                  + s["jurisdiction_rows"])
    if (s["alpha_ratio"] >= float(th["prose_min_alpha_ratio"])
            and structured <= int(th["prose_max_structured_rows"])):
        out["narrative_footnote"] = (
            min(0.55 + (s["alpha_ratio"] - 0.80), 1.0),
            ["alpha/numeric token ratio %.2f (prose)" % s["alpha_ratio"],
             "%d structured rows (at or below prose ceiling)" % structured])

    return out


# ---------------------------------------------------------------------------
# Per-page classification
# ---------------------------------------------------------------------------

def classify_page(text, sig, page_num=None, total_pages=None):
    """Classify one page. Returns a full evidence record.

    Role is 'unresolved' when evidence is absent or ambiguous. There is no
    silent default -- the old fall-through-to-footnote behaviour is exactly
    the fail-open pattern this replaces.
    """
    th = sig["thresholds"]
    s = structural_signals(text, sig)
    cands = structural_candidates(s, sig, page_num, total_pages)
    t_role, t_score, t_phrase, t_margin = title_signal(text, sig)

    scored = dict(cands)
    title_effect = None
    if t_role:
        if t_role in scored:
            base, reasons = scored[t_role]
            scored[t_role] = (min(base + 0.15, 1.0),
                              reasons + ["title corroborates (%r, %.2f)"
                                         % (t_phrase, t_score)])
            title_effect = "corroborated"
        else:
            # Title alone is not sufficient to assert a role, but it is
            # recorded and used to break a structural tie below.
            title_effect = "unsupported_by_structure"

    ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
    role, score, reasons, margin = "unresolved", 0.0, [], 0.0

    if not ranked:
        reasons = ["no structural signal met its minimum threshold"]
    else:
        role, (score, reasons) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0.0
        margin = score - second
        if margin < float(th["structural_ambiguity_margin"]):
            if t_role and t_role in scored:
                role = t_role
                score, reasons = scored[t_role]
                reasons = list(reasons) + [
                    "ambiguous structure (margin %.2f) broken by page title"
                    % margin]
                title_effect = "tiebreak"
            else:
                reasons = ["ambiguous: %s" % ", ".join(
                    "%s=%.2f" % (r, v[0]) for r, v in ranked[:3])]
                role = "unresolved"

    return {
        "page": page_num,
        "role": role,
        "score": round(float(score), 3),
        "margin": round(float(margin), 3),
        "method": "structural" if title_effect is None else (
            "structural+title" if title_effect in
            ("corroborated", "tiebreak") else "structural"),
        "reasons": reasons,
        "title": {
            "matched_role": t_role,
            "score": round(float(t_score), 3),
            "phrase": t_phrase,
            "margin": round(float(t_margin), 3),
            "effect": title_effect,
        },
        "heading": heading_lines(text, 2),
        "signals": s,
        "candidates": {r: round(float(v[0]), 3) for r, v in ranked},
    }


# ---------------------------------------------------------------------------
# Document-level pass (continuation)
# ---------------------------------------------------------------------------

def _fingerprint(s, names):
    """Coarse structural fingerprint: which signals are present, bucketed.
    Exact counts vary page to page; presence/absence is what identifies a
    continuing section."""
    fp = []
    for n in names:
        v = s.get(n, 0)
        fp.append(0 if not v else (1 if v <= 5 else 2))
    return tuple(fp)


def classify_document(pages, sig):
    """pages: list of {'page': int, 'text': str}. Returns list of records.

    Applies the continuation rule: an unresolved page whose structural
    fingerprint matches the previous page's inherits that role, explicitly
    marked so the inheritance is auditable rather than invisible.
    """
    cont = sig.get("continuation") or {}
    enabled = bool(cont.get("enabled", True))
    inheritable = set(cont.get("inheritable_roles") or [])
    fp_names = cont.get("fingerprint_signals") or []
    respect_title = bool(cont.get("respect_own_title", True))

    records = [classify_page(p["text"], sig, p["page"], len(pages))
               for p in pages]

    if enabled and fp_names:
        for i in range(1, len(records)):
            cur, prev = records[i], records[i - 1]
            if cur["role"] != "unresolved":
                continue
            if prev["role"] not in inheritable:
                continue
            if respect_title and cur["title"]["matched_role"] and \
                    cur["title"]["matched_role"] != prev["role"]:
                cur["reasons"].append(
                    "continuation declined: own title indicates %s"
                    % cur["title"]["matched_role"])
                continue
            if _fingerprint(cur["signals"], fp_names) == \
                    _fingerprint(prev["signals"], fp_names):
                cur["role"] = prev["role"]
                cur["method"] = "continuation"
                cur["reasons"] = [
                    "structurally identical to page %s (role %s); no competing "
                    "title" % (prev["page"], prev["role"])]

    for r in records:
        r["section"] = sig["role_to_section"].get(r["role"], r["role"])
    return records


# ---------------------------------------------------------------------------
# CLI / self-test
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Structural page-role classifier for K-1 packages")
    ap.add_argument("--text-dir", required=True,
                    help="Directory of page_NN.txt files from extracted page text")
    ap.add_argument("--signatures", default=None,
                    help="Path to page-signatures.yaml")
    ap.add_argument("--out", default=None,
                    help="Optional path to write the JSON evidence report")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    sig = load_signatures(args.signatures)
    print("signatures: %s" % sig["_path"], flush=True)

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

    records = classify_document(pages, sig)

    print()
    print("=" * 108)
    print("%-6s %-26s %-18s %6s %6s  %s"
          % ("page", "role", "method", "score", "margin", "heading"))
    print("=" * 108)
    for r in records:
        head = (r["heading"][0] if r["heading"] else "")[:44]
        print("%-6s %-26s %-18s %6.2f %6.2f  %s"
              % (r["page"], r["role"], r["method"], r["score"],
                 r["margin"], head))
        if args.verbose:
            for reason in r["reasons"]:
                print("           - %s" % reason)

    print()
    print("SECTION ROLL-UP")
    roll = {}
    for r in records:
        roll.setdefault(r["section"], []).append(r["page"])
    for k in sorted(roll):
        print("  %-30s pages=%s" % (k, roll[k]))

    unresolved = [r["page"] for r in records if r["role"] == "unresolved"]
    print()
    print("  unresolved pages: %s" % (unresolved if unresolved else "none"))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({
            "signatures": sig["_path"],
            "total_pages": len(records),
            "pages": records,
            "sections": {k: v for k, v in sorted(roll.items())},
            "unresolved": unresolved,
        }, indent=2), encoding="utf-8")
        print()
        print("WROTE %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
