#!/usr/bin/env python
"""OTD K-1 Face Reader Validation Suite.

Encodes ground truth manually confirmed during the face-extraction build
session (2026-07-28/29): every value below was cross-checked against raw
PDF word/curve evidence before being asserted here, not invented as an
expected value and then coded to match. The verified source PDFs are shipped
as repository fixtures so this regression suite is portable.

This suite exercises the deterministic face_reader.py pipeline end-to-end
against two real documents:
  - the blank IRS Schedule K-1 (Form 1065) 2025 -- everything should be
    blank/verified_absent, nothing should be fabricated as present.
  - Copperleaf (Meridian Real Assets Aggregator) -- a real, flattened,
    non-AcroForm preparer document with genuine filled-in values.

A pass here means the face reader reproduces, via subprocess invocation
(matching this repo's existing test convention), every value confirmed by
hand during interactive development. It is a regression net, not new
verification -- ground truth was established by direct evidence, this
suite exists so a future change cannot silently reintroduce a fixed defect.

Exit 0 = every assertion held.
Exit 1 = at least one confirmed value regressed or was never fixed.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
FACE_READER = REPO_ROOT / "skills/k1-otd/scripts/face_reader.py"
GRAMMAR = REPO_ROOT / "skills/k1-otd/grammars/k1-1065-2025.grammar.yaml"

IRS_BLANK_PDF = (
    REPO_ROOT / "tests/fixtures/pdf/irs-k1-1065-2025-blank.pdf"
)
COPPERLEAF_PDF = (
    REPO_ROOT / "examples/k1-1065-2025-synthetic/source/synthetic-k1.pdf"
)


class HarnessError(Exception):
    """Raised when a fixture PDF is missing or the reader cannot run at
    all. A missing-fixture case must not silently pass -- that would be
    exactly the false-assurance failure mode this suite exists to catch."""


def run_face_reader(pdf_path):
    if not pdf_path.exists():
        raise HarnessError("Fixture PDF not found: %s" % pdf_path)
    result = subprocess.run(
        [sys.executable, str(FACE_READER), str(pdf_path), str(GRAMMAR)],
        cwd=str(FACE_READER.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        raise HarnessError(
            "face_reader.py exited %d on %s\nSTDERR:\n%s" %
            (result.returncode, pdf_path.name, result.stderr))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise HarnessError(
            "face_reader.py did not emit valid JSON for %s: %s\nSTDOUT (first 500):\n%s" %
            (pdf_path.name, e, result.stdout[:500]))


def field(result, key):
    if key not in result["fields"]:
        raise HarnessError("Field %r not present in output (grammar/reader mismatch?)" % key)
    return result["fields"][key]


def check(label, actual, expected, failures):
    if actual != expected:
        failures.append("  [FAIL] %-40s actual=%r expected=%r" % (label, actual, expected))
    else:
        print("  [OK]   %-40s = %r" % (label, actual))


def check_case(name, pdf_path, cases):
    """cases: list of (label, field_key, extractor_fn, expected) tuples.
    extractor_fn receives the field envelope dict and returns the value
    to compare against `expected`."""
    print("=" * 100)
    print("CASE: %s (%s)" % (name, pdf_path.name))
    result = run_face_reader(pdf_path)
    failures = []
    for label, field_key, extractor, expected in cases:
        try:
            f = field(result, field_key)
            actual = extractor(f)
        except HarnessError as e:
            failures.append("  [FAIL] %-40s HARNESS ERROR: %s" % (label, e))
            continue
        check(label, actual, expected, failures)
    for f in failures:
        print(f)
    print("RESULT: %s" % ("PASS" if not failures else "FAIL"))
    return len(failures) == 0


def status_of(f):
    return f["status"]


def value_of(f):
    return f.get("normalized_value")


def nested(*path):
    def extractor(f):
        v = f.get("normalized_value")
        for p in path:
            if v is None:
                return None
            v = v.get(p) if isinstance(v, dict) else None
        return v
    return extractor


def nested_status(*path):
    def extractor(f):
        v = f.get("normalized_value")
        for p in path[:-1]:
            if v is None:
                return None
            v = v.get(p) if isinstance(v, dict) else None
        if v is None or not isinstance(v, dict):
            return None
        leaf = v.get(path[-1])
        return leaf.get("status") if isinstance(leaf, dict) else None


def nested_value(*path):
    def extractor(f):
        v = f.get("normalized_value")
        for p in path[:-1]:
            if v is None:
                return None
            v = v.get(p) if isinstance(v, dict) else None
        if v is None or not isinstance(v, dict):
            return None
        leaf = v.get(path[-1])
        return leaf.get("value") if isinstance(leaf, dict) else None


IRS_BLANK_CASES = [
    ("checkbox_frame_count", "item_g", lambda f: None, None),  # placeholder, overwritten below
]


def main():
    all_pass = True
    backfilled_fields = (
        "item_a", "item_b", "item_c", "item_e", "item_f", "item_h2",
        "item_i1", "box_11", "box_13", "box_14", "box_15", "box_17",
        "box_18", "box_19", "box_20",
    )

    def coded_signature(result, key):
        rows = field(result, key)["normalized_value"]
        return [
            (
                row.get("code"),
                row.get("value"),
                bool(row.get("statement_reference")),
            )
            for row in rows
        ]

    # ---- IRS blank: everything should be blank/verified_absent, nothing fabricated ----
    blank_result = run_face_reader(IRS_BLANK_PDF)
    print("=" * 100)
    print("CASE: IRS blank (%s)" % IRS_BLANK_PDF.name)
    blank_failures = []
    check("checkbox_frame_count", blank_result["checkbox_frame_count"], 18, blank_failures)
    check("item_g.status", field(blank_result, "item_g")["status"], "blank", blank_failures)
    check("item_h1.status", field(blank_result, "item_h1")["status"], "blank", blank_failures)
    check("item_m.status", field(blank_result, "item_m")["status"], "blank", blank_failures)
    check("box_1.status", field(blank_result, "box_1")["status"], "blank", blank_failures)
    check("box_2.status", field(blank_result, "box_2")["status"], "blank", blank_failures)
    check("box_5.status", field(blank_result, "box_5")["status"], "blank", blank_failures)
    check("box_12.status", field(blank_result, "box_12")["status"], "blank", blank_failures)
    check("box_21.status", field(blank_result, "box_21")["status"], "blank", blank_failures)
    check("box_4c.status (genuine blank, confirmed non-bug)",
          field(blank_result, "box_4c")["status"], "blank", blank_failures)
    check("header.tax_year_begin.status", field(blank_result, "header.tax_year_begin")["status"],
          "blank", blank_failures)
    check("header.tax_year_end.status", field(blank_result, "header.tax_year_end")["status"],
          "blank", blank_failures)
    for key in backfilled_fields:
        check("%s.status (blank form abstention)" % key,
              field(blank_result, key)["status"], "blank", blank_failures)
    check("blank unresolved reader count",
          blank_result["status_counts"].get("unresolved", 0), 0, blank_failures)
    check("blank reader_not_implemented count",
          sum(1 for value in blank_result["fields"].values()
              if value.get("method") == "reader_not_implemented"),
          0, blank_failures)
    for f in blank_failures:
        print(f)
    print("RESULT: %s" % ("PASS" if not blank_failures else "FAIL"))
    all_pass = all_pass and not blank_failures

    # ---- Copperleaf: real filled document, exact values confirmed by hand ----
    cl_result = run_face_reader(COPPERLEAF_PDF)
    print("=" * 100)
    print("CASE: Copperleaf real preparer doc (%s)" % COPPERLEAF_PDF.name)
    cl_failures = []
    check("item_g.value", field(cl_result, "item_g")["normalized_value"],
          "limited_or_other_member", cl_failures)
    check("item_h1.value", field(cl_result, "item_h1")["normalized_value"],
          "domestic", cl_failures)
    check("item_m.value", field(cl_result, "item_m")["normalized_value"], "no", cl_failures)
    check("item_a.value", field(cl_result, "item_a")["normalized_value"],
          "**-**65189", cl_failures)
    check("item_b.value", field(cl_result, "item_b")["normalized_value"],
          "COPPERLEAF REAL ESTATE FUND V, L.P.\n"
          "5255 LAKE CIR\nST LOUIS, MO 63150", cl_failures)
    check("item_c.status", field(cl_result, "item_c")["status"],
          "blank", cl_failures)
    check("item_e.value", field(cl_result, "item_e")["normalized_value"],
          "XX-XXX8572", cl_failures)
    check("item_f.value", field(cl_result, "item_f")["normalized_value"],
          "MERIDIAN REAL ASSETS AGGREGATOR, L.P.\n"
          "5981 JUNIPER RD\nROCHESTER, NY 14633", cl_failures)
    check("item_h2.status", field(cl_result, "item_h2")["status"],
          "blank", cl_failures)
    check("item_i1.raw_text", field(cl_result, "item_i1")["raw_text"],
          "PARTNERSHIP (LIMITED)", cl_failures)
    check("item_i1.value", field(cl_result, "item_i1")["normalized_value"],
          "partnership", cl_failures)
    check("item_i1.method", field(cl_result, "item_i1")["method"],
          "overlay_font_bounded_text+value_alias", cl_failures)
    check("box_11 coded rows", coded_signature(cl_result, "box_11"),
          [("A", 600700.0, False), ("*", None, True)], cl_failures)
    check("box_13 coded rows", coded_signature(cl_result, "box_13"),
          [
              ("A", 29400.0, False),
              ("B", 56300.0, False),
              ("*", None, True),
          ], cl_failures)
    check("box_14 coded rows", coded_signature(cl_result, "box_14"),
          [("A", 104600.0, False), ("*", None, True)], cl_failures)
    check("box_15 coded rows", coded_signature(cl_result, "box_15"),
          [("A", 1800.0, False), ("*", None, True)], cl_failures)
    check("box_17 coded rows", coded_signature(cl_result, "box_17"),
          [
              ("A", 79000.0, False),
              ("B", 17200.0, False),
              ("*", None, True),
          ], cl_failures)
    check("box_18 coded rows", coded_signature(cl_result, "box_18"),
          [("A", 118800.0, False), ("C", 41200.0, False)],
          cl_failures)
    check("box_19 coded rows", coded_signature(cl_result, "box_19"),
          [("A", 27000.0, False)], cl_failures)
    check("box_20 coded rows", coded_signature(cl_result, "box_20"),
          [
              ("A", 2317700.0, False),
              ("B", -112600.0, False),
              ("C", 104300.0, False),
              ("*", None, True),
          ], cl_failures)
    check("synthetic unresolved reader count",
          cl_result["status_counts"].get("unresolved", 0), 0, cl_failures)
    check("synthetic reader_not_implemented count",
          sum(1 for value in cl_result["fields"].values()
              if value.get("method") == "reader_not_implemented"),
          0, cl_failures)
    check("backfilled present fields have bounding boxes",
          all(
              field(cl_result, key).get("bbox")
              for key in backfilled_fields
              if field(cl_result, key)["status"] == "present"
          ),
          True, cl_failures)
    check("box_1.value", field(cl_result, "box_1")["normalized_value"], 556000.0, cl_failures)
    check("box_2.value", field(cl_result, "box_2")["normalized_value"], 28800.0, cl_failures)
    # CORRECTED 2026-07-29. This assertion previously expected 17200.0 -- the
    # value the reader emitted when D2 (unbounded next-line search window) was
    # live. box 17 code B is 17,200; the source's box 5 is 434,000. The
    # expectation had been captured from reader OUTPUT rather than from the
    # source document, so the test canonized the defect as a requirement and
    # would have actively resisted the fix. Every expectation below is now
    # transcribed from the printed page.
    #
    # Boxes 8, 9a, 9b, 9c, 10 were never asserted at all -- which is why five
    # wrong values survived a green suite. All five were confirmed wrong by
    # D2's path attribution and are now verified correct.
    check("box_5.value", field(cl_result, "box_5")["normalized_value"], 434000.0, cl_failures)
    check("box_8.value (D2 regression guard: was box 19's label number '19')",
          field(cl_result, "box_8")["normalized_value"], 94000.0, cl_failures)
    check("box_9a.value", field(cl_result, "box_9a")["normalized_value"], 897700.0, cl_failures)
    check("box_9b.value (D2 regression guard: was box 20's label number '20')",
          field(cl_result, "box_9b")["normalized_value"], 394600.0, cl_failures)
    check("box_9c.value (D2 regression guard: was box 20 code A's 2,317,700)",
          field(cl_result, "box_9c")["normalized_value"], 95300.0, cl_failures)
    check("box_10.value (D2 regression guard: was box 20 code B's -112,600)",
          field(cl_result, "box_10")["normalized_value"], 16100.0, cl_failures)
    check("box_12.value", field(cl_result, "box_12")["normalized_value"], 75000.0, cl_failures)
    check("box_21.value", field(cl_result, "box_21")["normalized_value"], 267500.0, cl_failures)
    check("box_4c.status (genuine blank on this doc too)",
          field(cl_result, "box_4c")["status"], "blank", cl_failures)
    check("header.tax_year_begin.value",
          field(cl_result, "header.tax_year_begin")["normalized_value"], "1/1/2025", cl_failures)
    check("header.tax_year_end.value",
          field(cl_result, "header.tax_year_end")["normalized_value"], "12/31/2025", cl_failures)

    item_j = field(cl_result, "item_j")["normalized_value"]
    check("item_j.profit.beginning", item_j["profit"]["beginning"]["value"], 100.0, cl_failures)
    check("item_j.loss.ending (regression guard: was fabricating box_11's '11')",
          item_j["loss"]["ending"]["value"], 100.0, cl_failures)
    check("item_j.capital.ending", item_j["capital"]["ending"]["value"], 100.0, cl_failures)

    item_k1 = field(cl_result, "item_k1")["normalized_value"]
    check("item_k1.nonrecourse.beginning", item_k1["nonrecourse"]["beginning"]["value"],
          39700.0, cl_failures)
    check("item_k1.nonrecourse.ending (regression guard: was fabricating box_13's '13')",
          item_k1["nonrecourse"]["ending"]["value"], 51600.0, cl_failures)

    # D3 REGRESSION GUARD, added 2026-07-29. This row was previously the ONLY
    # item_k1 row with no assertion -- and it was also the only one that was
    # broken. Its label wraps across two baselines:
    #     Qualified nonrecourse
    #     financing . . . $ 10,000 $ 52,600
    # so no single row band contained the declared anchor "Qualified nonrecourse
    # financing", the matcher returned None, and a real 10,000 / 52,600 pair was
    # silently discarded as row_anchor_not_found. Its NEIGHBOURS resolved fine,
    # which is exactly why the loss was invisible in aggregate and why the gap in
    # coverage mattered: an untested row adjacent to tested ones looks healthy.
    check("item_k1.qualified_nonrecourse_financing.beginning "
          "(D3 guard: wrapped label, was row_anchor_not_found)",
          item_k1["qualified_nonrecourse_financing"]["beginning"]["value"],
          10000.0, cl_failures)
    check("item_k1.qualified_nonrecourse_financing.ending "
          "(D3 guard: wrapped label, was row_anchor_not_found)",
          item_k1["qualified_nonrecourse_financing"]["ending"]["value"],
          52600.0, cl_failures)

    check("item_k1.recourse.beginning", item_k1["recourse"]["beginning"]["value"], 80000.0, cl_failures)
    check("item_k1.recourse.ending", item_k1["recourse"]["ending"]["value"], 5100.0, cl_failures)

    item_l = field(cl_result, "item_l")["normalized_value"]
    check("item_l.beginning", item_l["beginning"]["value"], 113000.0, cl_failures)
    check("item_l.contributions", item_l["contributions"]["value"], 168000.0, cl_failures)
    check("item_l.current_year_net_income_loss", item_l["current_year_net_income_loss"]["value"],
          7206200.0, cl_failures)
    check("item_l.other_increase_decrease (regression guard: was fabricating a copy of the row above)",
          item_l["other_increase_decrease"]["status"], "blank", cl_failures)
    # CORRECTED 2026-07-29. Previously expected +27000.0 -- the value the
    # reader emitted while D15 was live. The source prints
    #     Withdrawals and distributions . . . $( 27,000 )
    # and parentheses are accounting notation for a negative. The bug was that
    # text extraction split the parens into separate word tokens ('$(' ,
    # '27,000' , ')') so parse_numeric never saw them.
    #
    # Note the assertion immediately below already documented withdrawals as a
    # SUBTRACTION ("...+7206200-27000") while this line asserted it positive.
    # The disproof was sitting in the neighbouring assertion's own label.
    check("item_l.withdrawals_distributions (D15 regression guard: split-paren negative)",
          item_l["withdrawals_distributions"]["value"], -27000.0, cl_failures)
    check("item_l.ending (arithmetic cross-check: 113000+168000+7206200-27000)",
          item_l["ending"]["value"], 7460200.0, cl_failures)


    # ---- Item L continuity, computed FROM READER OUTPUT ----------------------
    # Every assertion above compares reader output to a number a human
    # transcribed. That is necessary but not sufficient: two of those
    # hand-written expectations were themselves captured from buggy output and
    # passed green for a full session (box_5 = 17,200 under D2, withdrawals =
    # +27,000 under D15).
    #
    # This check is different in kind. It takes ONLY reader output and tests it
    # against an identity the tax form itself imposes:
    #     beginning + contributions + current-year + other +/- withdrawals == ending
    # No transcription is involved, so it cannot inherit a transcription error.
    # It would have failed the instant D15 appeared (7,514,200 vs a printed
    # 7,460,200) with no prior knowledge of the correct sign.
    _l_components = ("beginning", "contributions", "current_year_net_income_loss",
                     "other_increase_decrease", "withdrawals_distributions")
    _l_sum = 0.0
    for _k in _l_components:
        _v = (item_l.get(_k) or {}).get("value")
        if isinstance(_v, (int, float)):
            _l_sum += float(_v)
    _l_end = (item_l.get("ending") or {}).get("value")
    check("item_l CONTINUITY %s == %s (self-validating from reader output)"
          % (_l_sum, _l_end),
          isinstance(_l_end, (int, float)) and abs(_l_sum - float(_l_end)) < 0.01,
          True, cl_failures)

    item_n = field(cl_result, "item_n")["normalized_value"]
    check("item_n.beginning", item_n["beginning"]["value"], 30900.0, cl_failures)
    check("item_n.ending", item_n["ending"]["value"], 11800.0, cl_failures)

    for f in cl_failures:
        print(f)
    print("RESULT: %s" % ("PASS" if not cl_failures else "FAIL"))
    all_pass = all_pass and not cl_failures

    print("=" * 100)
    print("OVERALL: %s" % ("ALL PASS" if all_pass else "SOME FAILED"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
