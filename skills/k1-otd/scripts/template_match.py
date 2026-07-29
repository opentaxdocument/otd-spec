
#!/usr/bin/env python
"""Template fit gate for K-1 face grammars.

Verifies whether a candidate face grammar's `fit_signals` block matches
observed document evidence, BEFORE any field extraction is trusted.

Design principles (see grammars/GRAMMAR-FORMAT.md, item 5):
  - Fit verification is separate from extraction. This script never reads
    field values; it only tests fit_signals against document-level evidence.
  - Ties among top-scoring candidates are reported, never broken silently.
    This fixes Extractium's TemplateMatcher.cs `matches.MaxBy(tm => tm.Score)`,
    which silently selects a winner based on candidate enumeration order.
  - required_text entries are ANDed together; "|"-separated alternatives
    within ONE entry are ORed. This fixes Extractium's inverted semantics
    (`parts.All(pageText.Contains)`, which required ALL alternatives within
    an entry -- making "|" alternation meaningless or actively wrong).
  - required_text matching is whitespace-normalized and case-insensitive.
    Extractium's matcher was case-sensitive raw Contains(), vulnerable to
    PDF-extraction line breaks splitting a required phrase across lines.
  - A document that cannot be evaluated (unreadable, page out of range, no
    content layer at all) forces verdict=unverified for EVERY candidate.
    This must never silently degrade to a match/mismatch verdict.
  - Checkbox frame signatures are computed at runtime as corroborating
    evidence only. They are never stored as a fixed expected value inside
    a grammar -- that would reintroduce the absolute-template anti-pattern
    through the back door.

Verdicts: match | partial | mismatch | unverified

Usage
-----
    python template_match.py --pdf PATH [--pdf PATH ...] --grammar PATH [--grammar PATH ...] [--page N]
    python template_match.py --selftest --pdf PATH --grammar PATH
"""
import sys
import json
import hashlib
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not available")
    sys.exit(2)

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber not available")
    sys.exit(2)


FRAME_MIN = 6.5
FRAME_MAX = 9.5
FRAME_COUNT_TOLERANCE = 1
PAGE_SIZE_TOLERANCE_PT = 2.0
TIE_SCORE_EPSILON = 0.01
DEFAULT_OUTDIR = "D:/SecondWind/Artifacts/20260727-otd-k1-alignment/probes/out"


