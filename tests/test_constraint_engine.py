#!/usr/bin/env python
"""Constraint engine regression tests.

Every case here reproduces a defect found by adversarial review of the
taxonomy-driven constraint engine. Each was demonstrated failing before its
fix; this file exists so none can regress silently.

Coverage map (third adversarial review findings):
  F1  cross-path range rules failed open       -> range_* cases
  F2  bare-path sum targets were inert         -> sum_* cases
  F3  null/absent algebra (parser-spec 4.7)    -> algebra_* cases
  F4  duplicate coded entries were invisible   -> duplicate_* cases
  F5  missing `semantic` bypassed required_field -> required_field_* cases

The algebra_null_* pair exists to make an otherwise unobservable branch
observable. A present-but-null operand and an absent operand are handled
differently by 4.7 -- null is arithmetically 0.00, absent skips the
constraint -- and a passing document alone cannot distinguish which branch
ran. Comparing a violating value against a null operand separates them: it
must FAIL under null-coercion and would silently pass under a skip.

Exit code 0 = every case behaved as expected. Exit code 1 = at least one
diverged.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills/k1-otd/scripts"))

from constraint_engine import (  # noqa: E402
    run_constraints,
    validate_coded_entry_uniqueness,
)


# ---------------------------------------------------------------------------
# Minimal node builders -- structurally valid TaxNodes, nothing more
# ---------------------------------------------------------------------------

def scalar(sem_id, value):
    return {"type": "scalar",
            "semantic": {"id": sem_id, "label": sem_id},
            "form": {"form_id": "k1-1065", "location": "Part III"},
            "value": value}


def coded(sem_id, entries):
    return {"type": "coded",
            "semantic": {"id": sem_id, "label": sem_id},
            "form": {"form_id": "k1-1065", "location": "Part III"},
            "entries": entries}


# ---------------------------------------------------------------------------
# Constraint declarations mirroring the shipped taxonomy's own rules
# ---------------------------------------------------------------------------

SUM_RULE = [{"id": "box_4c_equals_4a_plus_4b",
             "type": "sum",
             "target": "part_iii.box_4c",
             "operands": ["part_iii.box_4a", "part_iii.box_4b"],
             "tolerance": 0.01,
             "severity": "error"}]

RANGE_RULE = [{"id": "box_6b_lte_6a",
               "type": "range",
               "target": "part_iii.box_6b",
               "rule": "value <= part_iii.box_6a.value",
               "severity": "error"}]

ZZ_RULE = [{"id": "box_20_zz_requires_classification",
            "type": "required_field",
            "target": "part_iii.box_20.ZZ.semantic.classification",
            "severity": "error"}]


def constraints_case(body, constraints):
    return lambda: run_constraints(body, constraints)


def uniqueness_case(body):
    return lambda: validate_coded_entry_uniqueness(body)


# ---------------------------------------------------------------------------
# Cases: (label, callable -> findings, expect_findings)
# ---------------------------------------------------------------------------

CASES = [
    # -- F1: cross-path range rules must actually evaluate ------------------
    ("range_violation_fails",
     constraints_case(
         {"part_iii": {"box_6a": scalar("ordinary_dividends", 100.0),
                       "box_6b": scalar("qualified_dividends", 500.0)}},
         RANGE_RULE),
     True),

    ("range_compliant_passes",
     constraints_case(
         {"part_iii": {"box_6a": scalar("ordinary_dividends", 500.0),
                       "box_6b": scalar("qualified_dividends", 100.0)}},
         RANGE_RULE),
     False),

    # -- F2: bare-path sum targets must resolve through the TaxNode ---------
    ("sum_mismatch_fails",
     constraints_case(
         {"part_iii": {"box_4a": scalar("gp_services", 100.0),
                       "box_4b": scalar("gp_capital", 100.0),
                       "box_4c": scalar("gp_total", 999.0)}},
         SUM_RULE),
     True),

    ("sum_correct_passes",
     constraints_case(
         {"part_iii": {"box_4a": scalar("gp_services", 100.0),
                       "box_4b": scalar("gp_capital", 100.0),
                       "box_4c": scalar("gp_total", 200.0)}},
         SUM_RULE),
     False),

    # -- F3: parser-spec 4.7 null/absent algebra ----------------------------
    ("algebra_null_sum_target_is_zero",
     constraints_case(
         {"part_iii": {"box_4a": scalar("gp_services", 100.0),
                       "box_4b": scalar("gp_capital", 100.0),
                       "box_4c": scalar("gp_total", None)}},
         SUM_RULE),
     True),

    ("algebra_absent_sum_operand_is_zero",
     constraints_case(
         {"part_iii": {"box_4a": scalar("gp_services", 100.0),
                       "box_4c": scalar("gp_total", 200.0)}},
         SUM_RULE),
     True),

    ("algebra_absent_range_target_is_skipped",
     constraints_case(
         {"part_iii": {"box_6a": scalar("ordinary_dividends", 100.0)}},
         RANGE_RULE),
     False),

    # -- F3 discriminator: null operand is coerced, NOT skipped -------------
    # If a null referenced path were skipped rather than coerced to 0.00,
    # this violating comparison would silently pass.
    ("algebra_null_range_operand_coerced_not_skipped",
     constraints_case(
         {"part_iii": {"box_6a": scalar("ordinary_dividends", None),
                       "box_6b": scalar("qualified_dividends", 500.0)}},
         RANGE_RULE),
     True),

    # The regression fixtures' actual shape: both sides effectively zero.
    # Passes on the arithmetic (0.0 <= 0.0), not because the rule vanished.
    ("algebra_null_operand_zero_comparison_passes",
     constraints_case(
         {"part_iii": {"box_6a": scalar("ordinary_dividends", None),
                       "box_6b": scalar("qualified_dividends", 0.0)}},
         RANGE_RULE),
     False),

    # -- F4: duplicate coded entries are malformed on the form's face -------
    ("duplicate_coded_entry_rejected",
     uniqueness_case(
         {"part_iii": {"box_20": coded("other_information", [
             {"code": "X", "value": 500.0,
              "statement": {"classification": "payment_obligation"}},
             {"code": "X", "value": 999.0},
         ])}}),
     True),

    ("unique_coded_entries_accepted",
     uniqueness_case(
         {"part_iii": {"box_20": coded("other_information", [
             {"code": "A", "value": 500.0},
             {"code": "X", "value": 999.0,
              "statement": {"classification": "payment_obligation"}},
         ])}}),
     False),

    # -- F5: a missing `semantic` dict must not bypass required_field -------
    ("required_field_missing_semantic_still_enforced",
     constraints_case(
         {"part_iii": {"box_20": coded("other_information", [
             {"code": "ZZ", "value": 100.0},
         ])}},
         ZZ_RULE),
     True),

    ("required_field_present_classification_passes",
     constraints_case(
         {"part_iii": {"box_20": coded("other_information", [
             {"code": "ZZ", "value": 100.0,
              "semantic": {"id": "other_information.zz",
                           "classification": "section_199a_detail"}},
         ])}},
         ZZ_RULE),
     False),
]


def main() -> int:
    print("=== OTD Constraint Engine Regression Suite ===")
    failures = []
    for label, run, expect_findings in CASES:
        try:
            findings = run()
        except Exception as exc:  # an engine crash is itself a failure
            print(f"  MISS [{label}] engine raised {exc!r}")
            failures.append(label)
            continue
        got = bool(findings)
        detail = findings[0][2] if findings else "(no findings)"
        if got != expect_findings:
            failures.append(label)
            verdict = "MISS"
            expectation = "expected findings" if expect_findings else "expected none"
            print(f"  {verdict} [{label}] {expectation}\n         -> {detail}")
        else:
            print(f"  OK   [{label}]\n         -> {detail}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} cases behaved as expected.")
    if failures:
        print("=== FAILURES ===")
        for f in failures:
            print(f"  {f}")
        return 1
    print("ALL CONSTRAINT ENGINE TESTS PASSED SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