def load_grammar_fit_signals(path):
    """Load only the fit_signals + identity block from a grammar YAML.

    Deliberately does NOT load `fields` -- the fit gate must never depend
    on extraction content, only on document-level fit evidence. This keeps
    fit verification and field extraction as genuinely separate concerns.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    form = doc.get("form", {}) or {}
    fit = doc.get("fit_signals", {}) or {}

    return {
        "grammar_path": str(path),
        "grammar_id": "%s-%s" % (form.get("form_name", "?"), form.get("tax_year", "?")),
        "schema_version": doc.get("schema_version"),
        "tax_year": form.get("tax_year"),
        "required_text": fit.get("required_text", []) or [],
        "page_size_points": fit.get("page_size_points"),
        "expected_checkbox_frame_count": fit.get("expected_checkbox_frame_count"),
        "ocr_catalog_token": fit.get("ocr_catalog_token"),
    }


def detect_checkbox_frames(page):
    """Detect checkbox-sized stroke-only rectangles, deduplicated.

    Empirically confirmed geometry (2026-07-28 session): 8x8pt stroke-only
    rects survive PDF flattening at identical coordinates across producers
    within a form-year. This is re-derived from the document under test on
    every call -- never a stored/hardcoded value.

    Deduplication note: flattened real preparer PDFs frequently render
    each checkbox frame as TWO overlapping identical rects (confirmed
    during the earlier session's Extractium probe: Copperleaf's frame at
    x0=324.00 top=38.00 appeared with an explicit "(x2)" duplicate marker,
    and every frame on that document showed the same pattern). Extractium's
    own DetectDrawnCheckBoxes.cs independently solves the identical problem
    via `.Deduplicate(MaxOverlap)`. Without this step, the first version of
    this function double-counted frames on real documents (36 raw vs 18
    unique), which understated the fit score for a document that should
    have matched cleanly -- caught by the first test-matrix run, not by
    inspection.
    """
    raw = []
    for r in page.rects:
        w, h = r.get("width", 0), r.get("height", 0)
        if FRAME_MIN <= w <= FRAME_MAX and FRAME_MIN <= h <= FRAME_MAX and not r.get("fill", False):
            raw.append(r)

    seen = {}
    for r in raw:
        key = (round(r.get("x0", 0), 1), round(r.get("top", 0), 1))
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def compute_frame_signature(frames):
    """Runtime-computed structural fingerprint.

    Corroborating evidence only -- never compared against a stored value
    in a grammar file. Storing an expected signature would reintroduce
    absolute-template coupling through the back door.
    """
    if not frames:
        return None
    keys = sorted((round(r.get("x0", 0)), round(r.get("top", 0))) for r in frames)
    blob = "|".join("%d,%d" % k for k in keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def extract_document_evidence(pdf_path, page_number=1):
    """Extract page-level fit evidence. Returns (evidence_dict, error_or_None).

    On any condition that prevents reliable measurement -- unreadable file,
    out-of-range page, no content layer at all -- returns evidence=None and
    a descriptive error. Callers MUST treat that as `unverified` for every
    candidate grammar. This function never guesses; it reports what it
    could not determine.
    """
    path = Path(pdf_path)
    if not path.exists():
        return None, "file not found: %s" % path

    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:
        return None, "could not open PDF: %s" % exc

    try:
        if page_number < 1 or page_number > len(pdf.pages):
            return None, "page %d out of range (document has %d pages)" % (page_number, len(pdf.pages))

        page = pdf.pages[page_number - 1]

        try:
            raw_text = page.extract_text() or ""
        except Exception as exc:
            return None, "text extraction failed: %s" % exc

        chars = page.chars
        if len(chars) == 0 and len(raw_text.strip()) == 0:
            return None, (
                "no content layer on page %d (likely scanned image; OCR "
                "required, not evaluable by this deterministic gate)" % page_number
            )

        # Whitespace normalization before matching -- collapses line breaks
        # that could otherwise split a required phrase across lines. This
        # is a deliberate improvement over Extractium's raw Contains().
        text_norm = " ".join(raw_text.split())

        frames = detect_checkbox_frames(page)
        signature = compute_frame_signature(frames)

        return {
            "page_width": float(page.width),
            "page_height": float(page.height),
            "page_text_lower": text_norm.lower(),
            "char_count": len(chars),
            "frame_count": len(frames),
            "frame_signature": signature,
        }, None
    finally:
        pdf.close()


def evaluate_required_text(required_text, page_text_lower):
    """required_text entries are ANDed; '|'-separated alternatives within
    ONE entry are ORed.

    Corrects Extractium's TemplateMatcher.cs, which used
    `parts.All(pageText.Contains)` -- requiring ALL alternatives within one
    entry to be present, making '|' alternation meaningless (or actively
    wrong) for any real use of the feature.
    """
    if not required_text:
        return True, []

    entry_results = []
    for entry in required_text:
        alts = [a.strip().lower() for a in entry.split("|") if a.strip()]
        matched_alt = next((a for a in alts if a in page_text_lower), None)
        entry_results.append({
            "entry": entry,
            "matched_alt": matched_alt,
            "pass": matched_alt is not None,
        })

    all_pass = all(r["pass"] for r in entry_results)
    return all_pass, entry_results


def evaluate_grammar_fit(grammar_sig, evidence):
    """Score one grammar against extracted document evidence.

    Returns a dict with verdict in {match, partial, mismatch}. The caller
    is responsible for the `unverified` case (evidence is None) -- this
    function is never invoked when evidence could not be measured.
    """
    required_ok, required_detail = evaluate_required_text(
        grammar_sig["required_text"], evidence["page_text_lower"]
    )

    page_size_ok = True
    page_size_detail = None
    if grammar_sig["page_size_points"]:
        exp_w, exp_h = grammar_sig["page_size_points"]
        page_size_ok = (
            abs(evidence["page_width"] - exp_w) <= PAGE_SIZE_TOLERANCE_PT and
            abs(evidence["page_height"] - exp_h) <= PAGE_SIZE_TOLERANCE_PT
        )
        page_size_detail = {
            "expected": [exp_w, exp_h],
            "observed": [evidence["page_width"], evidence["page_height"]],
            "pass": page_size_ok,
        }

    frame_count_ok = None
    frame_count_detail = None
    if grammar_sig["expected_checkbox_frame_count"] is not None:
        exp_n = grammar_sig["expected_checkbox_frame_count"]
        obs_n = evidence["frame_count"]
        frame_count_ok = abs(obs_n - exp_n) <= FRAME_COUNT_TOLERANCE
        frame_count_detail = {"expected": exp_n, "observed": obs_n, "pass": frame_count_ok}

    ocr_token_present = None
    if grammar_sig["ocr_catalog_token"]:
        ocr_token_present = grammar_sig["ocr_catalog_token"].lower() in evidence["page_text_lower"]

    # required_text and page_size are CRITICAL: failing either is a hard
    # mismatch, never softened by corroborating signals.
    critical_pass = required_ok and page_size_ok

    if not critical_pass:
        verdict = "mismatch"
        score = 0.0
    else:
        score = 0.6  # baseline once both critical signals pass
        if frame_count_ok:
            score += 0.3
        elif frame_count_ok is False:
            score -= 0.2
        if ocr_token_present:
            score += 0.1
        score = max(0.0, min(1.0, score))
        verdict = "match" if score >= 0.85 else "partial"

    return {
        "grammar_id": grammar_sig["grammar_id"],
        "verdict": verdict,
        "score": round(score, 3),
        "required_text": {"pass": required_ok, "detail": required_detail},
        "page_size": page_size_detail,
        "frame_count": frame_count_detail,
        "ocr_catalog_token_present": ocr_token_present,
        "frame_signature_observed": evidence["frame_signature"],
    }


def resolve_ties(results):
    """Flag ambiguous ties among top-scoring candidates. NEVER breaks a tie
    silently.

    Fixes Extractium's `matches.MaxBy(tm => tm.Score)`, which silently picks
    a winner among ties based on candidate-list order -- itself dependent
    on filesystem enumeration order and therefore non-deterministic across
    machines and runs.
    """
    if not results:
        return [], False

    ordered = sorted(results, key=lambda r: r["grammar_id"])  # deterministic pre-sort
    top_score = max(r["score"] for r in ordered)
    top_candidates = [r for r in ordered if abs(r["score"] - top_score) <= TIE_SCORE_EPSILON]

    is_tie = len(top_candidates) > 1
    return top_candidates, is_tie


def evaluate_document(pdf_path, grammar_paths, page_number=1):
    """Evaluate one document against N candidate grammars.

    Grammar paths are sorted before evaluation so results never depend on
    filesystem enumeration order (the root cause of Extractium's tie
    non-determinism).
    """
    evidence, error = extract_document_evidence(pdf_path, page_number)

    report = {
        "pdf_path": str(pdf_path),
        "page_number": page_number,
        "evaluable": evidence is not None,
        "evaluation_error": error,
        "candidates": [],
        "recommended_grammar_id": None,
        "overall_verdict": None,
        "ambiguous_tie": False,
    }

    grammar_sigs = [load_grammar_fit_signals(p) for p in sorted(grammar_paths)]

    if evidence is None:
        # Hard rule: cannot evaluate -> every candidate is unverified.
        # This branch NEVER falls through to match/mismatch logic below.
        for gs in grammar_sigs:
            report["candidates"].append({
                "grammar_id": gs["grammar_id"],
                "verdict": "unverified",
                "score": None,
                "reason": error,
            })
        report["overall_verdict"] = "unverified"
        return report

    results = [evaluate_grammar_fit(gs, evidence) for gs in grammar_sigs]
    report["candidates"] = results
    report["document_evidence"] = {
        "page_width": evidence["page_width"],
        "page_height": evidence["page_height"],
        "char_count": evidence["char_count"],
        "frame_count": evidence["frame_count"],
        "frame_signature": evidence["frame_signature"],
    }

    top_candidates, is_tie = resolve_ties(results)
    report["ambiguous_tie"] = is_tie

    if is_tie:
        # Hard posture: a tie among top candidates is NOT resolved silently.
        report["overall_verdict"] = "unverified"
        report["tie_candidates"] = [c["grammar_id"] for c in top_candidates]
        report["recommended_grammar_id"] = None
    else:
        winner = top_candidates[0] if top_candidates else None
        report["overall_verdict"] = winner["verdict"] if winner else "mismatch"
        report["recommended_grammar_id"] = (
            winner["grammar_id"] if winner and winner["verdict"] in ("match", "partial") else None
        )

    return report


def run_selftest(pdf_path, grammar_path, page_number):
    """Deterministic proof that identical-score ties are flagged, never
    silently broken.

    Duplicates one grammar's fit signals under a second label and confirms
    ambiguous_tie=True and recommended_grammar_id=None result.
    """
    evidence, error = extract_document_evidence(pdf_path, page_number)
    if evidence is None:
        print("SELFTEST SKIPPED: document not evaluable (%s)" % error, flush=True)
        return 1

    gs = load_grammar_fit_signals(grammar_path)
    gs_dup = dict(gs)
    gs_dup["grammar_id"] = gs["grammar_id"] + "__dup"

    r1 = evaluate_grammar_fit(gs, evidence)
    r2 = evaluate_grammar_fit(gs_dup, evidence)
    top_candidates, is_tie = resolve_ties([r1, r2])

    print("SELFTEST: tie detection (fixes Extractium MaxBy silent-winner defect)", flush=True)
    print("    candidate A: %-30s score=%s verdict=%s" % (r1["grammar_id"], r1["score"], r1["verdict"]), flush=True)
    print("    candidate B: %-30s score=%s verdict=%s (duplicate signals)" % (r2["grammar_id"], r2["score"], r2["verdict"]), flush=True)
    print("    ambiguous_tie=%s (expected True)" % is_tie, flush=True)

    ok = is_tie is True
    print("    RESULT: %s" % ("PASS" if ok else "FAIL"), flush=True)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Template fit gate for K-1 face grammars.")
    ap.add_argument("--pdf", action="append", help="PDF to evaluate. Repeatable.")
    ap.add_argument("--grammar", required=True, action="append", help="Candidate grammar YAML path. Repeatable.")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--selftest", action="store_true", help="Run tie-detection self-test instead of normal evaluation.")
    args = ap.parse_args()

    if args.selftest:
        if not args.pdf:
            print("ERROR: --selftest requires at least one --pdf")
            return 2
        return run_selftest(args.pdf[0], args.grammar[0], args.page)

    if not args.pdf:
        print("ERROR: --pdf is required (unless --selftest)")
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    all_reports = []
    print("TEMPLATE FIT GATE", flush=True)
    print("Candidates: %s" % ", ".join(sorted(args.grammar)), flush=True)
    print("=" * 110, flush=True)

    for pdf_path in args.pdf:
        report = evaluate_document(pdf_path, args.grammar, args.page)
        all_reports.append(report)

        name = Path(pdf_path).name
        print("DOC: %s" % name, flush=True)
        if not report["evaluable"]:
            print("    UNVERIFIED (document not evaluable): %s" % report["evaluation_error"], flush=True)
        else:
            ev = report["document_evidence"]
            print("    page=%sx%s frames=%d sig=%s" % (
                ev["page_width"], ev["page_height"], ev["frame_count"], ev["frame_signature"]), flush=True)
            for c in report["candidates"]:
                print("    %-24s verdict=%-10s score=%s" % (c["grammar_id"], c["verdict"], c.get("score")), flush=True)
                if not c["required_text"]["pass"]:
                    for d in c["required_text"]["detail"]:
                        if not d["pass"]:
                            print("        required_text FAILED: %r" % d["entry"], flush=True)
        print("    OVERALL: %s  tie=%s  recommended=%s" % (
            report["overall_verdict"], report["ambiguous_tie"], report["recommended_grammar_id"]), flush=True)
        print("-" * 110, flush=True)

    outpath = outdir / "template_fit_report.json"
    with open(outpath, "w", encoding="utf-8") as fh:
        json.dump(all_reports, fh, indent=2, default=str)
    print("WROTE %s" % outpath.resolve(), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
